"""Bounded local LRC / enhanced LRC parsing. All timestamps are seconds."""
import re

MAX_BYTES = 256 * 1024
STAMP = r'(\d{1,3}):([0-5]\d)(?:[.:](\d{1,3}))?'
LINE = re.compile(r'\[' + STAMP + r'\]')
WORD = re.compile(r'<' + STAMP + r'>')


def seconds(match):
    minutes, secs, fraction = match.groups()
    return int(minutes) * 60 + int(secs) + (int(fraction) / 10 ** len(fraction) if fraction else 0)


def parse_lrc(data: bytes):
    if len(data) > MAX_BYTES:
        raise ValueError('歌词文件最大为 256 KB。')
    try:
        text = data.decode('utf-16' if data.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig')
    except UnicodeError:
        try:
            text = data.decode('gb18030')
        except UnicodeError:
            raise ValueError('无法读取歌词编码，请另存为 UTF-8 的 LRC 文件。') from None
    if '\x00' in text:
        raise ValueError('请选择文本格式的 LRC 文件。')
    offsets = re.findall(r'\[offset:([+-]?\d+)\]', text, re.I)
    file_offset = int(offsets[-1]) / 1000 if offsets else 0
    if abs(file_offset) > 600:
        raise ValueError('文件内的 offset 超出 ±600 秒，请检查歌词时间。')
    rows = []
    for raw in text.splitlines():
        raw = raw.strip()
        stamps = []
        while match := LINE.match(raw):
            stamps.append(seconds(match))
            raw = raw[match.end():]
        if not stamps:
            continue
        if len(raw) > 4000:
            raise ValueError('单句歌词过长，请分行后导入。')
        marks = list(WORD.finditer(raw))
        plain = WORD.sub('', raw).strip()
        if len(plain) > 500:
            raise ValueError('每句歌词最多 500 字。')
        base_words = []
        end_mark = None
        if marks:
            times = [seconds(m) for m in marks]
            if any(b < a for a, b in zip([stamps[0]] + times, times)):
                raise ValueError('逐字时间须按顺序排列，且不能早于本句开始。')
            prefix = raw[:marks[0].start()]
            if prefix.strip():
                base_words.append(dict(start=stamps[0], end=times[0], text=prefix))
            for i, mark in enumerate(marks):
                word = raw[mark.end():marks[i+1].start() if i+1 < len(marks) else len(raw)]
                if word.strip():
                    base_words.append(dict(start=times[i], end=times[i+1] if i+1 < len(marks) else None, text=word))
                elif i == len(marks) - 1:
                    end_mark = times[i]
        for start in stamps:
            shift = start - stamps[0] - file_offset
            words = [dict(text=w['text'], start=w['start']+shift, end=w['end']+shift if w['end'] is not None else None) for w in base_words]
            rows.append(dict(start=start-file_offset, text=plain, words=words,
                             end=end_mark+shift if end_mark is not None else None))
        if len(rows) > 1500:
            raise ValueError('最多支持 1500 句歌词，请拆分文件。')
    if not rows or not any(r['text'] for r in rows):
        raise ValueError('没有找到带 [分:秒] 时间标记的歌词。支持 LRC 和 <分:秒> 逐字 LRC。')
    rows.sort(key=lambda r: r['start'])
    # Same-time translation lines stay together, with no invented word timing.
    merged = []
    for row in rows:
        if merged and row['start'] == merged[-1]['start']:
            previous = merged[-1]
            previous['text'] = '\n'.join(filter(None, [previous['text'], row['text']]))
            previous['words'] = []
            previous['end'] = None
        else:
            merged.append(row)
    if sum(len(r['text']) for r in merged) > 50000:
        raise ValueError('歌词文字过多，请拆分文件。')
    for i, row in enumerate(merged):
        next_start = merged[i+1]['start'] if i+1 < len(merged) else max(row['start'] + 8, row['end'] or 0, row['words'][-1]['start'] + 2 if row['words'] else 0)
        end = row['end'] if row['end'] is not None else next_start
        row['end'] = min(next_start, max(row['start'], end))
        for word in row['words']:
            word['end'] = min(row['end'], word['end'] if word['end'] is not None else row['end'])
            if word['end'] < word['start']:
                raise ValueError('逐字时间超过下一句，请检查时间轴。')
    return dict(lines=merged, timed_words=any(r['words'] for r in merged), file_offset=file_offset)
