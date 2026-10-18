"""Conservative automatic acceptance and an ASR retry; no invented missing timestamps."""
import math
from difflib import SequenceMatcher
from app.song_identity import normalized


def acceptable(rows, expected_count, coverage=None):
    if not rows or not expected_count:return False
    if coverage is not None and coverage<.8:return False
    if coverage is None and len(rows)!=expected_count:return False
    previous=0
    confidence=[]
    for row in rows:
        start,end=row.get('start'),row.get('end')
        if not isinstance(start,(int,float)) or not isinstance(end,(int,float)) or not math.isfinite(start+end):return False
        if start<previous or not .25<=end-start<=22 or not row.get('text','').strip():return False
        confidence.append(row.get('confidence',0))
        previous=end
    return min(confidence)>=.25 and sum(c>=.45 for c in confidence)/len(confidence)>=.85


def match_transcription(segments,original):
    """Anchor matching original phrases to observed ASR segments, including repeated choruses.

    A combined phrase keeps the segment's actual boundaries; we do not divide time among words.
    """
    choices=[]
    for i in range(len(original)):
        for count in range(1,min(3,len(original)-i)+1):
            text=' '.join(original[i:i+count])
            choices.append((i,count,text,normalized(text)))
    rows=[];covered=set()
    for segment in segments:
        observed=normalized(segment.get('text',''))
        if len(observed)<4:continue
        candidates=[(SequenceMatcher(None,observed,key,autojunk=False).ratio(),i,count,text) for i,count,text,key in choices]
        score,i,count,text=max(candidates,default=(0,0,0,''))
        if score<.76:continue
        words=segment.get('words') or []
        probability=sum(w.get('probability',0) for w in words)/max(len(words),1)
        if probability<.4:continue
        rows.append(dict(text=text,start=segment['start'],end=segment['end'],confidence=min(score,probability),original_indices=list(range(i,i+count))))
        covered.update(range(i,i+count))
    # Repeated identical phrases in the supplied text represent the same observed lyric content.
    found={normalized(original[i]) for i in covered}
    unique={normalized(t) for t in original if normalized(t)}
    return rows,len(found)/max(1,len(unique))
