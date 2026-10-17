"""Keep translated text attached to its original line, independent of performance time."""
from collections import defaultdict, deque
import unicodedata


def key(text):
    return ''.join(c for c in unicodedata.normalize('NFKC', text).casefold() if c.isalnum())


def attach_by_text(lines, pairs):
    choices = defaultdict(deque)
    for pair in pairs:
        if pair.get('translation', '').strip() and key(pair['text']):
            choices[key(pair['text'])].append(pair['translation'].strip())
    repeated = {k: next(iter(set(v))) for k, v in choices.items() if len(set(v)) == 1}
    count = 0
    for line in lines:
        normalized = key(line['text'])
        translation = choices[normalized].popleft() if choices[normalized] else repeated.get(normalized, '')
        if translation:
            line['translation'] = translation
            count += 1
    return count


def attach_by_time(lines, translations):
    """Only pair timestamps in the same provider's version; never apply its clock to live audio."""
    for line in lines:
        candidates = [t for t in translations if abs(t['start']-line['start']) <= .35 and t['text'].strip()]
        if candidates:
            closest = min(candidates, key=lambda t: abs(t['start']-line['start']))
            if key(closest['text']) != key(line['text']):
                line['translation'] = closest['text']
    return lines
