"""Anonymous lyric lookup, restricted to fixed providers; no music or credentials."""
import base64
import html
import json
import re
import time
import uuid
from collections import OrderedDict
from difflib import SequenceMatcher
from functools import wraps
from threading import RLock

import httpx

from .lyrics import parse_lrc
from .translations import attach_by_time

PROVIDERS = {'netease': '网易云音乐', 'qq': 'QQ 音乐', 'lrclib': 'LRCLIB'}


class LookupError(ValueError):
    pass


def readable_source_errors(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except LookupError:
            raise
        except (KeyError, TypeError, AttributeError, ValueError, OverflowError) as error:
            raise LookupError('歌词来源的数据格式发生变化，请切换来源或直接粘贴歌词。') from error
    return wrapped


class LyricSources:
    def __init__(self, transport=None):
        self.transport = transport
        self.candidates = OrderedDict()
        self.lock = RLock()

    def request(self, url, *, params=None, payload=None):
        try:
            with httpx.Client(timeout=15, follow_redirects=False, transport=self.transport,
                              headers={'User-Agent': 'Liushengjian/0.7 (local lyric lookup)',
                                       'Referer': 'https://music.163.com/' if '163.com' in url else 'https://y.qq.com/'}) as client:
                with client.stream('POST' if payload else 'GET', url, params=params, json=payload) as response:
                    response.raise_for_status()
                    data = bytearray()
                    for chunk in response.iter_bytes():
                        data.extend(chunk)
                        if len(data) > 2 * 1024 * 1024:
                            raise LookupError('歌词来源返回内容过大，请换一个来源。')
                    return json.loads(data)
        except (httpx.HTTPError, ValueError) as error:
            if isinstance(error, LookupError):
                raise
            raise LookupError('这个歌词来源暂时无法读取，请稍后重试或切换来源。') from error

    def qq(self, method, module, param):
        response = self.request('https://u.y.qq.com/cgi-bin/musicu.fcg', payload={
            'comm': {'ct': 11, 'cv': '1003006', 'v': '1003006', 'tmeAppID': 'qqmusiclight', 'uid': '0'},
            'request': dict(method=method, module=module, param=param)})
        if response.get('code') != 0 or response.get('request', {}).get('code') != 0:
            raise LookupError('QQ 音乐暂时拒绝了查询，请切换网易云或 LRCLIB。')
        return response['request']['data']

    @readable_source_errors
    def search(self, provider, query, track):
        if provider == 'netease':
            data = self.request('https://music.163.com/api/search/get/web', params={'s': query, 'type': 1, 'limit': 20})
            if data.get('code') != 200:
                raise LookupError('网易云查询暂时不可用，请切换来源。')
            items = [dict(remote_id=str(s['id']), title=s.get('name', ''),
                          artist=' / '.join(a.get('name', '') for a in s.get('artists', [])),
                          album=s.get('album', {}).get('name', ''), duration=s.get('duration', 0)/1000)
                     for s in data.get('result', {}).get('songs', [])[:20]]
        elif provider == 'qq':
            data = self.qq('DoSearchForQQMusicLite', 'music.search.SearchCgiService',
                           dict(query=query, search_type=0, num_per_page=20, page_num=1, highlight=0,
                                remoteplace='search.android.keyboard'))
            items = [dict(remote_id=str(s['id']), mid=s.get('mid', ''), title=s.get('title', ''),
                          artist=' / '.join(a.get('name', '') for a in s.get('singer', [])),
                          album=s.get('album', {}).get('name', ''), duration=s.get('interval', 0))
                     for s in data.get('body', {}).get('item_song', [])[:20]]
        else:
            data = self.request('https://lrclib.net/api/search', params={'q': query})
            if not isinstance(data, list):
                raise LookupError('歌词库返回异常，请重试。')
            items = [dict(remote_id=str(s['id']), title=s.get('trackName', ''), artist=s.get('artistName', ''),
                          album=s.get('albumName', ''), duration=s.get('duration', 0)) for s in data[:20]]
        def similarity(a, b):
            clean = lambda s: re.sub(r'[^\w]', '', s.lower())
            return SequenceMatcher(None, clean(a), clean(b)).ratio() if a else 0
        for item in items:
            item.update(provider=provider, source=PROVIDERS[provider])
            item['rank'] = round(100*(.65*similarity(track.get('title',''), item['title']) + .35*similarity(track.get('artist',''), item['artist'])))
            item['duration_difference'] = round(abs((track.get('source_duration') or 0)-item['duration']), 1)
        items.sort(key=lambda s: (-s['rank'], s['duration_difference']))
        with self.lock:
            now = time.monotonic()
            for key in list(self.candidates):
                if now-self.candidates[key][0] > 1800:
                    self.candidates.pop(key)
            results = []
            for item in items:
                token = uuid.uuid4().hex
                self.candidates[token] = (now, item)
                results.append({k: v for k, v in {**item, 'token': token}.items() if k not in ('remote_id', 'mid')})
            while len(self.candidates) > 200:
                self.candidates.popitem(last=False)
        return results

    @readable_source_errors
    def fetch(self, token):
        with self.lock:
            entry = self.candidates.get(token)
        if not entry or time.monotonic()-entry[0] > 1800:
            raise LookupError('候选已过期，请重新搜索。')
        item = entry[1]
        provider = item['provider']
        synced, plain, translated = '', '', ''
        if provider == 'netease':
            data = self.request('https://music.163.com/api/song/lyric', params={'id': item['remote_id'], 'lv': -1, 'kv': -1, 'tv': -1})
            synced = data.get('lrc', {}).get('lyric', '')
            translated = (data.get('tlyric') or {}).get('lyric') or ''
        elif provider == 'qq':
            data = self.request('https://c.y.qq.com/lyric/fcgi-bin/fcg_query_lyric_new.fcg',
                                params={'songmid': item['mid'], 'format': 'json', 'nobase64': 1})
            if data.get('code') != 0:
                raise LookupError('这个 QQ 版本的歌词暂时不可用，请换一个候选或来源。')
            synced = data.get('lyric', '')
            if synced and '[' not in synced:
                try:
                    synced = base64.b64decode(synced, validate=True).decode('utf-8')
                except (ValueError, UnicodeError):
                    raise LookupError('该歌词格式暂不支持，请切换来源。') from None
            synced = html.unescape(synced)
            translated = html.unescape(data.get('trans') or '')
        else:
            data = self.request('https://lrclib.net/api/get/' + item['remote_id'])
            synced, plain = data.get('syncedLyrics') or '', data.get('plainLyrics') or ''
        rows = []
        if synced:
            try:
                rows = parse_lrc(synced.encode())['lines']
                plain = '\n'.join(r['text'] for r in rows if r['text'])
                if translated:
                    try: attach_by_time(rows, parse_lrc(translated.encode())['lines'])
                    except ValueError: pass  # Original lyrics remain usable if the translation is malformed.
            except ValueError:
                plain = plain or re.sub(r'\[[^\]]*\]', '', synced)
        # Credit lines are not normally sung; users review the editable text before alignment.
        plain = '\n'.join(line.strip() for line in plain.splitlines() if line.strip() and not re.match(
            r'^(作词|作曲|编曲|制作人|混音|录音|词|曲|歌词贡献|翻译|Lyrics by|Composed by)\s*[:：]', line.strip(), re.I))
        if not plain.strip():
            raise LookupError('这个版本没有可用歌词，可能是纯音乐，请换一个候选。')
        if len(plain) > 16000:
            raise LookupError('歌词过长，请使用较短版本或手动粘贴。')
        return dict(text=plain, title=item['title'], artist=item['artist'], source=item['source'], provider=provider,
                    source_id=item['remote_id'], synced_available=bool(synced),
                    translations=[dict(text=r['text'], translation=r['translation']) for r in rows if r.get('translation')])
