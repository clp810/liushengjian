import threading
import time
import pytest
from fastapi.testclient import TestClient
from app.automatic_lyrics import AutoRequest
from app.auto_quality import acceptable,match_transcription
from app.main import create_app
from app.song_identity import identify,choose,language,candidate_score
from app.store import Store


def test_identify_video_title_and_reject_ambiguous_or_remix():
    identity=identify({'title':'椎名林檎 -【丸ノ内サディスティック】東京事変'})
    assert identity=={'title':'丸ノ内サディスティック','artist':'椎名林檎'}
    correct={'title':identity['title'],'artist':'椎名林檎'}
    remix={'title':identity['title']+' (Neetskills Remix)','artist':'椎名林檎'}
    medley={'title':identity['title']+' ～ 歌舞伎町の女王','artist':'椎名林檎'}
    assert choose(identity,[remix,medley,correct])==correct
    assert choose({'title':'Hello','artist':''},[{'title':'Hello','artist':'A'},{'title':'Hello','artist':'B'}]) is None
    assert choose({'title':'Hello','artist':''},[{'title':'Hello','artist':'A'},{'title':'Hello','artist':'A'},{'title':'Hello','artist':'B'}]) is None
    assert candidate_score({'title':'Hello','artist':'A'},{'title':'Hello','artist':''})[2]==0
    assert choose(identity,[{'title':'wrong','artist':'椎名林檎'}]) is None
    assert language('日本語のうた')=='ja' and language('우리 노래')=='ko' and language('一首歌')=='zh'
    assert language('Le temps passe') is None
    assert identify({'title':'【Sabrina Carpenter】「Good Luck, Babe!」（Chappell Roan cover）现场'})=={'title':'Good Luck, Babe!','artist':'Chappell Roan'}
    assert identify({'title':'听过最好听的一版Hype boy翻唱'})['title']=='Hype boy'
    assert identify({'title':'【4K修复】陶喆《Melody》现场'})=={'title':'Melody','artist':'陶喆'}


def test_bad_timings_never_automatically_accepted():
    rows=[dict(start=1,end=2,text='one',confidence=.8),dict(start=3,end=4,text='two',confidence=.9)]
    assert acceptable(rows,2)
    assert not acceptable(rows,3)
    for change in [{'start':3,'end':3},{'start':1.9},{'confidence':.1},{'end':float('nan')}]:
        assert not acceptable([rows[0],{**rows[1],**change}],2)
    assert not acceptable(rows,2,coverage=.5)


def test_transcription_retry_anchors_observed_phrases_without_dividing_time():
    texts=['The evening light is falling softly.','I keep this song inside my heart.']
    segments=[dict(text=texts[0],start=2,end=5,words=[{'probability':.9}]),dict(text=texts[1],start=8,end=12,words=[{'probability':.9}])]
    rows,coverage=match_transcription(segments,texts)
    assert coverage==1 and acceptable(rows,2,coverage)
    assert [(r['start'],r['end']) for r in rows]==[(2,5),(8,12)]
    assert match_transcription([dict(text='please subscribe to this channel',start=2,end=5,words=[{'probability':.9}])],texts)[0]==[]


@pytest.fixture
def setup(tmp_path,monkeypatch):
    store=Store(tmp_path/'data');store.path('source.m4a').write_bytes(b'original')
    tid=store.new_track(title='Song',artist='Singer',revision='a1',source_duration=20,duration=20,status='ready',m4a_path='source.m4a',preview_path='source.m4a')['id']
    with TestClient(create_app(store.root,seed=False)) as client:
        auto=client.app.state.automatic_lyrics
        item=dict(title='Song',artist='Singer',album='Album')
        candidate=dict(text='First line\nSecond line',translations=[{'text':'First line','translation':'第一句'},{'text':'Second line','translation':'第二句'}])
        monkeypatch.setattr(auto,'discover',lambda *args:(item,candidate))
        good=dict(segments=[dict(start=2,end=4,text='First line',confidence=.9),dict(start=5,end=8,text='Second line',confidence=.8)],strategy='alignment',coverage=1)
        monkeypatch.setattr(auto,'infer',lambda *args:good)
        yield client,auto,store,tid,good


def wait(client,tid):
    for _ in range(100):
        result=client.get(f'/api/tracks/{tid}/lyrics/auto').json()
        if result['status'] not in ('queued','running'):return result
        time.sleep(.02)
    raise AssertionError('automatic task did not finish')


def test_automatic_apply_and_idempotence(setup):
    client,auto,store,tid,_=setup;url=f'/api/tracks/{tid}/lyrics'
    assert client.post(url+'/auto',json={}).status_code==202
    result=wait(client,tid)
    assert result['status']=='ready' and result['translated']==2
    doc=client.get(url).json()
    assert doc['lines'][0]['translation']=='第一句' and doc['lines'][0]['start']==2 and doc['settings']['clock']=='source'
    assert client.post(url+'/auto',json={}).json()['id']==result['id']
    assert store.path('source.m4a').read_bytes()==b'original'
    # Manual workflow cannot accidentally render an automatic task's internal result.
    assert client.get(url+'/alignment').json()['task'] is None


def test_bad_result_saves_text_without_fake_timing(setup):
    client,auto,store,tid,result=setup;url=f'/api/tracks/{tid}/lyrics'
    result['segments'][0]['end']=2
    client.post(url+'/auto',json={});assert wait(client,tid)['status']=='text_only'
    doc=client.get(url).json();assert doc['lines']==[] and doc['plain_lines'][0]['translation']=='第一句'


def test_existing_timeline_translation_only_and_failed_retry_preserves(setup):
    client,auto,store,tid,result=setup;url=f'/api/tracks/{tid}/lyrics'
    original=client.post(url,files={'file':('original.lrc',b'[00:01]<00:01>First <00:02>line<00:03>\n[00:07]Second line')}).json()
    client.post(url+'/auto',json={});assert wait(client,tid)['status']=='ready'
    updated=client.get(url).json()
    assert updated['lines'][0]['words']==original['lines'][0]['words'] and updated['lines'][0]['translation']=='第一句'
    result['strategy']='text_only'
    client.post(url+'/auto',json={'retry':True});assert wait(client,tid)['status']=='text_only'
    assert client.get(url).json()==updated


@pytest.mark.parametrize('mode',['cancel','edit'])
def test_cancel_and_stale_auto_cannot_overwrite(setup,monkeypatch,mode):
    client,auto,store,tid,result=setup;url=f'/api/tracks/{tid}/lyrics'
    entered=threading.Event();release=threading.Event()
    def infer(*args):entered.set();release.wait(3);return result
    monkeypatch.setattr(auto,'infer',infer)
    started=client.post(url+'/auto',json={}).json();assert entered.wait(2)
    if mode=='cancel':client.post(url+f'/auto/{started["id"]}/cancel')
    else:client.post(url,files={'file':('new.lrc',b'[00:01]My edit')})
    before=client.get(url).json();release.set()
    # Wait on the actual worker so a late write would be detected.
    auto.workflow.pool.submit(lambda:None).result(timeout=3)
    assert client.get(url).json()==before
    assert wait(client,tid)['status']==('cancelled' if mode=='cancel' else 'conflict')


def test_unknown_song_preserves_data(setup,monkeypatch):
    client,auto,store,tid,result=setup;url=f'/api/tracks/{tid}/lyrics'
    monkeypatch.setattr(auto,'discover',lambda *args:None)
    client.post(url+'/auto',json={});assert wait(client,tid)['status']=='not_found'
    assert client.get(url).json()['lines']==[]
