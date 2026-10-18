"""Automatic UX acceptance in an isolated library; no human timing correction or Apply action."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import httpx
from playwright.sync_api import sync_playwright
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.automatic_fixture import TEXT

ROOT=Path(__file__).resolve().parents[1];BASE='http://127.0.0.1:8770';RESULTS=ROOT/'test-results'


def ready(tid):
    for _ in range(120):
        track=next(t for t in httpx.get(BASE+'/api/tracks').json() if t['id']==tid)
        if track['status']=='ready':return track
        if track['status']=='error':raise AssertionError(track['error'])
        time.sleep(.1)
    raise AssertionError('fixture import timeout')


def run():
    RESULTS.mkdir(exist_ok=True);errors=[]
    with tempfile.TemporaryDirectory(prefix='lsj-auto-browser-') as tmp:
        tmp=Path(tmp);wav=tmp/'voice.wav'
        subprocess.run(['say','-v','Samantha','-r','130','-o',str(tmp/'voice.aiff'),TEXT.replace('\n',' ')],check=True)
        subprocess.run(['ffmpeg','-v','error','-i',str(tmp/'voice.aiff'),'-ar','16000','-ac','1',str(wav)],check=True)
        env={**os.environ,'LSJ_AUTOMATIC_TEST':'1','LIUSHENGJIAN_DATA':str(tmp/'data'),'LIUSHENGJIAN_SEED':'0'}
        with (RESULTS/'auto-server.log').open('w') as log:
            server=subprocess.Popen([sys.executable,'-m','uvicorn','scripts.automatic_fixture:create_app','--factory','--host','127.0.0.1','--port','8770','--log-level','warning'],cwd=ROOT,env=env,stdout=log,stderr=log)
            try:
                for _ in range(80):
                    try:
                        if httpx.get(BASE+'/api/status',timeout=.3).status_code==200:break
                    except httpx.HTTPError:time.sleep(.1)
                assert Path(httpx.get(BASE+'/api/status').json()['storage']).resolve()==(tmp/'data').resolve()
                ids={}
                for title in ['Evening Light','Uncertain','Unknown']:
                    result=httpx.post(BASE+'/api/imports/file',files={'file':(title+'.wav',wav.read_bytes())}).json()
                    tid=result['track_id'];ready(tid)
                    httpx.put(BASE+f'/api/tracks/{tid}',json={'title':title,'artist':'Test Singer'});ready(tid);ids[title]=tid
                with sync_playwright() as p:
                    browser=p.chromium.launch(channel='chrome',headless=True)
                    page=browser.new_page(viewport={'width':1440,'height':1000});page.on('pageerror',lambda e:errors.append(str(e)))
                    page.goto(BASE,wait_until='networkidle')
                    page.locator(f'[data-id="{ids["Evening Light"]}"] .inspect-cover').click();page.locator('#focus-listen').click();page.locator('#room-play').click()
                    # Listening is the only trigger; no source/locale/timing inputs or approval step.
                    page.wait_for_function('() => document.querySelector("#room-lyrics-status").textContent.includes("准备")')
                    page.locator('#room-settings-toggle').click();page.locator('#lyrics-settings').click()
                    page.locator('#auto-activity').wait_for(state='visible')
                    assert not page.locator('#match-dialog').is_visible() and not page.locator('#lyrics-dialog').is_visible()
                    assert not page.locator('#auto-query').is_visible()
                    page.screenshot(path=str(RESULTS/'v07-automatic-progress.png'))
                    page.locator('#auto-dismiss').click()
                    for _ in range(120):
                        status=httpx.get(BASE+f'/api/tracks/{ids["Evening Light"]}/lyrics/auto').json()
                        if status['status'] not in ('running','queued'):break
                        time.sleep(1)
                    assert status['status']=='ready',status
                    doc=httpx.get(BASE+f'/api/tracks/{ids["Evening Light"]}/lyrics').json()
                    assert len(doc['lines'])==3 and all(l.get('translation') for l in doc['lines'])
                    page.locator('#room-seek').fill('3')
                    page.wait_for_function('() => document.querySelector("#lyric-translation").textContent==="把这首歌留在心里。"')
                    # Verify actual rendered backgrounds and persisted preference.
                    page.locator('#room-settings-toggle').click()
                    images=[]
                    for theme in ['warm','night','forest']:
                        page.locator(f'.theme[data-theme="{theme}"]').click()
                        page.screenshot(path=str(RESULTS/f'v07-theme-{theme}.png'),animations='disabled')
                        images.append(page.locator('.room-backdrop').screenshot(animations='disabled'))
                    assert len(set(images))==3
                    page.locator('#ambient-toggle').click()
                    page.wait_for_function('() => getComputedStyle(document.querySelector(".room-wash")).opacity==="0"')
                    assert page.locator('.room-backdrop').evaluate('(e)=>getComputedStyle(e).backgroundImage')=='none'
                    page.screenshot(path=str(RESULTS/'v07-ambient-off.png'),animations='disabled')
                    page.reload(wait_until='networkidle');page.locator('[data-view=room]').click()
                    assert page.locator('#room-view').get_attribute('data-theme')=='forest'
                    assert 'no-ambient' in page.locator('#room-view').get_attribute('class')
                    # Uncertain timing is handled by the application: text survives, no repair table.
                    page.locator('#room-back').click();page.locator(f'[data-id="{ids["Uncertain"]}"] .inspect-cover').click();page.locator('#focus-lyrics').click()
                    page.wait_for_function('() => document.querySelector("#auto-status").textContent.includes("暂时无法可靠同步")')
                    assert not page.locator('#match-lines').is_visible()
                    page.locator('#auto-text summary').click();assert page.locator('#auto-copy small').count()==3
                    assert httpx.get(BASE+f'/api/tracks/{ids["Uncertain"]}/lyrics').json()['lines']==[]
                    page.set_viewport_size({'width':390,'height':844});page.screenshot(path=str(RESULTS/'v07-automatic-text-mobile.png'))
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                    page.locator('#auto-close').click()
                    page.locator(f'[data-id="{ids["Unknown"]}"] .inspect-cover').click();page.locator('#focus-lyrics').click()
                    page.wait_for_function('() => document.querySelector("#auto-status").textContent.includes("没有确认")')
                    assert not page.locator('#match-dialog').is_visible()
                    page.screenshot(path=str(RESULTS/'v07-automatic-unknown-mobile.png'))
                    assert not errors,errors
                    browser.close()
                report=dict(temporary_storage=True,source='synthetic provider transport; real NetEase checked separately',real_local_whisper_inference=True,listen_auto_start=True,auto_save_with_chinese=True,no_manual_apply=True,background_completion=True,uncertain_text_fallback=True,unknown_no_fake_match=True,theme_pixels_differ=True,audio_ambient_toggle=True,persisted_preferences=True,mobile=True,browser_errors=errors)
                (RESULTS/'automatic-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print('PASS: automatic lyrics, Chinese, fallback, themes, ambient and mobile',flush=True)
            finally:
                server.terminate()
                try:server.wait(timeout=15)
                except subprocess.TimeoutExpired:server.kill();server.wait()


if __name__=='__main__':run()
