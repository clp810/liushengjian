"""v0.3 presentation acceptance, using generated media in a disposable library.
The eight named records are test fixtures, never installed in the user's library.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

import httpx
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
BASE = 'http://127.0.0.1:8767'
RESULTS = ROOT / 'test-results'


def ready(tid):
    for _ in range(150):
        track = next(t for t in httpx.get(BASE+'/api/tracks').json() if t['id'] == tid)
        if track['status'] == 'error': raise AssertionError(track['error'])
        if track['status'] == 'ready': return track
        time.sleep(.1)
    raise AssertionError('fixture timed out')


def no_overflow(page):
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'horizontal overflow'


def run():
    RESULTS.mkdir(exist_ok=True)
    errors=[]
    with tempfile.TemporaryDirectory(prefix='lsj-space-') as tmp:
        tmp=Path(tmp)
        # The same tiny original is independently imported, exercising the real API.
        audio=tmp/'fixture.m4a'
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','sine=frequency=440:duration=15','-c:a','aac',str(audio)],check=True)
        env={**os.environ,'LIUSHENGJIAN_DATA':str(tmp/'data'),'LIUSHENGJIAN_SEED':'0'}
        with (RESULTS/'space-server.log').open('w') as out:
            server=subprocess.Popen([sys.executable,'-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8767','--log-level','warning'],cwd=ROOT,env=env,stdout=out,stderr=out)
            try:
                for _ in range(80):
                    try:
                        if httpx.get(BASE+'/api/status',timeout=.3).status_code==200:break
                    except httpx.HTTPError:time.sleep(.1)
                assert Path(httpx.get(BASE+'/api/status').json()['storage']).resolve()==(tmp/'data').resolve(), 'Refusing to test against non-temporary storage'
                ids=[]
                titles=['雨后的房间','Blue Hour','慢慢喜欢你','声音的形状','晚风经过这扇窗','Somewhere, Quiet','月光里的长长长长长长长长长长歌名','<声音 & 记忆>']
                styles=['groove','dusk','collage','type']
                for i,title in enumerate(titles):
                    response=httpx.post(BASE+'/api/imports/file',files={'file':('fixture.m4a',audio.read_bytes())},data={'cover_style':styles[i%4]}).json()
                    tid=response['track_id'];ready(tid)
                    edit=httpx.put(BASE+f'/api/tracks/{tid}',json={'title':title,'artist':'测试演唱者','version':'现场片段' if i%2 else '私人录音','note':'仅用于临时库验收的合成音频。\n封面、笔记与播放器连接真实 API。'})
                    assert edit.status_code==202
                    ready(tid);ids.append(tid)
                with sync_playwright() as p:
                    browser=p.chromium.launch(channel='chrome',headless=True)
                    context=browser.new_context(viewport={'width':1440,'height':1000})
                    page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
                    page.goto(BASE,wait_until='networkidle')
                    page.locator('.track-card').first.wait_for();assert page.locator('.track-card').count()==8
                    no_overflow(page)
                    assert page.locator('.sidebar').evaluate('(e)=>getComputedStyle(e).backdropFilter')!='none'
                    # Pointer lighting and perspective are limited to the active record.
                    card=page.locator('.track-card').first
                    card.hover(position={'x':30,'y':70})
                    page.wait_for_function('() => !!document.querySelector(".track-card").style.getPropertyValue("--rx")')
                    page.mouse.move(5,5)
                    page.screenshot(path=str(RESULTS/'v03-wall-desktop.png'),full_page=True,animations="disabled")
                    inspect=card.locator('.inspect-cover')
                    inspect.focus();page.keyboard.press('ArrowRight')
                    page.wait_for_function('() => document.querySelectorAll(".inspect-cover")[1]===document.activeElement')
                    page.keyboard.press('Enter');page.locator('#record-focus').wait_for(state='visible')
                    assert page.locator('#focus-note').inner_text().startswith('仅用于临时库')
                    page.locator('#focus-heart').click()
                    page.wait_for_function('() => document.querySelector("#focus-heart").getAttribute("aria-pressed")==="true"')
                    page.screenshot(path=str(RESULTS/'v03-focus-desktop.png'),full_page=True,animations="disabled")
                    page.keyboard.press('Escape')
                    page.locator('#record-focus').wait_for(state='hidden')
                    page.wait_for_function('() => document.querySelectorAll(".inspect-cover")[1]===document.activeElement')
                    # Immersion and fullscreen preserve global controls; Escape leaves immersion.
                    page.locator('#wall-immerse').click()
                    assert not page.locator('.sidebar').is_visible()
                    page.locator('#wall-fullscreen').click()
                    page.wait_for_function('() => document.fullscreenElement===document.documentElement')
                    assert page.locator('#play-toggle').is_visible()
                    page.locator('#wall-fullscreen').click()
                    page.keyboard.press('Escape');assert page.locator('.sidebar').is_visible()
                    page.locator('#wall-depth').click()
                    assert page.locator('body').get_attribute('data-wall')=='flat'
                    page.wait_for_function('() => getComputedStyle(document.querySelector(".track-card")).transform==="none"')
                    page.reload(wait_until='networkidle')
                    assert page.locator('body').get_attribute('data-wall')=='flat'
                    page.locator('#wall-depth').click()
                    # Real playback through focus details, then restore paused at the same time.
                    page.locator('.inspect-cover').first.click()
                    page.evaluate('document.querySelector("#audio").muted=true')
                    page.locator('#focus-listen').click()
                    page.wait_for_function('() => document.querySelector("#audio").currentTime>.2')
                    page.locator('#room-play').click()
                    page.locator('#room-seek').fill('4')
                    page.locator('#room-volume').fill('0.25')
                    page.screenshot(path=str(RESULTS/'v03-room.png'),full_page=True,animations="disabled")
                    page.reload(wait_until='networkidle')
                    page.locator('#resume-banner').wait_for(state='visible')
                    page.wait_for_function('() => Math.abs(document.querySelector("#audio").currentTime-4)<.3')
                    assert page.locator('#audio').evaluate('(e)=>e.paused && e.volume===.25')
                    page.locator('#resume-play').click()
                    page.wait_for_function('() => !document.querySelector("#audio").paused')
                    page.locator('[data-view=room]').click()
                    page.locator('#room-fullscreen').click()
                    page.wait_for_function('() => document.fullscreenElement?.id==="room-view"')
                    page.locator('#room-title').click();page.mouse.move(5,5)
                    page.wait_for_function('() => document.querySelector("#room-view").classList.contains("quiet")',timeout=7000)
                    page.mouse.move(200,200)
                    page.wait_for_function('() => !document.querySelector("#room-view").classList.contains("quiet")')
                    page.locator('#room-fullscreen').click()
                    page.locator('#room-play').click()
                    page.locator('#room-back').click()
                    page.locator('#search').fill('does not exist')
                    page.locator('#empty-state').wait_for(state='visible');no_overflow(page)
                    page.locator('#search').fill('')
                    # Narrow viewport: every action remains reachable without hover.
                    page.set_viewport_size({'width':390,'height':844});no_overflow(page)
                    page.screenshot(path=str(RESULTS/'v03-wall-mobile.png'),full_page=True,animations="disabled")
                    page.locator('.inspect-cover').first.click()
                    page.locator('#record-focus').wait_for(state='visible');no_overflow(page)
                    page.screenshot(path=str(RESULTS/'v03-focus-mobile.png'),full_page=True,animations="disabled")
                    page.locator('#focus-edit').click()
                    page.locator('#edit-dialog').wait_for(state='visible');no_overflow(page)
                    page.locator('#edit-dialog .dialog-heading .close-dialog').click()
                    page.locator('.card-menu summary').first.click()
                    page.locator('.card-menu[open] [data-action=export]').click()
                    page.locator('#export-dialog').wait_for(state='visible');no_overflow(page)
                    page.locator('#export-dialog .close-dialog').click()
                    page.emulate_media(reduced_motion='reduce')
                    assert page.locator('.track-card').first.evaluate('(e)=>getComputedStyle(e).transform')=='none'
                    assert not errors,errors
                    browser.close()
                report={'fixtures':'8 synthetic records in disposable storage; never user data','glass_and_perspective':True,'focus_keyboard_and_favorite':True,'immersion_and_fullscreen':True,'flat_mode_persistence':True,'paused_resume_and_volume':True,'fullscreen_idle_controls':True,'search_empty':True,'mobile_actions_and_no_overflow':True,'reduced_motion':True,'browser_errors':errors}
                (RESULTS/'space-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
                print('PASS: v0.3 spatial wall, focus, keyboard, fullscreen, resume, mobile and reduced-motion checks',flush=True)
            finally:
                server.terminate()
                try:server.wait(timeout=15)
                except subprocess.TimeoutExpired:server.kill();server.wait()


if __name__=='__main__':run()
