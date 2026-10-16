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


def test_cover_favorites_and_persistence(client,audio_sample):
    tid=imported(client,audio_sample)
    picture=io.BytesIO();Image.new('RGB',(150,100),'#aabbcc').save(picture,'PNG')
    assert client.post(f'/api/tracks/{tid}/cover',files={'file':('cover.png',picture.getvalue(),'image/png')}).status_code==200
    assert client.get(f'/api/tracks/{tid}/cover').headers['content-type']=='image/jpeg'
    assert client.post(f'/api/tracks/{tid}/cover',files={'file':('bad.png',b'not an image')}).status_code==422
    assert client.patch(f'/api/tracks/{tid}',json={'favorite':True}).status_code==200
    stored=Store(client.app.state.store.root).get('tracks',tid)
    assert stored['favorite'] and not stored['archived']
    assert client.patch(f'/api/tracks/{tid}',json={'archived':True}).status_code==422


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


@pytest.fixture(scope='session')
def video_sample(tmp_path_factory):
    path = tmp_path_factory.mktemp('video') / 'live.mkv'
    # VP9/Opus exercises browser conversion, not only the MP4 copy path.
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=160x90:rate=12:duration=4',
                    '-f', 'lavfi', '-i', 'sine=frequency=440:duration=4', '-c:v', 'libvpx-vp9',
                    '-c:a', 'libopus', '-shortest', str(path)], check=True)
    return path


def test_video_import_trim_range_and_restart(client, video_sample):
    response = client.post('/api/imports/file', data={'keep_video': 'true', 'cover_style': 'dusk'},
                           files={'file': ('live.mkv', video_sample.read_bytes(), 'video/x-matroska')})
    assert response.status_code == 202
    tid = response.json()['track_id']
    assert wait_for_job(client, response.json()['job_id'])['status'] == 'done'
    track = client.get('/api/tracks').json()[0]
    assert track['has_video'] and track['cover_style'] == 'dusk'
    store = client.app.state.store
    saved = store.get('tracks', tid)
    video = store.path(saved['video_path'])
    source = store.path(saved['source_path'])
    assert source.read_bytes() == video_sample.read_bytes()
    info = probe(video)
    assert info['video']['codec_name'] == 'h264' and info['codec'] == 'aac'
    subprocess.run(['ffmpeg', '-v', 'error', '-xerror', '-i', str(video), '-f', 'null', '-'], check=True)
    before = hashlib.sha256(video.read_bytes()).hexdigest()
    ranged = client.get(f'/api/tracks/{tid}/video', headers={'Range': 'bytes=20-119'})
    assert ranged.status_code == 206 and len(ranged.content) == 100
    edit = client.put(f'/api/tracks/{tid}', json={'title': '片段现场', 'trim_start': 1, 'trim_end': 3})
    assert wait_for_job(client, edit.json()['id'])['status'] == 'done'
    updated = client.get('/api/tracks').json()[0]
    assert updated['duration'] == 2 and updated['trim_start'] == 1 and updated['has_video']
    assert hashlib.sha256(video.read_bytes()).hexdigest() == before
    assert client.post(f'/api/tracks/{tid}/video').status_code == 409
    with TestClient(create_app(store.root, seed=False)) as restarted:
        result = restarted.get('/api/tracks').json()[0]
        assert result['has_video'] and result['cover_style'] == 'dusk' and result['trim_start'] == 1
        assert restarted.get(f'/api/tracks/{tid}/video').content == video.read_bytes()


def test_prepare_video_for_existing_local_collection(client, video_sample, monkeypatch):
    response = client.post('/api/imports/file', files={'file': ('live.mkv', video_sample.read_bytes())}).json()
    assert wait_for_job(client, response['job_id'])['status'] == 'done'
    tid = response['track_id']
    assert not client.get('/api/tracks').json()[0]['has_video']
    store = client.app.state.store
    before = store.get('tracks', tid)
    from app import media
    original = media.prepare_video
    def fail(*args):
        raise media.MediaError('视频转换测试失败')
    monkeypatch.setattr(media, 'prepare_video', fail)
    job = client.post(f'/api/tracks/{tid}/video').json()
    assert wait_for_job(client, job['id'])['status'] == 'error'
    assert client.get('/api/tracks').json()[0]['has_audio']
    assert store.get('tracks', tid)['revision'] == before['revision']
    monkeypatch.setattr(media, 'prepare_video', original)
    retry = client.post(f'/api/tracks/{tid}/retry').json()
    assert retry['kind'] == 'video' and wait_for_job(client, retry['id'])['status'] == 'done'
    assert client.get('/api/tracks').json()[0]['has_video']
    assert store.get('tracks', tid)['m4a_path'] == before['m4a_path']


def test_pure_audio_and_cover_choices_preserve_originals(client, audio_sample):
    response = client.post('/api/imports/file', data={'keep_video': 'true', 'cover_style': 'groove'},
                           files={'file': ('audio.m4a', audio_sample.read_bytes())}).json()
    assert wait_for_job(client, response['job_id'])['status'] == 'done'
    tid = response['track_id']
    track = client.get('/api/tracks').json()[0]
    assert not track['has_video'] and not track['can_prepare_video'] and not track['keep_video']
    assert client.get(f'/api/tracks/{tid}/video').status_code == 404
    assert client.put(f'/api/tracks/{tid}/cover-choice', json={'style': 'uploaded'}).status_code == 422
    # Simulate a v0.1 record without the new cover fields.
    store = client.app.state.store
    original = store.path(f'tracks/{tid}/original.jpg')
    Image.new('RGB', (40, 40), 'red').save(original)
    legacy = store.get('tracks', tid)
    for field in ('cover_style', 'original_cover_path', 'uploaded_cover_path'):
        legacy.pop(field, None)
    legacy['cover_path'] = store.relative(original)
    store.put('tracks', legacy)
    assert client.get('/api/tracks').json()[0]['has_original_cover']
    picture = io.BytesIO(); Image.new('RGB', (30, 30), 'blue').save(picture, 'PNG')
    client.post(f'/api/tracks/{tid}/cover', files={'file': ('cover.png', picture.getvalue())})
    uploaded = client.get(f'/api/tracks/{tid}/cover?kind=uploaded').content
    for style in ('groove', 'collage', 'type', 'dusk', 'original', 'uploaded'):
        result = client.put(f'/api/tracks/{tid}/cover-choice', json={'style': style})
        assert result.status_code == 200 and result.json()['cover_style'] == style
        assert client.get(f'/api/tracks/{tid}/cover?kind=original').content == original.read_bytes()
        assert client.get(f'/api/tracks/{tid}/cover?kind=uploaded').content == uploaded
    assert client.put(f'/api/tracks/{tid}/cover-choice', json={'style': '../bad'}).status_code == 422
