import hashlib
import io
import json
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.main import create_app
from app.media import canonical_url, probe
from app.store import Store


@pytest.fixture(scope='session')
def audio_sample(tmp_path_factory):
    path = tmp_path_factory.mktemp('audio') / 'test.m4a'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','sine=frequency=440:duration=3.2','-c:a','aac',str(path)],check=True)
    return path


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / 'library', seed=False)) as client:
        yield client


def wait_for_job(client, job_id):
    for _ in range(300):
        job = next(j for j in client.get('/api/jobs').json() if j['id'] == job_id)
        if job['status'] in ('done','error'):
            return job
        time.sleep(.05)
    pytest.fail('Audio task timed out')


def imported(client, audio_sample):
    result = client.post('/api/imports/file',files={'file':('test.m4a',audio_sample.read_bytes(),'audio/mp4')})
    assert result.status_code == 202
    assert wait_for_job(client,result.json()['job_id'])['status'] == 'done'
    return result.json()['track_id']


def test_bilibili_url_validation():
    assert canonical_url('BV1CFG66UELP') == 'https://www.bilibili.com/video/BV1CFG66UELP/'
    assert canonical_url('https://www.bilibili.com/video/BV1CFG66UELP/?p=2&vd_source=tracking') == 'https://www.bilibili.com/video/BV1CFG66UELP/?p=2'
    for value in ['http://127.0.0.1/secret','https://www.bilibili.com.evil.test/video/BV1CFG66UELP/','https://u:p@www.bilibili.com/video/BV1CFG66UELP/','https://www.bilibili.com/video/BV1CFG66UELP/?p=0']:
        with pytest.raises(ValueError): canonical_url(value)


def test_real_audio_import_range_and_trim(client,audio_sample,tmp_path):
    tid=imported(client,audio_sample)
    track=client.get('/api/tracks').json()[0]
    assert track['status']=='ready'
    assert track['quality_note']=='保留源音轨 · 未再次压缩'
    assert not any(k.endswith('_path') for k in track)
    response=client.get(f'/api/tracks/{tid}/audio',headers={'Range':'bytes=0-99'})
    assert response.status_code==206 and len(response.content)==100
    original=client.get(f'/api/tracks/{tid}/audio?kind=source')
    assert original.status_code==200
    body=dict(title='测试曲目',artist='测试演唱者',album='版本集',version='片段',note='个人笔记',trim_start=.5,trim_end=2.1)
    edit=client.put(f'/api/tracks/{tid}',json=body)
    assert edit.status_code==202
    assert wait_for_job(client,edit.json()['id'])['status']=='done'
    for ext in ('m4a','mp3'):
        response=client.get(f'/api/tracks/{tid}/audio?kind={ext}&download=true')
        assert response.status_code==200 and 'attachment' in response.headers['content-disposition']
        path=tmp_path/f'out.{ext}'; path.write_bytes(response.content)
        info=probe(path)
        assert abs(info['duration']-1.6)<.08
        assert info['tags']['title']=='测试曲目（片段）'
        assert info['tags']['artist']=='测试演唱者'
        subprocess.run(['ffmpeg','-v','error','-xerror','-i',str(path),'-f','null','-'],check=True)
    # Preview remains the full original even after trimming.
    preview=tmp_path/'preview.m4a'; preview.write_bytes(client.get(f'/api/tracks/{tid}/audio?kind=source').content)
    assert abs(probe(preview)['duration']-3.2)<.1
    assert client.put(f'/api/tracks/{tid}',json={**body,'trim_end':50}).status_code==422
    assert client.put(f'/api/tracks/{tid}',json={**body,'trim_start':2,'trim_end':1}).status_code==422
    assert client.put(f'/api/tracks/{tid}',json={**body,'title':'   '}).status_code==422


def test_full_m4a_preserves_audio_packets(client,audio_sample,tmp_path):
    tid=imported(client,audio_sample)
    output=tmp_path/'output.m4a';output.write_bytes(client.get(f'/api/tracks/{tid}/audio').content)
    def digest(path):
        return subprocess.check_output(['ffmpeg','-v','error','-i',str(path),'-map','0:a:0','-c:a','copy','-f','hash','-hash','sha256','-'])
    assert digest(audio_sample)==digest(output)


def test_cover_favorites_archive_and_persistence(client,audio_sample):
    tid=imported(client,audio_sample)
    picture=io.BytesIO();Image.new('RGB',(150,100),'#aabbcc').save(picture,'PNG')
    assert client.post(f'/api/tracks/{tid}/cover',files={'file':('cover.png',picture.getvalue(),'image/png')}).status_code==200
    assert client.get(f'/api/tracks/{tid}/cover').headers['content-type']=='image/jpeg'
    assert client.post(f'/api/tracks/{tid}/cover',files={'file':('bad.png',b'not an image')}).status_code==422
    assert client.patch(f'/api/tracks/{tid}',json={'favorite':True,'archived':True}).status_code==200
    stored=Store(client.app.state.store.root).get('tracks',tid)
    assert stored['favorite'] and stored['archived']
    assert client.patch(f'/api/tracks/{tid}',json={'archived':False}).json()['archived'] is False


def test_invalid_media_and_retry_are_explicit(client):
    assert client.post('/api/imports/file',files={'file':('bad.txt',b'hello')}).status_code==422
    result=client.post('/api/imports/file',files={'file':('bad.mp3',b'hello')}).json()
    assert wait_for_job(client,result['job_id'])['status']=='error'
    retry=client.post(f"/api/tracks/{result['track_id']}/retry").json()
    assert wait_for_job(client,retry['id'])['status']=='error'
    assert len(client.get('/api/tracks').json())==1


def test_local_access_boundary(client):
    assert client.get('/api/status',headers={'Host':'evil.example'}).status_code==403
    assert client.post('/api/imports/url',headers={'Origin':'https://evil.example'},json={'url':'BV1CFG66UELP'}).status_code==403
    assert client.post('/api/imports/url',headers={'Sec-Fetch-Site':'cross-site'},json={'url':'BV1CFG66UELP'}).status_code==403
    assert client.post('/api/imports/url',json={'url':'https://localhost/secret'}).status_code==422
    assert client.get('/api/tracks/nonexistent/audio').status_code==404
    assert client.get('/api/status').json()['cloud_mode']=='manual'


def test_interrupted_task_recovery(tmp_path):
    store=Store(tmp_path)
    track=store.new_track(title='Interrupted')
    job=store.new_job(track['id'],'import')
    with TestClient(create_app(tmp_path,seed=False)) as client:
        assert client.get('/api/tracks').json()[0]['status']=='error'
        assert client.get('/api/jobs').json()[0]['status']=='error'
        assert client.get('/api/jobs').json()[0]['error']
