"""Translated lines retain the performance timeline, never the provider's album clock."""
import httpx
from fastapi.testclient import TestClient
from app.lyric_sources import LyricSources
from app.main import create_app
from app.store import Store
from app.translations import attach_by_text


def test_provider_translation_is_paired_with_original_not_array_position():
    def respond(request):
        if 'search/get' in request.url.path:
            return httpx.Response(200,json={'code':200,'result':{'songs':[{'id':2,'name':'Hello','artists':[{'name':'Singer'}],'duration':30000}]}})
        return httpx.Response(200,json={'lrc':{'lyric':'[00:02]Hello\n[00:05]World\n[00:08]Again'},'tlyric':{'lyric':'[00:05.1]世界\n[00:08]再一次'}})
    sources=LyricSources(httpx.MockTransport(respond))
    item=sources.search('netease','Hello',{})[0]
    result=sources.fetch(item['token'])
    assert result['translations']==[{'text':'World','translation':'世界'},{'text':'Again','translation':'再一次'}]
    # Different live timing/order, omitted text, fullwidth punctuation and repeated chorus.
    lines=[dict(start=12,text='Ａｇａｉｎ!'),dict(start=17,text='changed'),dict(start=25,text='World'),dict(start=30,text='Again')]
    assert attach_by_text(lines,result['translations'])==3
    assert [l['start'] for l in lines]==[12,17,25,30]
    assert 'translation' not in lines[1]
    assert lines[2]['translation']=='世界'


def test_translation_edit_preserves_word_timing_and_conflicts(tmp_path):
    store=Store(tmp_path/'library')
    audio=store.path('original.m4a');audio.write_bytes(b'original audio')
    tid=store.new_track(status='ready',source_duration=30,duration=30,source_path='original.m4a')['id']
    with TestClient(create_app(store.root,seed=False)) as client:
        url=f'/api/tracks/{tid}/lyrics'
        imported=client.post(url,files={'file':('test.lrc',b'[00:02]<00:02>Hello <00:03>world<00:04>\n[00:05]Again')}).json()
        original_path=store.path(store.get('tracks',tid)['lyrics_path'])
        result=client.put(url+'/translations',json={'revision':imported['revision'],'translations':['你好，世界','再一次']})
        assert result.status_code==200
        saved=client.get(url).json()
        assert saved['lines'][0]['translation']=='你好，世界'
        assert saved['lines'][0]['words']==imported['lines'][0]['words']
        assert [(l['start'],l['end']) for l in saved['lines']]==[(l['start'],l['end']) for l in imported['lines']]
        assert saved['settings']==imported['settings'] and original_path.exists() and audio.read_bytes()==b'original audio'
        assert client.put(url+'/translations',json={'revision':imported['revision'],'translations':['stale','stale']}).status_code==409
        assert client.put(url+'/translations',json={'revision':saved['revision'],'translations':['wrong count']}).status_code==422
        assert client.put(url+'/translations',json={'revision':saved['revision'],'translations':['x'*1001,'']}).status_code==422
        assert client.get(url).json()==saved


def test_fill_existing_translation_keeps_offsets_and_requires_matching_text(tmp_path,monkeypatch):
    store=Store(tmp_path/'library')
    tid=store.new_track(status='ready',source_duration=30,duration=30)['id']
    with TestClient(create_app(store.root,seed=False)) as client:
        url=f'/api/tracks/{tid}/lyrics'
        client.post(url,files={'file':('live.lrc',b'[00:12]Hello\n[00:18]World')})
        saved=client.put(url+'/settings',json={'offset':2,'clock':'source','mood':'calm'}).json()
        monkeypatch.setattr(client.app.state.lyric_workflow.sources,'fetch',lambda token:{'source':'test','translations':[{'text':'World','translation':'世界'}]})
        result=client.post(url+'/translations/match',json={'token':'candidate','revision':saved['revision']})
        assert result.status_code==200 and result.json()['matched']==1
        new=client.get(url).json()
        assert new['lines'][1]['translation']=='世界' and new['lines'][1]['start']==18 and new['settings']==saved['settings']
        assert client.post(url+'/translations/match',json={'token':'candidate','revision':saved['revision']}).status_code==409
        monkeypatch.setattr(client.app.state.lyric_workflow.sources,'fetch',lambda token:{'source':'test','translations':[{'text':'unrelated','translation':'无关'}]})
        assert client.post(url+'/translations/match',json={'token':'candidate','revision':new['revision']}).status_code==422
        assert client.get(url).json()==new
