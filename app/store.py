"""Small, durable local library. Only relative file paths are persisted."""
import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS tracks (id TEXT PRIMARY KEY, doc TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, doc TEXT NOT NULL)')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.root / 'library.sqlite3', timeout=15)
        try:
            db.execute('PRAGMA journal_mode=WAL')
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def put(self, table, doc):
        assert table in ('tracks', 'jobs')
        with self.lock, self.connect() as db:
            db.execute(f'INSERT OR REPLACE INTO {table} VALUES (?, ?)', (doc['id'], json.dumps(doc, ensure_ascii=False)))
        return doc

    def get(self, table, key):
        assert table in ('tracks', 'jobs')
        with self.lock, self.connect() as db:
            row = db.execute(f'SELECT doc FROM {table} WHERE id = ?', (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def all(self, table):
        assert table in ('tracks', 'jobs')
        with self.lock, self.connect() as db:
            rows = db.execute(f'SELECT doc FROM {table}').fetchall()
        return sorted((json.loads(r[0]) for r in rows), key=lambda r: r['created_at'], reverse=True)

    def patch(self, table, key, **values):
        with self.lock:
            doc = self.get(table, key)
            if not doc:
                raise KeyError(key)
            doc.update(values, updated_at=now())
            return self.put(table, doc)

    def new_track(self, **values):
        doc = dict(id=uuid.uuid4().hex, title='正在导入', artist='', album='', version='', note='',
                   source_kind='bilibili', source_url='', source_title='', uploader='', source_path='',
                   preview_path='', source_duration=0, duration=0, trim_start=0, trim_end=None, cover_path='',
                   m4a_path='', mp3_path='', revision='', status='processing', error='', favorite=False,
                   archived=False, created_at=now(), updated_at=now())
        doc.update(keep_video=False, video_path='', cover_style='original', original_cover_path='', uploaded_cover_path='')
        doc.update(values)
        return self.put('tracks', doc)

    def new_job(self, track_id, kind):
        return self.put('jobs', dict(id=uuid.uuid4().hex, track_id=track_id, kind=kind,
                                    status='queued', phase='等待处理', error='', created_at=now(), updated_at=now()))

    def path(self, relative):
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError('Invalid storage path')
        return path

    def relative(self, path):
        return str(path.resolve().relative_to(self.root))

    def recover(self):
        for job in self.all('jobs'):
            if job['status'] in ('queued', 'running'):
                self.patch('jobs', job['id'], status='error', phase='处理已中断', error='应用上次退出时任务尚未完成，请重试。')
        for track in self.all('tracks'):
            if track['status'] == 'processing':
                ready = bool(track['m4a_path'] and self.path(track['m4a_path']).exists())
                self.patch('tracks', track['id'], status='ready' if ready else 'error', error='上次处理已中断，可以重试。')
