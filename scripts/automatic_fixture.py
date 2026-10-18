"""Isolated browser-test factory: synthetic source responses, actual local speech inference."""
import os
from pathlib import Path
import httpx
from app.main import create_app as application
from app.lyric_sources import LyricSources

TEXT='The evening light is falling softly.\nI keep this song inside my heart.\nWe listen as the world grows quiet.'


def create_app():
    assert os.environ.get('LSJ_AUTOMATIC_TEST')=='1'
    root=Path(os.environ['LIUSHENGJIAN_DATA']).resolve()
    assert root!=Path('data').resolve() and root.parent.name.startswith('lsj-auto-browser-')
    app=application(root,seed=False)
    def response(request):
        if 'search/get' in request.url.path:
            query=request.url.params.get('s','')
            name='Uncertain' if 'Uncertain' in query else 'Evening Light'
            songs=[] if 'Unknown' in query else [dict(id=1,name=name,artists=[{'name':'Test Singer'}],album={'name':'Test only'},duration=9000)]
            return httpx.Response(200,json={'code':200,'result':{'songs':songs}})
        lrc='\n'.join(f'[00:{1+i*3:02d}]{text}' for i,text in enumerate(TEXT.splitlines()))
        return httpx.Response(200,json={'lrc':{'lyric':lrc},'tlyric':{'lyric':'[00:01]暮色轻轻落下。\n[00:04]把这首歌留在心里。\n[00:07]世界安静下来，我们继续听。'}})
    app.state.lyric_workflow.sources=LyricSources(httpx.MockTransport(response))
    original=app.state.automatic_lyrics.infer
    def infer(job,track,*args):
        if track['title']=='Uncertain':return dict(strategy='text_only',coverage=0,segments=[])
        return original(job,track,*args)
    app.state.automatic_lyrics.infer=infer
    return app
