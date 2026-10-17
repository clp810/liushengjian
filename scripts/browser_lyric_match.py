"""Real lyric lookup and local-model acceptance, isolated from the user's library.
Online candidate text is reviewed, then replaced by our synthesized fixture text for alignment.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

import httpx
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
BASE='http://127.0.0.1:8769'
RESULTS=ROOT/'test-results'
TEXT='The evening light is falling softly.\nI keep this song inside my heart.\nWe listen as the world grows quiet.'


def ready(tid):
    for _ in range(150):
        track=next(t for t in httpx.get(BASE+'/api/tracks').json() if t['id']==tid)
        if track['status']=='error':raise AssertionError(track['error'])
        if track['status']=='ready':return track
        time.sleep(.1)
    raise AssertionError('import timed out')


def open_match(page):
    page.mouse.move(200,200)
    page.locator('#room-settings-toggle').click()
    page.locator('#lyrics-settings').click()
    page.locator('#match-lyrics').click()


def run():
    RESULTS.mkdir(exist_ok=True);errors=[]
    with tempfile.TemporaryDirectory(prefix='lsj-match-') as tmp:
        tmp=Path(tmp);wav=tmp/'voice.wav'
        subprocess.run(['say','-v','Samantha','-r','130','-o',str(tmp/'voice.aiff'),TEXT.replace('\n',' ')],check=True)
        subprocess.run(['ffmpeg','-v','error','-i',str(tmp/'voice.aiff'),'-ar','16000','-ac','1',str(wav)],check=True)
        env={**os.environ,'LIUSHENGJIAN_DATA':str(tmp/'data'),'LIUSHENGJIAN_SEED':'0'}
        with (RESULTS/'match-server.log').open('w') as log:
            server=subprocess.Popen([sys.executable,'-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8769','--log-level','warning'],cwd=ROOT,env=env,stdout=log,stderr=log)
            try:
                for _ in range(80):
                    try:
                        if httpx.get(BASE+'/api/status',timeout=.3).status_code==200:break
                    except httpx.HTTPError:time.sleep(.1)
                assert Path(httpx.get(BASE+'/api/status').json()['storage']).resolve()==(tmp/'data').resolve()
                response=httpx.post(BASE+'/api/imports/file',files={'file':('voice.wav',wav.read_bytes())},data={'cover_style':'groove'})
                tid=response.json()['track_id'];ready(tid)
                with sync_playwright() as p:
                    browser=p.chromium.launch(channel='chrome',headless=True)
                    page=browser.new_page(viewport={'width':1440,'height':1000});page.on('pageerror',lambda e:errors.append(str(e)))
                    # Trace only this page's alignment requests for failure diagnosis.
                    requests=[]
                    page.on('request',lambda r:requests.append(r.method+' '+r.url) if '/lyrics/alignment' in r.url else None)
                    page.goto(BASE,wait_until='networkidle')
                    assert page.locator('[data-view=archive]').count()==0
                    page.locator('.inspect-cover').click();page.locator('#focus-listen').click();page.locator('#room-play').click()
                    page.locator('#room-fullscreen').click()
                    page.wait_for_function('() => document.fullscreenElement?.id === "room-view"')
                    open_match(page)
                    page.locator('#match-dialog').wait_for(state='visible')
                    assert page.evaluate('document.fullscreenElement.contains(document.querySelector("#match-dialog"))')
                    page.evaluate('document.exitFullscreen()')
                    page.locator('#match-query').fill('丸ノ内サディスティック 椎名林檎')
                    page.locator('#match-search-form button').click()
                    page.locator('.match-candidate').first.wait_for(timeout=25000)
                    assert page.locator('.match-candidate').count()>0
                    page.screenshot(path=str(RESULTS/'v05-search.png'),animations='disabled')
                    with page.expect_response(lambda r:'/api/lyrics/candidates/' in r.url) as candidate_response:
                        page.locator('.match-candidate').first.click()
                    candidate=candidate_response.value.json()
                    assert candidate['translations']
                    page.locator('#match-text').wait_for(state='visible')
                    assert len(page.locator('#match-text').input_value())>20
                    # Known fixture words test alignment without confusing song lyrics with spoken content.
                    page.locator('#match-text').fill(TEXT);page.locator('#match-language').select_option('en')
                    page.locator('#match-start').click()
                    page.locator('#match-progress').wait_for(state='visible')
                    page.locator('#match-cancel-task').click()
                    page.wait_for_function('() => document.querySelector("#match-error").textContent.includes("已取消")')
                    assert httpx.get(BASE+f'/api/tracks/{tid}/lyrics').json()['lines']==[]
                    page.locator('#match-start').click();page.locator('#match-progress').wait_for(state='visible')
                    page.locator('#match-close').click();open_match(page)
                    try:
                        page.locator('#match-review-panel').wait_for(state='visible',timeout=180000)
                    except Exception:
                        page.screenshot(path=str(RESULTS/'v05-match-failure.png'))
                        (RESULTS/'match-ui-failure.txt').write_text(page.locator('#match-dialog').inner_text()+'\n'+str(errors)+'\n'+'\n'.join(requests))
                        raise
                    rows=page.locator('.match-line');assert rows.count()==3
                    assert page.locator('.line-words').nth(0).input_value()==TEXT.splitlines()[0]
                    job=httpx.get(BASE+f'/api/tracks/{tid}/lyrics/alignment').json()['task']
                    assert job['status']=='done'
                    assert 1.5 < job['lines'][0]['end'] < 3.5 and 4 < job['lines'][2]['start'] < 6
                    assert httpx.get(BASE+f'/api/tracks/{tid}/lyrics').json()['lines']==[]
                    page.screenshot(path=str(RESULTS/'v05-alignment-review.png'),animations='disabled')
                    rows.nth(1).locator('button').first.click()
                    page.wait_for_function('() => document.querySelector("#match-preview-audio").currentTime>2.5')
                    assert page.evaluate('audio.paused && document.querySelector("#preview-audio").paused')
                    page.locator('#match-preview-audio').evaluate('(e)=>e.pause()')
                    old=rows.nth(0).locator('[data-time=end]').input_value()
                    rows.nth(0).locator('[data-time=end]').fill('0')
                    page.locator('#match-apply').click()
                    assert rows.nth(0).get_attribute('class').endswith('invalid')
                    rows.nth(0).locator('[data-time=end]').fill(old)
                    rows.nth(1).locator('.line-translation').fill('把这首歌留在心里。')
                    page.set_viewport_size({'width':390,'height':844})
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                    page.locator('#match-apply').scroll_into_view_if_needed()
                    page.screenshot(path=str(RESULTS/'v05-review-mobile.png'),animations='disabled')
                    page.locator('#match-apply').click();page.locator('#match-dialog').wait_for(state='hidden')
                    assert httpx.get(BASE+f'/api/tracks/{tid}/lyrics').json()['lines'][1]['text']==TEXT.splitlines()[1]
                    page.locator('#room-seek').fill('3')
                    page.wait_for_function('() => document.querySelector(".lyric-line.current")?.textContent.includes("inside my heart")')
                    assert page.locator('#lyric-translation').inner_text()=='把这首歌留在心里。'
                    page.reload(wait_until='networkidle');page.locator('[data-view=room]').click()
                    page.wait_for_function('() => document.querySelector(".lyric-line.current")?.textContent.includes("inside my heart")')
                    assert page.evaluate('audio.paused')
                    open_match(page);page.locator('#match-paste').click();page.locator('#match-text').fill(TEXT)
                    page.locator('#match-start').click();page.locator('#match-progress').wait_for(state='visible')
                    page.locator('#match-cancel-task').click()
                    page.wait_for_function('() => document.querySelector("#match-error").textContent.includes("已取消")')
                    assert httpx.get(BASE+f'/api/tracks/{tid}/lyrics').json()['lines'][0]['text']==TEXT.splitlines()[0]
                    # Populate a temporary saved timeline with real provider text, then add Chinese
                    # through the visible lookup UI. This fixture is not an accuracy test for singing.
                    pairs=candidate['translations'][:2]
                    page.locator('#match-close').click()
                    lrc='\n'.join(f'[00:{1+i*3:02d}]{pair["text"]}' for i,pair in enumerate(pairs))
                    assert httpx.post(BASE+f'/api/tracks/{tid}/lyrics',files={'file':('translation-check.lrc',lrc.encode())}).status_code==200
                    page.reload(wait_until='networkidle');page.locator('[data-view=room]').click();open_match(page)
                    page.locator('#match-back-search').click()
                    page.locator('#match-query').fill('丸ノ内サディスティック 椎名林檎')
                    page.locator('#match-search-form button').click();page.locator('.match-candidate').first.click()
                    page.locator('#match-translations summary').click()
                    page.locator('#match-translation-apply').click();page.locator('#match-dialog').wait_for(state='hidden')
                    translated=httpx.get(BASE+f'/api/tracks/{tid}/lyrics').json()
                    assert [l['start'] for l in translated['lines']]==[1,4]
                    assert [l['translation'] for l in translated['lines']]==[p['translation'] for p in pairs]
                    page.locator('#room-seek').fill('2')
                    page.wait_for_function('(text) => document.querySelector("#lyric-translation").textContent===text',arg=pairs[0]['translation'])
                    assert page.locator('#lyric-translation').is_visible()
                    assert not errors,errors
                    browser.close()
                report={'real_netease_search_and_lyrics':True,'real_local_whisper_small_alignment':True,'fixture':'synthesized English speech, not singing-quality benchmark','cancel_and_preserve_saved':True,'close_reopen_background_draft':True,'preview_audio_exclusive':True,'invalid_timing_rejected':True,'explicit_apply_and_reload':True,'mobile_no_overflow':True,'fullscreen_dialog_accessible':True,'archive_controls_removed':True,'real_online_chinese_fill_preserves_timing':True,'browser_errors':errors}
                (RESULTS/'match-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
                print('PASS: Online lookup, local AI alignment, cancel, review, save and paused reload',flush=True)
            except Exception:
                # Retain only this isolated fixture's diagnostics, never user library data.
                shutil.copytree(tmp/'data',RESULTS/'match-failure-data',dirs_exist_ok=True)
                try:
                    (RESULTS/'match-failure.json').write_text(json.dumps(httpx.get(BASE+f'/api/tracks/{tid}/lyrics/alignment').json(),ensure_ascii=False,indent=2))
                except (httpx.HTTPError, UnboundLocalError): pass
                raise
            finally:
                server.terminate()
                try:server.wait(timeout=15)
                except subprocess.TimeoutExpired:server.kill();server.wait()


if __name__=='__main__':run()
