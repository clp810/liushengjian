import json
import math
import logging
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen
from PIL import Image, ImageOps


class MediaError(Exception):
    pass


def canonical_url(value: str):
    value = value.strip()
    if re.fullmatch(r'BV[0-9A-Za-z]{10}', value):
        return f'https://www.bilibili.com/video/{value}/'
    parsed = urlparse(value)
    if parsed.scheme != 'https' or parsed.hostname not in ('www.bilibili.com', 'bilibili.com', 'm.bilibili.com') or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ValueError('请粘贴 https://www.bilibili.com/video/BV… 视频链接，或 BV 号。')
    match = re.fullmatch(r'/video/(BV[0-9A-Za-z]{10})/?', parsed.path)
    if not match:
        raise ValueError('当前支持 B站单个 BV 视频链接；分 P 可在链接后加 ?p=2。')
    p = parse_qs(parsed.query).get('p', ['1'])[0]
    if not p.isdigit() or not 1 <= int(p) <= 1000:
        raise ValueError('分 P 编号应为 1–1000 的整数。')
    return f'https://www.bilibili.com/video/{match[1]}/' + (f'?p={int(p)}' if int(p) != 1 else '')


def run(args, timeout=300):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as e:
        raise MediaError('缺少音频处理工具，请先安装 FFmpeg，再重新启动。') from e
    except subprocess.TimeoutExpired as e:
        raise MediaError('处理超时，请检查网络或换一个较短的视频后重试。') from e
    if result.returncode:
        # Raw external errors may contain temporary URLs. Do not expose them to clients.
        summary = next((line for line in reversed(result.stderr.splitlines()) if line.strip()), 'unknown failure')
        summary = re.sub(r'https?://\S+', '[media URL]', summary)[:400]
        logging.warning('Media command failed: %s', summary)
        err = result.stderr.lower()
        if 'yt_dlp' in ' '.join(args):
            if any(x in err for x in ('412', '403', 'login', 'cookies', 'premium', '会员', '登录')):
                raise MediaError('B站限制了此次访问，或视频需要登录/会员。可稍后重试，或导入你已保存的本地文件。')
            raise MediaError('未能获取 B站音频，请检查视频是否可公开访问和网络连接。也可以改用本地文件。')
        raise MediaError('音频处理失败：文件可能损坏、没有音轨，或编码不受支持。')
    return result.stdout


def probe(path):
    data = json.loads(run(['ffprobe', '-v', 'error', '-protocol_whitelist', 'file,pipe', '-show_format', '-show_streams', '-of', 'json', str(path)], 30))
    streams = [s for s in data.get('streams', []) if s.get('codec_type') == 'audio']
    if not streams:
        raise MediaError('这个文件没有可用的音轨。')
    duration = float(data.get('format', {}).get('duration') or streams[0].get('duration') or 0)
    if not math.isfinite(duration) or duration <= 0 or duration > 14400:
        raise MediaError('当前支持时长在 0–4 小时之间的音视频。')
    videos = [s for s in data.get('streams', []) if s.get('codec_type') == 'video' and not s.get('disposition', {}).get('attached_pic')]
    return dict(duration=duration, codec=streams[0]['codec_name'], tags=data.get('format', {}).get('tags', {}),
                video=videos[0] if videos else None)


def clean_cover(source, target):
    try:
        with Image.open(source) as image:
            if image.width * image.height > 40_000_000:
                raise ValueError('图片过大')
            image = ImageOps.exif_transpose(image).convert('RGB')
            image.thumbnail((1000, 1000))
            image.save(target, 'JPEG', quality=88)
    except Exception as e:
        raise MediaError('封面需要可读取的 JPG、PNG 或 WebP 图片（不超过 10 MB）。') from e


def fetch_bilibili(url, directory, phase, keep_video=False):
    url = canonical_url(url)
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        raise MediaError('请先安装 FFmpeg，音频提取和导出需要它。')
    phase('保存现场视频与音轨，长视频可能需要几分钟' if keep_video else '读取视频并提取音轨，长视频可能需要几分钟')
    run([sys.executable, '-m', 'yt_dlp', '--no-playlist', '--no-warnings', '--no-progress', '--no-cache-dir',
         '--socket-timeout', '20', '--retries', '1', '--fragment-retries', '1', '--max-filesize', '500M',
         '--match-filter', 'duration <= 14400', '-f',
         'bestvideo[height<=720][vcodec^=avc]+bestaudio/bestvideo[height<=720]+bestaudio/best[height<=720]' if keep_video else 'bestaudio',
         '--merge-output-format', 'mp4', '--write-info-json',
         '-o', str(directory / 'source.%(ext)s'), url], timeout=900)
    infos = list(directory.glob('source*.info.json'))
    if not infos:
        raise MediaError('未得到可用音轨；请检查视频时长（最多 4 小时）和访问权限。')
    info = json.loads(infos[0].read_text())
    candidates = [p for p in directory.glob('source.*') if p.suffix.lower() in ('.m4a', '.mp3', '.webm', '.opus', '.ogg', '.mp4', '.flac', '.aac')]
    if not candidates:
        raise MediaError('未找到下载完成的音轨。')
    cover = ''
    thumbnail = info.get('thumbnail', '')
    parsed = urlparse(thumbnail)
    if parsed.scheme in ('https', 'http') and (parsed.hostname or '').endswith('.hdslb.com'):
        try:
            with urlopen(Request(thumbnail.replace('http://', 'https://', 1), headers={'User-Agent': 'Mozilla/5.0'}), timeout=10) as response:
                final_host = urlparse(response.url).hostname or ''
                if not final_host.endswith('.hdslb.com'):
                    raise ValueError('Unexpected thumbnail host')
                content = response.read(10 * 1024 * 1024 + 1)
                if len(content) > 10 * 1024 * 1024:
                    raise ValueError('Thumbnail too large')
                tmp = directory / 'thumbnail.tmp'
                tmp.write_bytes(content)
                clean_cover(tmp, directory / 'cover.jpg')
                tmp.unlink(missing_ok=True)
                cover = str(directory / 'cover.jpg')
        except Exception:
            pass  # A thumbnail is optional; audio import still succeeds.
    for p in infos:
        p.unlink(missing_ok=True)  # Do not retain signed media URLs.
    return candidates[0], info, Path(cover) if cover else None


def prepare_video(source, directory, phase):
    """Create a browser-compatible full-length copy; never alter the source media."""
    details = probe(source)
    video = details['video']
    if not video:
        raise MediaError('源文件只有音频，没有现场画面。请导入带画面的视频文件。')
    target = directory / f'live-{uuid.uuid4().hex[:12]}.mp4'
    phase('准备本地现场画面，首次转换可能需要几分钟')
    args = ['ffmpeg', '-v', 'error', '-nostdin', '-n', '-protocol_whitelist', 'file,pipe', '-i', str(source),
            '-map', f"0:{video['index']}", '-map', '0:a:0', '-map_metadata', '-1']
    if video['codec_name'] == 'h264' and video.get('pix_fmt') == 'yuv420p':
        args += ['-c:v', 'copy']
    else:
        args += ['-c:v', 'libx264', '-preset', 'veryfast', '-crf', '23', '-pix_fmt', 'yuv420p',
                 '-vf', 'scale=trunc(iw/2)*2:trunc(ih/2)*2']
    args += ['-c:a', 'copy'] if details['codec'] == 'aac' else ['-c:a', 'aac', '-b:a', '192k']
    try:
        run(args + ['-movflags', '+faststart', str(target)], timeout=1800)
        result = probe(target)
        if not result['video'] or abs(result['duration'] - details['duration']) > .5:
            raise MediaError('视频转换后的时长不一致，请重试。')
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return target


def render(source, directory, metadata, source_duration, codec, phase):
    start = float(metadata.get('trim_start', 0))
    end = metadata.get('trim_end')
    end = source_duration if end is None else float(end)
    if not all(math.isfinite(n) for n in (start, end)) or start < 0 or end > source_duration + .05 or end - start < .25:
        raise MediaError('裁剪范围无效：开始应早于结束，片段至少 0.25 秒，且不能超过原音频时长。')
    end = min(end, source_duration)
    trimmed = start > .001 or end < source_duration - .05
    revision = uuid.uuid4().hex[:12]
    target = directory / 'renders' / revision
    target.mkdir(parents=True, exist_ok=True)
    title = metadata['title'] + (f"（{metadata['version']}）" if metadata.get('version') else '')
    tags = ['-metadata', f'title={title}', '-metadata', f"artist={metadata.get('artist', '')}",
            '-metadata', f"album={metadata.get('album', '')}", '-metadata', f"comment={metadata.get('note', '')}\n来源：{metadata.get('source_url', '')}"]
    phase('整理歌曲信息并生成音频文件')
    outputs = {}
    try:
        for ext in ('m4a', 'mp3'):
            output = target / f'audio.{ext}'
            args = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin', '-n', '-protocol_whitelist', 'file,pipe', '-i', str(source)]
            if trimmed:
                args += ['-ss', str(start), '-t', str(end - start)]
            args += ['-map', '0:a:0', '-vn', '-map_metadata', '-1']
            if ext == 'm4a':
                args += ['-c:a', 'copy'] if codec == 'aac' and not trimmed else ['-c:a', 'aac', '-b:a', '192k']
                args += ['-movflags', '+faststart']
            else:
                args += ['-c:a', 'libmp3lame', '-b:a', '192k', '-id3v2_version', '3']
            run(args + tags + [str(output)])
            observed = probe(output)
            if abs(observed['duration'] - (end - start)) > .3:
                raise MediaError('导出时长与选定片段不一致，请重试。')
            outputs[ext + '_path'] = output
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        raise
    return {**outputs, 'duration': end - start, 'revision': revision,
            'quality_note': '保留源音轨 · 未再次压缩' if codec == 'aac' and not trimmed else '已转码 · 音质取决于原始音源'}
