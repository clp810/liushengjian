import json
import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from app.lyric_sources import LyricSources, LookupError
from app.lyric_workflow import preview_lines, validate_lines
from app.main import create_app
from app.store import Store, now


def fixture(tmp_path):
    store=Store(tmp_path/'library')
    tid=store.new_track(title='Test song',artist='Singer',status='ready',source_duration=30,duration=20,trim_start=10,revision='audio-1',m4a_path='test.m4a')['id']
    job=store.put('lyric_jobs',dict(id='job',track_id=tid,status='done',phase='预览',audio_revision='audio-1',lyrics_revision='',source='测试来源',source_duration=30,created_at=now(),updated_at=now(),lines=[dict(start=10,end=12,text='hello',words=[])]))
    return store,tid,job


def test_old_archived_records_restore_without_touching_files(tmp_path):
    store=Store(tmp_path/'library');path=store.path('original.m4a');path.write_bytes(b'unchanged')
    tid=store.new_track(archived=True,source_path='original.m4a',favorite=True,status='ready')['id']
    with TestClient(create_app(store.root,seed=False)) as client:
        track=client.get('/api/tracks').json()[0]
        assert track['id']==tid and track['favorite'] and not track['archived']
        assert path.read_bytes()==b'unchanged'
        assert client.patch(f'/api/tracks/{tid}',json={'archived':True}).status_code==422
    assert not Store(store.root).get('tracks',tid)['archived']


def test_provider_ranking_and_candidate_lyrics_stay_anonymous():
    calls=[]
    def respond(request):
        calls.append(request)
        assert 'cookie' not in request.headers
        if 'search/get' in request.url.path:
            return httpx.Response(200,json={'code':200,'result':{'songs':[{'id':1,'name':'Unrelated','artists':[{'name':'Someone'}],'duration':30000},{'id':2,'name':'Test song','artists':[{'name':'Singer'}],'album':{'name':'Live'},'duration':31000}]}})
        return httpx.Response(200,json={'lrc':{'lyric':'[00:01]作词 : Someone\n[00:02]Hello\n[00:05]World'}})
    sources=LyricSources(httpx.MockTransport(respond))
    result=sources.search('netease','Test song Singer',dict(title='Test song',artist='Singer',source_duration=30))
    assert result[0]['title']=='Test song' and result[0]['duration_difference']==1
    assert 'remote_id' not in result[0]
    lyric=sources.fetch(result[0]['token'])
    assert lyric['text']=='Hello\nWorld'
    assert all(r.url.host=='music.163.com' for r in calls)
    with pytest.raises(LookupError): sources.fetch('https://localhost/private')


def test_provider_errors_are_not_empty_results_and_responses_are_bounded():
    sources=LyricSources(httpx.MockTransport(lambda r:httpx.Response(200,json={'code':0,'request':{'code':2001}})))
    with pytest.raises(LookupError,match='拒绝'): sources.search('qq','song',{})
    sources=LyricSources(httpx.MockTransport(lambda r:httpx.Response(302,headers={'location':'http://127.0.0.1/secret'})))
    with pytest.raises(LookupError): sources.search('lrclib','song',{})
    sources=LyricSources(httpx.MockTransport(lambda r:httpx.Response(200,content=b'x'*(2*1024*1024+1))))
    with pytest.raises(LookupError,match='过大'): sources.search('lrclib','song',{})
    sources=LyricSources(httpx.MockTransport(lambda r:httpx.Response(200,json={'code':200,'result':{'songs':None}})))
    with pytest.raises(LookupError,match='格式'): sources.search('netease','song',{})


def test_review_flags_do_not_invent_valid_timings():
    rows=preview_lines([dict(text='missed',start=0,end=0,confidence=.1),dict(text='hello',start=2,end=4,confidence=.9)],20,10)
    assert rows[0]['start']==rows[0]['end']==10 and rows[0]['review']
    assert rows[1]['start']==12 and rows[1]['review']==''
    with pytest.raises(ValueError):validate_lines(rows,30)
    with pytest.raises(ValueError):validate_lines([dict(start=1,end=3,text='A'),dict(start=2,end=4,text='B')],30)


def test_apply_only_after_review_preserves_original_and_rejects_stale(tmp_path):
    store,tid,job=fixture(tmp_path)
    original=store.path('test.m4a');original.write_bytes(b'audio')
    url=f'/api/tracks/{tid}/lyrics/alignment/job/apply'
    with TestClient(create_app(store.root,seed=False)) as client:
        assert client.get(f'/api/tracks/{tid}/lyrics').json()['lines']==[]
        assert client.post(url,json={'lines':[dict(start=12,end=11,text='Bad')]}).status_code==422
        assert client.post(url,json={'lines':[dict(start=10,end=12,text='Hello')]}).status_code==200
        saved=client.get(f'/api/tracks/{tid}/lyrics').json()
        assert saved['settings']['clock']=='source' and saved['settings']['offset']==0
        assert saved['lines'][0]['start']==10 and saved['source']=='测试来源'
        assert original.read_bytes()==b'audio'
        assert client.post(url,json={'lines':[dict(start=10,end=12,text='again')]}).status_code==409
        store.patch('lyric_jobs','job',status='done')
        assert client.post(url,json={'lines':[dict(start=10,end=12,text='overwrite')]}).status_code==409
    with TestClient(create_app(store.root,seed=False)) as client:
        assert client.get(f'/api/tracks/{tid}/lyrics').json()['lines'][0]['text']=='Hello'


def test_job_cancel_conflicts_recovery_and_no_audio_status_change(tmp_path,monkeypatch):
    store,tid,_=fixture(tmp_path)
    with TestClient(create_app(store.root,seed=False)) as client:
        workflow=client.app.state.lyric_workflow
        def wait(job,event):
            event.wait(5)
        monkeypatch.setattr(workflow,'run',wait)
        url=f'/api/tracks/{tid}/lyrics/alignment'
        result=client.post(url,json={'text':'Hello\nWorld','scope':'clip','language':'en'})
        assert result.status_code==202
        jid=result.json()['id']
        assert result.json()['offset']==10
        assert client.post(url,json={'text':'Hello'}).status_code==409
        assert client.get('/api/tracks').json()[0]['status']=='ready'
        assert client.post(url+f'/{jid}/cancel').json()['status']=='cancelled'
        assert client.get(f'/api/tracks/{tid}/lyrics').json()['lines']==[]
        store.patch('lyric_jobs',jid,status='running')
    with TestClient(create_app(store.root,seed=False)) as client:
        assert client.get(url).json()['task']['status']=='error'
