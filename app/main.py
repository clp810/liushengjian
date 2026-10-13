import io
import logging
import os
import re
import shutil
import subprocess
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

from . import media
from .store import Store

PROJECT = Path(__file__).resolve().parents[1]
MAX_UPLOAD = 512 * 1024 * 1024
EXTENSIONS = {'.mp3', '.m4a', '.aac', '.flac', '.wav', '.ogg', '.opus', '.mp4', '.mov', '.mkv', '.webm'}


class ImportRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2000)
    keep_video: bool = False
    cover_style: Literal['original', 'groove', 'collage', 'type', 'dusk'] = 'original'


class CoverChoice(BaseModel):
    style: Literal['original', 'uploaded', 'groove', 'collage', 'type', 'dusk']


class EditRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    title: str = Field(min_length=1, max_length=300)
    artist: str = Field(default='', max_length=200)
    album: str = Field(default='', max_length=200)
    version: str = Field(default='', max_length=120)
    note: str = Field(default='', max_length=2000)
    trim_start: float = Field(default=0, ge=0)
    trim_end: float | None = Field(default=None, gt=0)


class FlagsRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    favorite: bool | None = None
    archived: bool | None = None


def create_app(data_dir=None, seed=True):
    store = Store(Path(data_dir or os.environ.get('LIUSHENGJIAN_DATA', PROJECT / 'data')))
    pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='audio')

    def update_job(job, **changes):
        return store.patch('jobs', job['id'], **changes)

    def process(track_id, job, edits=None):
        try:
            update_job(job, status='running', phase='准备音频')
            track = store.get('tracks', track_id)
            directory = store.path(f'tracks/{track_id}')
            directory.mkdir(parents=True, exist_ok=True)
            phase = lambda text: update_job(job, phase=text)
            if job['kind'] == 'video':
                source = store.path(track['source_path'])
                if not media.probe(source)['video']:
                    if not track['source_url']:
                        raise media.MediaError('这份收藏只有音频，请重新导入带画面的本地视频。')
                    download_dir = directory / ('video-source-' + uuid.uuid4().hex[:8])
                    download_dir.mkdir()
                    source, _, _ = media.fetch_bilibili(track['source_url'], download_dir, phase, keep_video=True)
                if abs(media.probe(source)['duration'] - track['source_duration']) > 1:
                    raise media.MediaError('现场视频与已收藏音轨的时长不同，无法安全对应，请作为新的收藏导入。')
                video = media.prepare_video(source, directory, phase)
                store.patch('tracks', track_id, video_path=store.relative(video), keep_video=True, status='ready', error='')
                update_job(job, status='done', phase='现场视频已保存在本地')
                return
            if not track['source_path']:
                source, info, cover = media.fetch_bilibili(track['source_url'], directory, phase, keep_video=track.get('keep_video', False))
                track = store.patch('tracks', track_id, source_path=store.relative(source),
                                    source_title=info.get('title', ''), title=info.get('title', '未命名音频'),
                                    uploader=info.get('uploader', ''), cover_path=store.relative(cover) if cover else '',
                                    original_cover_path=store.relative(cover) if cover else '')
            source = store.path(track['source_path'])
            details = media.probe(source)
            if job['kind'] == 'import' and track['source_kind'] == 'local':
                tags = {k.lower(): v for k, v in details['tags'].items()}
                track = store.patch('tracks', track_id, title=str(tags.get('title') or track['title'])[:300],
                                    artist=str(tags.get('artist') or track['artist'])[:200],
                                    album=str(tags.get('album') or track['album'])[:200])
            preview = directory / 'preview.m4a'
            if not preview.exists():
                phase('准备可试听的原音轨')
                media.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
                           '-protocol_whitelist', 'file,pipe', '-i', str(source), '-map', '0:a:0', '-vn',
                           '-c:a', 'copy' if details['codec'] == 'aac' else 'aac', '-movflags', '+faststart', str(preview)])
            track = store.patch('tracks', track_id, source_duration=details['duration'], source_has_video=bool(details['video']), preview_path=store.relative(preview))
            metadata = {**track, **(edits or {})}
            rendered = media.render(source, directory, metadata, details['duration'], details['codec'], phase)
            values = {**(edits or {}), **rendered}
            if track.get('keep_video') and not track.get('video_path'):
                if details['video']:
                    values['video_path'] = store.relative(media.prepare_video(source, directory, phase))
                else:
                    values['keep_video'] = False
            values['m4a_path'] = store.relative(values['m4a_path'])
            values['mp3_path'] = store.relative(values['mp3_path'])
            store.patch('tracks', track_id, **values, status='ready', error='')
            update_job(job, status='done', phase='已加入收藏，可以播放和导出')
        except Exception as e:
            message = str(e) if isinstance(e, media.MediaError) else '处理未完成，请重试；如果仍失败，可换一个文件。'
            logging.error('Audio task %s failed (%s)', job['id'], type(e).__name__)
            track = store.get('tracks', track_id)
            ready = bool(track and track['m4a_path'] and store.path(track['m4a_path']).exists())
            store.patch('tracks', track_id, status='ready' if ready else 'error', error=message)
            update_job(job, status='error', phase='处理失败', error=message)

    def queue(track_id, kind, edits=None):
        with store.lock:
            track = store.get('tracks', track_id)
            if any(j['track_id'] == track_id and j['status'] in ('queued', 'running') for j in store.all('jobs')):
                raise HTTPException(409, '这首音频正在处理中，请稍等。')
            if sum(j['status'] in ('queued', 'running') for j in store.all('jobs')) >= 8:
                raise HTTPException(429, '最多同时排队 8 个任务，请等待一些任务完成。')
            job = store.new_job(track_id, kind)
            if edits is not None:
                job = store.patch('jobs', job['id'], edits=edits)
            store.patch('tracks', track_id, status='processing', error='')
            pool.submit(process, track_id, job, edits)
        return job

    def require_track(track_id, ready=False):
        track = store.get('tracks', track_id)
        if not track:
            raise HTTPException(404, '没有找到这首收藏。')
        if ready and track['status'] != 'ready':
            raise HTTPException(409, '音频尚未处理完成，请稍等。')
        return track

    def public_track(track):
        result = {k: v for k, v in track.items() if not k.endswith('_path')}
        result['has_cover'] = bool(track['cover_path'])
        result['has_audio'] = bool(track['m4a_path'])
        result['has_source'] = bool(track['source_path'])
        result['has_video'] = bool(track.get('video_path'))
        result['can_prepare_video'] = bool(track.get('source_url') or track.get('source_has_video') or
                                           (track.get('source_has_video') is None and Path(track['source_path']).suffix in {'.mp4', '.mov', '.mkv', '.webm'}))
        result['cover_style'] = track.get('cover_style', 'original')
        result['has_original_cover'] = bool(track.get('original_cover_path') or (not track.get('cover_style') and track['cover_path']))
        result['has_uploaded_cover'] = bool(track.get('uploaded_cover_path'))
        return result

    def maybe_seed():
        source = PROJECT / '.cache/BV1CFG66UELP.source.m4a'
        marker = store.root / '.sample-imported'
        if marker.exists() or not source.exists():
            return
        # One explicit, previously downloaded user sample; never fetch demo music.
        track = store.new_track(title='Automatic', artist='宇多田光', version='人声伴奏 · 录音室现场版',
                                album='B站现场收藏', source_kind='bilibili',
                                source_url='https://www.bilibili.com/video/BV1CFG66UELP/',
                                source_title='Automatic / 宇多田光 人声伴奏 录音室现场版',
                                note='项目首个真实样例，歌曲信息依据视频标题填写。')
        directory = store.path(f"tracks/{track['id']}")
        directory.mkdir(parents=True, exist_ok=True)
        dest = directory / 'source.m4a'
        shutil.copy2(source, dest)
        store.patch('tracks', track['id'], source_path=store.relative(dest))
        marker.touch()
        queue(track['id'], 'import')

    @asynccontextmanager
    async def lifespan(app):
        store.recover()
        if seed:
            maybe_seed()
        yield
        pool.shutdown(wait=True, cancel_futures=False)

    app = FastAPI(title='留声间', version='0.2.0', lifespan=lifespan)
    app.state.store = store

    @app.middleware('http')
    async def local_only(request: Request, call_next):
        host = request.headers.get('host', '').split(':')[0]
        if host not in ('127.0.0.1', 'localhost', 'testserver'):
            return JSONResponse({'detail': '仅允许本机访问。'}, 403)
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            origin = request.headers.get('origin')
            if (origin and origin != f'http://{request.headers.get("host")}') or request.headers.get('sec-fetch-site') == 'cross-site':
                return JSONResponse({'detail': '请从留声间本地页面操作。'}, 403)
            if request.headers.get('content-length', '').isdigit() and int(request.headers['content-length']) > MAX_UPLOAD + 1024 * 1024:
                return JSONResponse({'detail': '单个文件最多 512 MB。'}, 413)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; script-src 'self'; media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
        if request.url.path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    @app.get('/api/status')
    def status():
        return dict(name='留声间', version='0.2.0', ffmpeg=bool(shutil.which('ffmpeg')),
                    ffprobe=bool(shutil.which('ffprobe')), storage=str(store.root),
                    cloud_mode='manual', max_upload_mb=512)

    @app.get('/api/tracks')
    def tracks():
        return [public_track(t) for t in store.all('tracks')]

    @app.get('/api/jobs')
    def jobs():
        return store.all('jobs')[:80]

    @app.post('/api/imports/url', status_code=202)
    def import_url(body: ImportRequest):
        try:
            url = media.canonical_url(body.url)
        except ValueError as e:
            raise HTTPException(422, str(e))
        with store.lock:
            existing = next((t for t in store.all('tracks') if t['source_url'] == url and not t['archived']), None)
            if existing:
                return dict(track_id=existing['id'], duplicate=True)
            if sum(j['status'] in ('queued', 'running') for j in store.all('jobs')) >= 8:
                raise HTTPException(429, '任务队列已满，请稍后再试。')
            track = store.new_track(source_url=url, album='B站现场收藏', keep_video=body.keep_video, cover_style=body.cover_style)
            job = queue(track['id'], 'import')
        return dict(track_id=track['id'], job_id=job['id'], duplicate=False)

    @app.post('/api/imports/file', status_code=202)
    async def import_file(file: UploadFile, keep_video: bool = Form(False),
                          cover_style: Literal['original', 'groove', 'collage', 'type', 'dusk'] = Form('original')):
        suffix = Path(file.filename or '').suffix.lower()
        if suffix not in EXTENSIONS:
            raise HTTPException(422, '请选择 MP3、M4A、FLAC、WAV、MP4 等音视频文件。')
        if sum(j['status'] in ('queued', 'running') for j in store.all('jobs')) >= 8:
            raise HTTPException(429, '任务队列已满，请稍后再试。')
        track = store.new_track(title=Path(file.filename).stem[:300], source_kind='local',
                                source_title=file.filename[:400], album='本地收藏', keep_video=keep_video, cover_style=cover_style)
        directory = store.path(f"tracks/{track['id']}")
        directory.mkdir(parents=True, exist_ok=True)
        source = directory / ('source' + suffix)
        try:
            size = 0
            with source.open('wb') as out:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_UPLOAD:
                        raise HTTPException(413, '单个文件最多 512 MB。')
                    out.write(chunk)
            if not size:
                raise HTTPException(422, '文件是空的，请重新选择。')
            store.patch('tracks', track['id'], source_path=store.relative(source))
            job = queue(track['id'], 'import')
            return dict(track_id=track['id'], job_id=job['id'])
        except HTTPException as e:
            store.patch('tracks', track['id'], status='error', error=str(e.detail))
            source.unlink(missing_ok=True)
            raise
        finally:
            await file.close()

    @app.post('/api/tracks/{track_id}/retry', status_code=202)
    def retry(track_id: str):
        track = require_track(track_id)
        if not track['source_path'] and not track['source_url']:
            raise HTTPException(422, '源文件没有保存成功，请重新导入本地文件。')
        previous = next((j for j in store.all('jobs') if j['track_id'] == track_id and j['status'] == 'error'), None)
        edits = previous.get('edits') if previous else None
        return queue(track_id, 'edit' if edits else 'video' if previous and previous['kind'] == 'video' else 'retry', edits)

    @app.post('/api/tracks/{track_id}/video', status_code=202)
    def add_video(track_id: str):
        track = require_track(track_id, ready=True)
        if track.get('video_path'):
            raise HTTPException(409, '这份收藏已有本地视频。')
        return queue(track_id, 'video')

    @app.get('/api/tracks/{track_id}/video')
    def get_video(track_id: str):
        track = require_track(track_id)
        path = track.get('video_path')
        if not path or not store.path(path).is_file():
            raise HTTPException(404, '这份收藏没有本地视频。')
        return FileResponse(store.path(path), media_type='video/mp4')

    @app.put('/api/tracks/{track_id}', status_code=202)
    def edit(track_id: str, body: EditRequest):
        track = require_track(track_id, ready=True)
        values = body.model_dump()
        values['title'] = body.title.strip()
        if not values['title']:
            raise HTTPException(422, '请填写歌名。')
        end = track['source_duration'] if body.trim_end is None else body.trim_end
        if end > track['source_duration'] + .05 or end - body.trim_start < .25:
            raise HTTPException(422, '裁剪范围应在原音频内，且片段至少 0.25 秒。')
        return queue(track_id, 'edit', values)

    @app.patch('/api/tracks/{track_id}')
    def flags(track_id: str, body: FlagsRequest):
        require_track(track_id, ready=False)
        if body.archived is not None:

            if store.get('tracks', track_id)['status'] == 'processing':
                raise HTTPException(409, '正在处理这首音频，请完成后再收起。')
        return public_track(store.patch('tracks', track_id, **body.model_dump(exclude_none=True)))

    @app.post('/api/tracks/{track_id}/cover')
    async def cover(track_id: str, file: UploadFile):
        track = require_track(track_id, ready=True)
        data = await file.read(10 * 1024 * 1024 + 1)
        await file.close()
        if len(data) > 10 * 1024 * 1024:
            raise HTTPException(413, '封面最大为 10 MB。')
        path = store.path(f'tracks/{track_id}/cover-{uuid.uuid4().hex[:8]}.jpg')
        try:
            media.clean_cover(io.BytesIO(data), path)
        except media.MediaError as e:
            raise HTTPException(422, str(e))
        original = track.get('original_cover_path') or (track['cover_path'] if not track.get('cover_style') else '')
        return public_track(store.patch('tracks', track_id, cover_path=store.relative(path),
                                       uploaded_cover_path=store.relative(path), original_cover_path=original, cover_style='uploaded'))

    @app.put('/api/tracks/{track_id}/cover-choice')
    def cover_choice(track_id: str, body: CoverChoice):
        track = require_track(track_id, ready=True)
        original = track.get('original_cover_path') or (track['cover_path'] if not track.get('cover_style') else '')
        values = dict(cover_style=body.style, original_cover_path=original)
        if body.style in ('original', 'uploaded'):
            path = original if body.style == 'original' else track.get('uploaded_cover_path')
            if body.style == 'uploaded' and not path:
                raise HTTPException(422, '请先上传自己的封面。')
            values['cover_path'] = path or ''
        return public_track(store.patch('tracks', track_id, **values))

    @app.get('/api/tracks/{track_id}/cover')
    def get_cover(track_id: str, kind: Literal['current', 'original', 'uploaded'] = 'current'):
        track = require_track(track_id)
        path = track['cover_path'] if kind == 'current' else track.get(kind + '_cover_path')
        if kind == 'original' and not track.get('cover_style'):
            path = track['cover_path']
        if not path or not store.path(path).is_file():
            raise HTTPException(404)
        return FileResponse(store.path(path), media_type='image/jpeg')

    @app.get('/api/tracks/{track_id}/audio')
    def audio(track_id: str, kind: str = 'm4a', download: bool = False):
        if kind not in ('m4a', 'mp3', 'source'):
            raise HTTPException(422, '不支持此格式。')
        track = require_track(track_id)
        path = track.get('preview_path', '') if kind == 'source' else track[kind + '_path']
        if not path or not store.path(path).is_file():
            raise HTTPException(404, '音频文件尚未就绪。')
        title = track['title'] + (f"（{track['version']}）" if track['version'] else '')
        filename = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', (track['artist'] + ' - ' if track['artist'] else '') + title)[:160]
        ext = store.path(path).suffix
        mime = 'audio/mp4' if ext == '.m4a' else 'audio/mpeg' if ext == '.mp3' else None
        return FileResponse(store.path(path), media_type=mime, filename=(filename + ext) if download else None)

    @app.post('/api/tracks/{track_id}/reveal')
    def reveal(track_id: str):
        track = require_track(track_id, ready=True)
        if sys.platform != 'darwin':
            raise HTTPException(409, '当前系统请使用「下载」按钮保存文件。')
        # This endpoint opens only an application-owned export, never a client-supplied path.
        subprocess.run(['open', '-R', str(store.path(track['mp3_path']))], check=True)
        return {'ok': True}

    @app.get('/')
    def home():
        return FileResponse(PROJECT / 'app/static/index.html')

    app.mount('/static', StaticFiles(directory=PROJECT / 'app/static'), name='static')
    return app


app = create_app(seed=os.environ.get('LIUSHENGJIAN_SEED', '1') != '0')
