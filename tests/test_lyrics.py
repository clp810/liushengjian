"""Lyric timing, validation and persistence never touch a real library."""
import pytest
from fastapi.testclient import TestClient

from app.lyrics import parse_lrc, MAX_BYTES
from app.main import create_app
from app.store import Store


def parse(text):
    return parse_lrc(text.encode('utf-8'))


def test_fraction_repeat_offset_translation_and_blank_cues():
    doc = parse('\ufeff[ar:someone]\n[offset:250]\n[00:01.5][00:09.250]First\n[00:03.12]第二句\n[00:03.12]Translation\n[00:05.000]\n[00:06]After')
    assert [r['start'] for r in doc['lines']] == [1.25, 2.87, 4.75, 5.75, 9.0]
    assert doc['lines'][1]['text'] == '第二句\nTranslation'
    assert doc['lines'][2]['text'] == ''
    assert doc['file_offset'] == .25
    assert not doc['timed_words']


def test_enhanced_words_keep_real_timing_and_end_marker():
    rows = parse('[00:01]<00:01>你<00:02.50>好 <00:04>世界<00:05>\n[00:08]下一句')['lines']
    assert rows[0]['words'] == [dict(text='你', start=1, end=2.5), dict(text='好 ', start=2.5, end=4), dict(text='世界', start=4, end=5)]
    assert rows[0]['end'] == 5
    rows = parse('[00:01][00:10]<00:01>A<00:02>B<00:03>')['lines']
    assert rows[1]['words'][0]['start'] == 10
    assert rows[1]['end'] == 12


@pytest.mark.parametrize('text', ['just text', '[00:99]Bad', '[00:01]<00:00>Early', '[00:01]<00:02>A<00:01>B', '[00:01]<00:03>A\n[00:02]Overlap', '[offset:600001]\n[00:01]Bad', '[00:01]' + 'x'*501])
def test_reject_bad_timing_and_excessive_inputs(text):
    with pytest.raises(ValueError): parse(text)


def test_encoding_and_html_stays_inert_text():
    source = '[00:01]<img src=x onerror=alert(1)>你好'
    for encoding in ['utf-8-sig', 'utf-16', 'gb18030']:
        assert parse_lrc(source.encode(encoding))['lines'][0]['text'] == '<img src=x onerror=alert(1)>你好'
    with pytest.raises(ValueError): parse_lrc(b'x'*(MAX_BYTES+1))


def test_lyric_api_atomic_replacement_settings_and_restart(tmp_path):
    root=tmp_path/'library'
    store=Store(root)
    track=store.new_track(title='Existing collection', status='ready', revision='unchanged', trim_start=10, duration=20)
    tid=track['id']; route=f'/api/tracks/{tid}/lyrics'
    with TestClient(create_app(root, seed=False)) as client:
        assert client.get(route).json()['lines']==[]
        assert client.post(route, files={'file':('words.txt',b'[00:10]hello')}).status_code==422
        assert client.post(route, files={'file':('words.lrc',b'x'*(MAX_BYTES+1))}).status_code==413
        content='[00:10]雨后的房间\n[00:12]<00:12>阳光<00:13>微笑<00:14>'
        assert client.post(route, files={'file':('words.lrc',content.encode())}).status_code==200
        stored=store.get('tracks',tid); old_path=store.path(stored['lyrics_path'])
        assert old_path.read_text()==content
        assert client.post(route, files={'file':('broken.lrc',b'broken')}).status_code==422
        assert client.get(route).json()['lines'][0]['text']=='雨后的房间'
        assert client.put(route+'/settings',json={'offset':2.25,'clock':'clip','mood':'bright'}).status_code==200
        for body in [{'offset':601},{'clock':'unknown'},{'mood':'unknown'},{'offset':'NaN'},{'offset':0,'extra':1}]:
            assert client.put(route+'/settings',json=body).status_code==422
        assert client.get('/api/tracks').json()[0]['has_lyrics']
        assert 'lyrics' not in client.get('/api/tracks').json()[0]
        assert client.get('/api/tracks').json()[0]['revision']=='unchanged'
        assert client.post(route,files={'file':('new.lrc',b'[00:01]Replacement')}).status_code==200
        assert old_path.is_file()  # Prior imported lyrics and media are not deleted.
        assert client.get('/api/tracks/nope/lyrics').status_code==404
    with TestClient(create_app(root,seed=False)) as client:
        result=client.get(route).json()
        assert result['settings']==dict(offset=2.25,clock='clip',mood='bright')
        assert result['lines'][0]['text']=='Replacement'


def test_long_phrase_does_not_get_an_invented_eight_second_cutoff():
    rows = parse('[00:00]A long phrase\n[00:20]Next')['lines']
    assert rows[0]['end'] == 20
    rows = parse('[00:00]<00:00>Long<00:12>held note')['lines']
    assert rows[0]['words'][-1]['end'] >= 14


def test_empty_word_markers_and_explicit_long_final_end():
    row = parse('[00:00]<00:00><00:01>Held note<00:20>')['lines'][0]
    assert row['words'] == [dict(text='Held note', start=1, end=20)]
    assert row['end'] == 20
