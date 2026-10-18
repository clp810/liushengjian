"""Automatic preparation: discover, verify, align, save; uncertainty never becomes a repair form."""
import json
from pathlib import Path
import shutil
import threading
import uuid

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .auto_quality import acceptable
from .lyric_sources import LookupError
from .song_identity import choose, identify, language, normalized
from .store import now
from .translations import attach_by_text

PROJECT=Path(__file__).resolve().parents[1]
ACTIVE=('queued','running')


class AutoRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    retry: bool=False
    query: str=Field(default='',max_length=200)


class AutomaticLyrics:
    def __init__(self,workflow):
        self.workflow=workflow
        self.store=workflow.store

    def latest(self,tid):
        self.workflow.track(tid)
        return next((j for j in self.store.all('lyric_jobs') if j['track_id']==tid and j.get('kind')=='automatic'),None)

    def public(self,job):
        if not job:return dict(status='idle',message='歌词会在收听时自动准备。')
        status=job['status']
        messages={'queued':'正在排队准备歌词…','running':'正在聆听这场演唱，准备同步歌词…',
                  'ready':'歌词已准备好。','text_only':'歌词已找到，暂时无法可靠同步这场现场。',
                  'not_found':'暂时没有确认这首歌。可以稍后重试，或告诉我更准确的歌名。',
                  'unavailable':'歌词服务暂时没有回应，稍后可重试。',
                  'cancelled':'已停止准备，原有歌词保留。','error':'这次未能完成准备，原有歌词保留。',
                  'conflict':'收藏已更新，已保留你的新修改。'}
        message=messages.get(status,'歌词准备尚未完成。')
        if status=='running' and job.get('stage')=='search':message='正在识别歌名，寻找歌词与中文…'
        if job.get('kept_existing') and status=='ready':message='已保留原有歌词时间，并检查了对应中文。'
        return dict(id=job['id'],status=status,message=message,song=job.get('song'),
                    translated=sum(bool(r.get('translation')) for r in job.get('plain_lines',[])),
                    plain_lines=job.get('plain_lines',[]) if status not in ACTIVE else [],
                    strategy=job.get('strategy',''),has_saved=job.get('saved',False))

    def start(self,tid,body):
        w=self.workflow;track=w.track(tid)
        if track['status']!='ready' or not track.get('m4a_path'):raise HTTPException(409,'音频准备好后会自动找歌词。')
        signature=f"{track['revision']}|{track['title']}|{track.get('artist','')}"
        with w.lock:
            previous=self.latest(tid)
            if previous and previous['status'] in ACTIVE:return self.public(previous)
            if not body.retry and previous and previous.get('signature')==signature:return self.public(previous)
            active=[j for j in self.store.all('lyric_jobs') if j['status'] in ACTIVE]
            if w.closed or len(active)>=3 or any(j['track_id']==tid for j in active):raise HTTPException(409,'已有歌词正在准备，稍后再试。')
            job=dict(id=uuid.uuid4().hex,track_id=tid,kind='automatic',status='queued',phase='准备歌词',
                     stage='search',signature=signature,audio_revision=track['revision'],
                     lyrics_revision=track.get('lyrics_revision',''),query=body.query,
                     preserve_existing=bool(track.get('lyrics',{}).get('lines')) and not body.retry,
                     created_at=now(),updated_at=now(),error='')
            self.store.put('lyric_jobs',job)
            event=w.cancels[job['id']]=threading.Event()
            w.pool.submit(self.run,job,event)
        return self.public(job)

    def check(self,job,event):
        if event.is_set():raise InterruptedError()
        track=self.workflow.track(job['track_id'])
        if track['revision']!=job['audio_revision'] or track.get('lyrics_revision','')!=job['lyrics_revision']:
            raise HTTPException(409,'收藏已更新')
        return track

    def discover(self,track,query,event):
        identity=identify(track,query)
        if not normalized(identity['title']):return None
        # Prefer song-only search; original performer metadata helps rank rather than hiding covers.
        items=[];seen=set()
        for q in [identity['title'],f"{identity['title']} {identity['artist']}".strip()]:
            if q in seen:continue
            seen.add(q)
            if event.is_set():raise InterruptedError()
            found=self.workflow.sources.search('netease',q,identity)
            items.extend(found)
            selected=choose(identity,items)
            if selected:
                result=self.workflow.sources.fetch(selected['token'])
                return selected,result
        return None

    def infer(self,job,track,text,lang,event,directory):
        w=self.workflow
        with (directory/'process.log').open('w') as log:
            uv=shutil.which('uv')
            if not uv:raise ValueError('本地组件暂不可用')
            w.command([uv,'sync','--project',str(PROJECT/'alignment'),'--frozen','--python','3.12'],event,log,timeout=600)
            wav=directory/'analysis.wav'
            w.command(['ffmpeg','-v','error','-nostdin','-y','-protocol_whitelist','file,pipe','-i',str(self.store.path(track['preview_path'])),'-vn','-ac','1','-ar','16000',str(wav)],event,log,timeout=180)
            progress=directory/'progress.json'
            request=dict(audio_path=str(wav),text=text,language=lang,automatic=True,model_dir=str(PROJECT/'.cache/lyric-models'),progress_path=str(progress),output_path=str(directory/'result.json'))
            path=directory/'request.json';path.write_text(json.dumps(request,ensure_ascii=False))
            w.command([str(PROJECT/'alignment/.venv/bin/python'),str(PROJECT/'app/alignment_worker.py'),str(path)],event,log,timeout=1800,progress_path=progress,jid=job['id'])
            return json.loads((directory/'result.json').read_text())

    def save(self,job,document,event,settings=None):
        with self.workflow.lock,self.store.lock:
            track=self.check(job,event)
            revision=uuid.uuid4().hex
            path=self.store.path(f"tracks/{track['id']}/lyrics-{revision}.json")
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text(json.dumps(document,ensure_ascii=False,indent=2))
            values=dict(lyrics=document,lyrics_path=self.store.relative(path),lyrics_revision=revision,lyrics_filename='自动准备 · 网易云歌词')
            if settings is not None:values['lyric_settings']=settings
            self.store.patch('tracks',track['id'],**values)
            self.store.patch('lyric_jobs',job['id'],saved=True,final_revision=revision)

    def complete(self,job,event,status,document=None,settings=None,**values):
        # Commit and cancellation share one lock: a cancelled task cannot publish afterwards.
        with self.workflow.lock,self.store.lock:
            self.check(job,event)
            if document is not None:self.save(job,document,event,settings)
            self.store.patch('lyric_jobs',job['id'],status=status,**values)

    def run(self,job,event):
        jid=job['id'];directory=self.store.path(f"tracks/{job['track_id']}/lyric-tasks/{jid}")
        directory.mkdir(parents=True,exist_ok=True)
        plain=[];song=None
        try:
            track=self.check(job,event)
            self.store.patch('lyric_jobs',jid,status='running',stage='search')
            found=self.discover(track,job['query'],event)
            if not found:
                self.complete(job,event,'not_found');return
            item,candidate=found
            song={k:item[k] for k in ('title','artist','album')}
            text=candidate['text'];plain=[dict(text=t,translation='') for t in text.splitlines() if t.strip()]
            attach_by_text(plain,candidate.get('translations',[]))
            self.store.patch('lyric_jobs',jid,plain_lines=plain,song=song,stage='align')
            self.check(job,event)
            if job['preserve_existing']:
                doc=track['lyrics'];rows=doc['lines']
                original=[r.get('translation','') for r in rows]
                attach_by_text(rows,candidate.get('translations',[]))
                for row,previous in zip(rows,original):
                    if previous:row['translation']=previous
                changed=[r.get('translation','') for r in rows]!=original
                self.complete(job,event,'ready',doc if changed else None,kept_existing=True);return
            strategy='text_only';rows=[]
            if 0<track['source_duration']<=900 and len(plain)<=500 and all(len(r['text'])<=500 for r in plain):
                try:
                    result=self.infer(job,track,text,language(text),event,directory)
                    rows=result['segments'];strategy=result['strategy']
                    # Worker quality is rechecked before any automatic write.
                    if not acceptable(rows,len(plain),result.get('coverage') if strategy=='transcription' else None):strategy='text_only'
                    if any(r['end']>track['source_duration']+.05 for r in rows):strategy='text_only'
                except (ValueError,TimeoutError,OSError) as error:
                    (directory/'failure.txt').write_text(str(error));strategy='text_only'
            if strategy!='text_only':
                for row in rows:
                    row['words']=[]
                    indices=row.pop('original_indices',None)
                    if indices:row['translation']=' '.join(plain[i].get('translation','') for i in indices)
                attach_by_text(rows,candidate.get('translations',[]))
                document=dict(lines=rows,timed_words=False,file_offset=0,source='网易云歌词',automatic=True,song=song,strategy=strategy)
                self.complete(job,event,'ready',document,dict(clock='source',offset=0,mood=track.get('lyric_settings',{}).get('mood','auto')),strategy=strategy)
            else:
                # Keep an existing user timeline; otherwise save plain lyrics without invented times.
                document=None if track.get('lyrics',{}).get('lines') else dict(lines=[],plain_lines=plain,timed_words=False,file_offset=0,source='网易云歌词',automatic=True,song=song,strategy='text_only')
                self.complete(job,event,'text_only',document,strategy='text_only')
        except InterruptedError:self.store.patch('lyric_jobs',jid,status='cancelled')
        except HTTPException:self.store.patch('lyric_jobs',jid,status='conflict')
        except LookupError:self.store.patch('lyric_jobs',jid,status='unavailable')
        except Exception as error:
            (directory/'failure.txt').write_text(repr(error))
            self.store.patch('lyric_jobs',jid,status='error')
        finally:
            with self.workflow.lock:self.workflow.cancels.pop(jid,None)


def install_automatic(app,workflow):
    auto=AutomaticLyrics(workflow);app.state.automatic_lyrics=auto
    @app.get('/api/tracks/{tid}/lyrics/auto')
    def status(tid:str):return auto.public(auto.latest(tid))
    @app.post('/api/tracks/{tid}/lyrics/auto',status_code=202)
    def start(tid:str,body:AutoRequest):return auto.start(tid,body)
    @app.post('/api/tracks/{tid}/lyrics/auto/{jid}/cancel')
    def cancel(tid:str,jid:str):
        job=workflow.task(tid,jid)
        if job.get('kind')!='automatic':raise HTTPException(404,'没有找到这次准备')
        return auto.public(workflow.cancel(tid,jid))
