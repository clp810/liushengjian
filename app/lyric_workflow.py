"""Lyric drafts and cancellable local alignment; apply only after user review."""
import json
import math
import os
from pathlib import Path
import shutil
import signal
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .lyric_sources import LyricSources, LookupError
from .store import now
from .translations import attach_by_text

PROJECT = Path(__file__).resolve().parents[1]


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    query: str = Field(min_length=1, max_length=200)
    provider: Literal['netease', 'qq', 'lrclib'] = 'netease'


class TranslationPair(BaseModel):
    model_config = ConfigDict(extra='forbid')
    text: str = Field(min_length=1, max_length=500)
    translation: str = Field(default='', max_length=1000)


class AlignmentRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    text: str = Field(min_length=1, max_length=16000)
    language: Literal['zh', 'en', 'ja', 'ko', 'fr', 'es', 'de', 'it', 'pt'] = 'zh'
    scope: Literal['source', 'clip'] = 'source'
    source: str = Field(default='手动输入', max_length=300)
    translations: list[TranslationPair] = Field(default_factory=list, max_length=500)


class ReviewLine(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    text: str = Field(min_length=1, max_length=500)
    translation: str = Field(default='', max_length=1000)


class ApplyRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    lines: list[ReviewLine] = Field(min_length=1, max_length=500)


class TranslationUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: str = Field(max_length=64)
    translations: list[str] = Field(max_length=1500)


class TranslationMatch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: str = Field(max_length=64)
    token: str = Field(max_length=64)


def validate_lines(lines, duration):
    if sum(len(line.get('translation','')) for line in lines)>50000:
        raise ValueError('中文译文总计最多 5 万字。')
    previous = -1
    for line in lines:
        if not line['text'].strip() or not math.isfinite(line['start']) or not math.isfinite(line['end']):
            raise ValueError('每句都需要文字和有效时间。')
        if not 0 <= line['start'] < line['end'] <= duration + .05 or line['start'] < previous:
            raise ValueError('请检查标记的句子：开始须早于结束，句子按时间排列且不能重叠或超过音频。')
        previous = line['end']


def preview_lines(segments, duration, offset=0):
    """Preserve uncertain rows for human review rather than inventing missing timestamps."""
    rows = []
    previous = 0
    for segment in segments:
        start, end = float(segment['start']), float(segment['end'])
        if not math.isfinite(start) or not math.isfinite(end):
            raise ValueError('模型返回了无效时间，请重试。')
        start, end = max(0, min(duration, start)), max(0, min(duration, end))
        confidence = segment.get('confidence', 0)
        reasons = []
        if end-start < .15: reasons.append('未定位或时长过短')
        if start < previous: reasons.append('与上一句重叠')
        if confidence < .45: reasons.append('模型把握较低')
        if end-start > 25: reasons.append('时间跨度较大')
        rows.append(dict(text=segment['text'], start=round(start+offset,2), end=round(end+offset,2),
                         words=[], review='；'.join(reasons), confidence=confidence))
        previous = end
    if not rows:
        raise ValueError('没有定位到可用歌词，请检查输入文字。')
    return rows


class LyricWorkflow:
    def __init__(self, store):
        self.store = store
        self.sources = LyricSources()
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='lyrics')
        self.lock = threading.RLock()
        self.cancels = {}
        self.closed = False

    def track(self, tid):
        track = self.store.get('tracks', tid)
        if not track: raise HTTPException(404, '没有找到这首收藏。')
        return track

    def task(self, tid, jid):
        job = self.store.get('lyric_jobs', jid)
        if not job or job['track_id'] != tid: raise HTTPException(404, '没有找到这次对齐。')
        return job

    def latest(self, tid):
        self.track(tid)
        return next((j for j in self.store.all('lyric_jobs') if j['track_id']==tid), None)

    def start(self, tid, body):
        track = self.track(tid)
        lines = [line.strip() for line in body.text.splitlines() if line.strip()]
        if not lines or len(lines)>500 or any(len(line)>500 for line in lines):
            raise HTTPException(422, '请按演唱顺序逐行填写歌词，最多 500 句，每句最多 500 字。')
        duration = track['source_duration'] if body.scope=='source' else track['duration']
        if not track.get('has_audio', track.get('m4a_path')) or track['status']!='ready':
            raise HTTPException(409, '请先等待音频处理完成。')
        if duration > 900:
            raise HTTPException(422, '一次对齐最多 15 分钟，请先裁剪为较短片段并选择当前片段。')
        with self.lock:
            active = [j for j in self.store.all('lyric_jobs') if j['status'] in ('queued','running')]
            if self.closed or len(active)>=3 or any(j['track_id']==tid for j in active):
                raise HTTPException(409, '已有对齐任务在处理中，请等待或取消后重试。')
            jid = uuid.uuid4().hex
            job = dict(id=jid, track_id=tid, status='queued', phase='等待本地对齐', error='', percent=None,
                       text='\n'.join(lines), language=body.language, scope=body.scope, source=body.source,
                       translations=[p.model_dump() for p in body.translations],
                       audio_revision=track['revision'], lyrics_revision=track.get('lyrics_revision',''),
                       source_duration=track['source_duration'], duration=duration,
                       offset=track['trim_start'] if body.scope=='clip' else 0,
                       created_at=now(), updated_at=now())
            self.store.put('lyric_jobs',job)
            event = self.cancels[jid] = threading.Event()
            self.pool.submit(self.run, job, event)
        return job

    def cancel(self, tid, jid):
        with self.lock:
            job = self.task(tid,jid)
            if job['status'] in ('queued','running'):
                if event := self.cancels.get(jid): event.set()
                job = self.store.patch('lyric_jobs',jid,status='cancelled',phase='已取消',error='')
        return job

    def shutdown(self):
        with self.lock:
            self.closed = True
            for event in self.cancels.values(): event.set()
        self.pool.shutdown(wait=True, cancel_futures=False)

    def command(self, args, event, log, timeout=1200, progress_path=None, jid=None):
        if event.is_set(): raise InterruptedError()
        process = subprocess.Popen(args, cwd=PROJECT, stdout=log, stderr=log, start_new_session=True)
        started, last_progress = time.monotonic(), ''
        try:
            while process.poll() is None:
                if event.wait(.5): raise InterruptedError()
                if time.monotonic()-started > timeout: raise TimeoutError('处理超时，请用更短的音频片段重试。')
                if progress_path and progress_path.exists():
                    try:
                        raw = progress_path.read_text()
                        if raw != last_progress:
                            update = json.loads(raw)
                            self.store.patch('lyric_jobs',jid,phase=update['phase'],percent=update.get('percent'))
                            last_progress = raw
                    except (OSError, ValueError): pass
            if process.returncode:
                raise ValueError('本地处理失败，可能是组件／模型下载失败或音频无法对齐。可重试，详细记录保留在本次任务目录。')
        finally:
            if process.poll() is None:
                try: os.killpg(process.pid,signal.SIGTERM)
                except ProcessLookupError: pass
                try: process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    try: os.killpg(process.pid,signal.SIGKILL)
                    except ProcessLookupError: pass
                    process.wait()

    def run(self, job, event):
        jid=job['id']
        directory=self.store.path(f"tracks/{job['track_id']}/lyric-tasks/{jid}")
        directory.mkdir(parents=True,exist_ok=True)
        try:
            if event.is_set(): raise InterruptedError()
            self.store.patch('lyric_jobs',jid,status='running',phase='准备本地 AI 组件（首次需要联网）')
            with (directory/'process.log').open('w') as log:
                uv=shutil.which('uv')
                if not uv: raise ValueError('没有找到 uv，请按 README 安装后重启。')
                self.command([uv,'sync','--project',str(PROJECT/'alignment'),'--frozen','--python','3.12'],event,log,timeout=600)
                track=self.track(job['track_id'])
                if track['revision'] != job['audio_revision']: raise ValueError('音频版本已经变化，请重新开始对齐。')
                path=self.store.path(track['preview_path'] if job['scope']=='source' else track['m4a_path'])
                wav=directory/'analysis.wav'
                self.store.patch('lyric_jobs',jid,phase='准备分析音轨，原文件保持不变')
                self.command(['ffmpeg','-v','error','-nostdin','-y','-protocol_whitelist','file,pipe','-i',str(path),'-vn','-ac','1','-ar','16000',str(wav)],event,log,timeout=180)
                progress_path=directory/'progress.json'
                request=dict(audio_path=str(wav),text=job['text'],language=job['language'],model_dir=str(PROJECT/'.cache/lyric-models'),progress_path=str(progress_path),output_path=str(directory/'result.json'))
                request_path=directory/'request.json';request_path.write_text(json.dumps(request,ensure_ascii=False))
                self.command([str(PROJECT/'alignment/.venv/bin/python'),str(PROJECT/'app/alignment_worker.py'),str(request_path)],event,log,progress_path=progress_path,jid=jid)
                segments=json.loads((directory/'result.json').read_text())
                rows=preview_lines(segments,job['duration'],job['offset'])
                attach_by_text(rows, job.get('translations', []))
                with self.lock:
                    if event.is_set(): raise InterruptedError()
                    self.store.patch('lyric_jobs',jid,status='done',phase='对齐完成，请试听并确认',percent=100,lines=rows)
        except InterruptedError:
            self.store.patch('lyric_jobs',jid,status='cancelled',phase='已取消，原歌词保持不变')
        except Exception as error:
            self.store.patch('lyric_jobs',jid,status='error',phase='对齐未完成',error=str(error) if isinstance(error,(ValueError,TimeoutError)) else '处理失败，请重试；原歌词保持不变。')
        finally:
            with self.lock: self.cancels.pop(jid,None)

    def apply(self, tid, jid, body):
        with self.store.lock:
            job=self.task(tid,jid);track=self.track(tid)
            if job['status']!='done': raise HTTPException(409,'请先完成对齐并预览。')
            if track['revision']!=job['audio_revision'] or track.get('lyrics_revision','')!=job['lyrics_revision']:
                raise HTTPException(409,'音频或已保存歌词在处理期间发生了变化，请重新对齐，避免覆盖新内容。')
            rows=[line.model_dump() for line in body.lines]
            try: validate_lines(rows,track['source_duration'])
            except ValueError as error: raise HTTPException(422,str(error)) from error
            for row in rows: row['words']=[]
            revision=uuid.uuid4().hex
            path=self.store.path(f'tracks/{tid}/lyrics-{revision}.json')
            document=dict(lines=rows,timed_words=False,file_offset=0,alignment='stable-ts / Whisper small',source=job['source'])
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text(json.dumps(document,ensure_ascii=False,indent=2))
            settings={**track.get('lyric_settings',dict(mood='auto')), 'clock':'source','offset':0}
            self.store.patch('tracks',tid,lyrics=document,lyrics_path=self.store.relative(path),lyrics_filename='现场对齐 · '+job['source'],lyrics_revision=revision,lyric_settings=settings)
            self.store.patch('lyric_jobs',jid,status='applied',phase='已保存到这份收藏')
            return dict(ok=True)

    def save_translations(self, tid, revision, translations, source='手动编辑'):
        with self.store.lock:
            track = self.track(tid)
            if track.get('lyrics_revision', '') != revision:
                raise HTTPException(409, '歌词已发生变化，请重新打开管理窗口，避免覆盖新内容。')
            document = track.get('lyrics')
            if not document or len(document['lines']) != len(translations):
                raise HTTPException(422, '请先保存原文歌词，再逐句补充中文。')
            if any(len(t)>1000 for t in translations) or sum(map(len,translations))>50000:
                raise HTTPException(422, '中文每句最多 1000 字，总计最多 5 万字。')
            for row, translated in zip(document['lines'], translations):
                row['translation'] = translated.strip()
            document['translation_source'] = source
            revision = uuid.uuid4().hex
            path = self.store.path(f'tracks/{tid}/lyrics-{revision}.json')
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(document, ensure_ascii=False, indent=2))
            self.store.patch('tracks',tid,lyrics=document,lyrics_revision=revision,lyrics_path=self.store.relative(path))
            return dict(ok=True, revision=revision)


def install_workflow(app, store):
    workflow=LyricWorkflow(store)
    app.state.lyric_workflow=workflow

    @app.post('/api/tracks/{tid}/lyrics/search')
    def search(tid: str, body: SearchRequest):
        track=workflow.track(tid)
        if not body.query.strip(): raise HTTPException(422,'请填写歌名或歌手。')
        try: return dict(items=workflow.sources.search(body.provider,body.query.strip(),track))
        except LookupError as error: raise HTTPException(502,str(error)) from error

    @app.get('/api/lyrics/candidates/{token}')
    def candidate(token: str):
        try: return workflow.sources.fetch(token)
        except LookupError as error: raise HTTPException(502,str(error)) from error

    @app.get('/api/tracks/{tid}/lyrics/alignment')
    def latest(tid: str):
        return dict(task=workflow.latest(tid),model_cached=(PROJECT/'.cache/lyric-models/small.pt').exists())

    @app.post('/api/tracks/{tid}/lyrics/alignment',status_code=202)
    def align(tid: str, body: AlignmentRequest): return workflow.start(tid,body)

    @app.post('/api/tracks/{tid}/lyrics/alignment/{jid}/cancel')
    def cancel(tid: str,jid: str): return workflow.cancel(tid,jid)

    @app.post('/api/tracks/{tid}/lyrics/alignment/{jid}/apply')
    def apply(tid: str,jid: str,body: ApplyRequest): return workflow.apply(tid,jid,body)

    @app.put('/api/tracks/{tid}/lyrics/translations')
    def translations(tid: str, body: TranslationUpdate):
        return workflow.save_translations(tid,body.revision,body.translations)

    @app.post('/api/tracks/{tid}/lyrics/translations/match')
    def translation_match(tid: str, body: TranslationMatch):
        track = workflow.track(tid)
        try: candidate = workflow.sources.fetch(body.token)
        except LookupError as error: raise HTTPException(502,str(error)) from error
        rows = track.get('lyrics',{}).get('lines',[])
        matched = attach_by_text(rows,candidate['translations'])
        if not matched:
            raise HTTPException(422,'没有找到文字对应的中文译文，请换一个候选，或在管理歌词中手动填写。')
        result = workflow.save_translations(tid,body.revision,[r.get('translation','') for r in rows],candidate['source'])
        return {**result, 'matched': matched}

    return workflow
