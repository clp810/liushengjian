"""Title-based song matching. Confidence here describes metadata, not audio recognition."""
import re
import unicodedata
from difflib import SequenceMatcher


def normalized(text):
    return ''.join(c for c in unicodedata.normalize('NFKC',text).casefold() if c.isalnum())


def identify(track, override=''):
    raw=unicodedata.normalize('NFKC',override or track.get('title') or track.get('source_title','')).strip()
    artist=track.get('artist','').strip()
    # Video titles often bracket the song, surrounding it with performer/channel/promo text.
    quoted=re.findall(r'[《「『](.*?)[》」』]',raw)
    bracketed=quoted or re.findall(r'【(.*?)】',raw)
    useful=[s for s in bracketed if normalized(s) and not re.search(r'4k|1080|720|字幕|高清|现场|live|中字|修复',s,re.I)]
    if useful:
        title=max(useful,key=len).strip()
        if not artist:
            prefix=raw.split(title)[0].rstrip('【《「『 -—·')
            prefix=re.sub(r'【[^】]*(?:修复|4K|字幕|高清)[^】]*】','',prefix,flags=re.I).strip('【】 ')
            artist=prefix if len(prefix)<60 else ''
    else:
        raw=re.sub(r'[\[（(][^\]）)]*(?:live|现场|4k|1080|720|字幕|高清|官方|中字)[^\]）)]*[\]）)]','',raw,flags=re.I)
        parts=re.split(r'\s+[-—–]\s+',raw,maxsplit=1)
        if len(parts)==2:
            if not artist:artist=parts[0].strip()
            title=parts[1].strip()
        else:title=raw
    if not useful and re.search(r'[\u4e00-\u9fff]',title):
        latin=re.findall(r"[A-Za-z][A-Za-z0-9 ,.!'’&-]*",title)
        if latin:
            phrase=max(latin,key=len).strip()
            if len(phrase)>=3 and normalized(phrase) not in ('live','official','cover'):
                prefix=title.split(phrase)[0].strip()
                title=phrase
                if not artist and len(prefix)<12 and not re.search(r'听过|好听|一版|翻唱|开麦|最强',prefix):artist=prefix
    cover=re.search(r'[（(]([^()（）]{2,60}?)\s+cover[）)]',raw,re.I)
    if cover:artist=cover.group(1).strip()
    title=re.sub(r'(?i)\b(?:official\s*(?:video|music\s*video|audio)|live|1080p|720p|4k)\b','',title)
    title=re.sub(r'【[^】]*】','',title).strip(' -—·')
    return dict(title=title[:200],artist=artist[:150])


def candidate_score(identity,item):
    a,b=normalized(identity['title']),normalized(item['title'])
    sim=SequenceMatcher(None,a,b,autojunk=False).ratio() if a and b else 0
    artist=normalized(identity['artist']);other=normalized(item['artist'])
    artist_score=SequenceMatcher(None,artist,other,autojunk=False).ratio() if artist else 0
    if artist and other and (artist in other or other in artist):artist_score=1
    score=.88*sim+.12*artist_score if artist else sim
    # A medley/remix is not a safe stand-in for a song merely contained in its title.
    if re.search(r'remix|medley|串烧|串燒|伴奏|instrumental|カラオケ|～| / ',item['title'],re.I) and not re.search(r'remix|medley|串烧|串燒|伴奏|instrumental|カラオケ|～| / ',identity['title'],re.I):score-=.22
    return score,sim,artist_score


def choose(identity,items):
    ranked=sorted(items,key=lambda item:candidate_score(identity,item),reverse=True)
    if not ranked:return None
    score,title_score,artist_score=candidate_score(identity,ranked[0])
    # A close title is required even when the performer matches. Ambiguous homonyms abstain.
    if title_score<.86 or score<.83:return None
    for alternative in ranked[1:]:
        second=candidate_score(identity,alternative)[0]
        different=normalized(alternative['artist'])!=normalized(ranked[0]['artist'])
        if different and score-second<.025 and artist_score<.8:return None
    return ranked[0]


def language(text):
    if re.search(r'[\u3040-\u30ff]',text):return 'ja'
    if re.search(r'[\uac00-\ud7af]',text):return 'ko'
    if re.search(r'[\u4e00-\u9fff]',text):return 'zh'
    # Latin-script languages are detected from audio by Whisper; no guessed language menu.
    return None
