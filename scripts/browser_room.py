"""Listening-room acceptance with synthetic media and lyrics in a disposable library."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

import httpx
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
BASE='http://127.0.0.1:8768'
RESULTS=ROOT/'test-results'


def ready(tid):
    for _ in range(200):
        track=next(t for t in httpx.get(BASE+'/api/tracks').json() if t['id']==tid)
        if track['status']=='error': raise AssertionError(track['error'])
        if track['status']=='ready': return track
        time.sleep(.1)
    raise AssertionError('fixture timed out')


def run():
    RESULTS.mkdir(exist_ok=True)
    errors=[]
    with tempfile.TemporaryDirectory(prefix='lsj-room-') as tmp:
        tmp=Path(tmp);video=tmp/'fixture.mp4'
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=size=640x360:rate=24:duration=30','-f','lavfi','-i','sine=frequency=330:duration=30','-c:v','libx264','-preset','ultrafast','-pix_fmt','yuv420p','-c:a','aac','-shortest',str(video)],check=True)
        lrc=tmp/'words.lrc'
        lrc.write_text('[00:10]晚风停在窗边\n[00:12]<00:12>阳光 <00:13>落在 <00:14>你的微笑<00:16>\n[00:16]<00:16>燃烧 <00:18>向前奔跑<00:20>\n[00:20]\n[00:22]把这一刻留给声音\n[00:25]<script>alert(1)</script>')
        env={**os.environ,'LIUSHENGJIAN_DATA':str(tmp/'data'),'LIUSHENGJIAN_SEED':'0'}
        with (RESULTS/'room-server.log').open('w') as log:
            server=subprocess.Popen([sys.executable,'-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8768','--log-level','warning'],cwd=ROOT,env=env,stdout=log,stderr=log)
            try:
                for _ in range(80):
                    try:
                        if httpx.get(BASE+'/api/status',timeout=.3).status_code==200:break
                    except httpx.HTTPError:time.sleep(.1)
                assert Path(httpx.get(BASE+'/api/status').json()['storage']).resolve()==(tmp/'data').resolve(), 'Refusing to test real storage'
                result=httpx.post(BASE+'/api/imports/file',files={'file':('fixture.mp4',video.read_bytes())},data={'keep_video':'true','cover_style':'dusk'}).json()
                tid=result['track_id'];ready(tid)
                assert httpx.put(BASE+f'/api/tracks/{tid}',json={'title':'晚风停在窗边','artist':'留声间 · 测试音源','version':'现场片段','note':'这一刻，留给声音。','trim_start':10,'trim_end':26}).status_code==202
                ready(tid)
                with sync_playwright() as p:
                    browser=p.chromium.launch(channel='chrome',headless=True)
                    context=browser.new_context(viewport={'width':1440,'height':1000})
                    page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
                    page.goto(BASE,wait_until='networkidle')
                    page.locator('.inspect-cover').click();page.locator('#focus-listen').click()
                    page.wait_for_function('() => !audio.paused')
                    page.locator('#room-play').click();page.locator('#room-seek').fill('3.5')
                    page.locator('#lyrics-settings').click()
                    page.locator('#lyrics-file').set_input_files(str(lrc))
                    page.wait_for_function('() => document.querySelector("#lyrics-filename").textContent.includes("6 句")')
                    page.locator('#lyrics-cancel').click()
                    page.wait_for_function('() => document.querySelector(".lyric-line.current")?.textContent==="阳光 落在 你的微笑"')
                    assert page.locator('#room-view').get_attribute('data-mood')=='bright'
                    assert page.locator('.lyric-word').nth(0).evaluate('(e)=>e.style.getPropertyValue("--progress")')=='100.0%'
                    assert page.locator('.lyric-word').nth(1).evaluate('(e)=>parseFloat(e.style.getPropertyValue("--progress"))')>40
                    assert page.evaluate('audio.paused')
                    page.screenshot(path=str(RESULTS/'v04-lyrics-desktop.png'),full_page=True,animations='disabled')
                    page.locator('#room-seek').fill('7')
                    page.wait_for_function('() => document.querySelector("#room-view").dataset.mood==="intense"')
                    page.locator('#room-seek').fill('11')
                    page.wait_for_function('() => document.querySelector(".lyric-interlude")?.textContent==="间奏"')
                    page.locator('#room-seek').fill('3.5')
                    page.locator('#lyrics-settings').click()
                    page.locator('#lyric-clock').select_option('clip')
                    page.locator('#lyric-offset').fill('-10')
                    page.locator('#lyric-mood').select_option('calm')
                    page.locator('#lyrics-form button[type=submit]').click()
                    page.locator('#lyrics-dialog').wait_for(state='hidden')
                    page.wait_for_function('() => document.querySelector(".lyric-line.current")?.textContent.includes("阳光")')
                    assert page.locator('#room-view').get_attribute('data-mood')=='calm'
                    settings=httpx.get(BASE+f'/api/tracks/{tid}/lyrics').json()['settings']
                    assert settings=={'offset':-10,'clock':'clip','mood':'calm'}
                    # Cancel restores saved settings; anchor alignment uses the chosen clock.
                    page.locator('#lyrics-settings').click();page.locator('#lyric-offset').fill('4');page.locator('#lyrics-cancel').click()
                    page.wait_for_function('() => document.querySelector(".lyric-line.current")?.textContent.includes("阳光")')
                    page.locator('#lyrics-settings').click();page.locator('#lyric-clock').select_option('source')
                    page.locator('#lyric-anchor').select_option('1');page.locator('#lyric-align-now').click()
                    assert abs(float(page.locator('#lyric-offset').input_value())-1.5)<.1
                    page.locator('#lyric-offset').fill('0');page.locator('#lyric-mood').select_option('auto')
                    page.locator('#lyrics-form button[type=submit]').click()
                    page.locator('#lyrics-dialog').wait_for(state='hidden')
                    # Invalid imports keep the previously saved lyrics and show an actionable error.
                    page.locator('#lyrics-settings').click()
                    page.locator('#lyrics-file').set_input_files({'name':'broken.lrc','mimeType':'text/plain','buffer':b'not lrc'})
                    page.wait_for_function('() => document.querySelector("#lyrics-error").textContent.includes("时间标记")')
                    assert len(httpx.get(BASE+f'/api/tracks/{tid}/lyrics').json()['lines'])==6
                    page.locator('#lyrics-cancel').click()
                    page.reload(wait_until='networkidle');page.locator('[data-view=room]').click()
                    page.wait_for_function('() => document.querySelector(".lyric-line.current")?.textContent.includes("阳光")')
                    assert page.evaluate('audio.paused')
                    # Fullscreen settings remain in the fullscreen subtree and can be operated.
                    page.locator('#room-fullscreen').click();page.locator('#lyrics-settings').click()
                    assert page.locator('#lyrics-dialog').is_visible();page.locator('#lyrics-cancel').click()
                    page.locator('#room-fullscreen').click()
                    page.locator('#mode-video').click()
                    page.wait_for_function('() => audio.mode==="video"')
                    assert abs(page.locator('#live-video').evaluate('(e)=>e.currentTime')-13.5)<.2
                    assert page.locator('#room-title').bounding_box()['y'] < page.locator('#live-video').bounding_box()['y']
                    page.locator('#room-play').click()
                    page.wait_for_function('() => document.querySelector("#live-video").currentTime>13.8')
                    page.screenshot(path=str(RESULTS/'v04-live-desktop.png'),full_page=True,animations='disabled')
                    assert page.evaluate('document.querySelector("#audio").paused')
                    assert page.locator('#stage-light').evaluate('(e)=>e.getContext("2d").getImageData(0,0,32,18).data.some(v=>v>0)')
                    page.locator('#room-fullscreen').click()
                    page.wait_for_function('() => document.fullscreenElement?.id==="room-view"')
                    page.wait_for_function('() => {const r=document.querySelector("#live-video").getBoundingClientRect();return Math.abs(r.width-innerWidth)<2&&Math.abs(r.height-innerHeight)<2&&r.x===0&&r.y===0;}')
                    page.locator('#video-fit').click()
                    assert page.locator('#live-video').evaluate('(e)=>getComputedStyle(e).objectFit')=='cover'
                    page.locator('#video-fit').click()
                    assert page.locator('#live-video').evaluate('(e)=>getComputedStyle(e).objectFit')=='contain'
                    page.locator('#live-video').click();page.mouse.move(1,1)
                    page.wait_for_function('() => document.querySelector("#room-view").classList.contains("quiet")',timeout=7000)
                    page.screenshot(path=str(RESULTS/'v04-live-fullscreen.png'),animations='disabled')
                    page.mouse.move(200,200);page.locator('#room-fullscreen').click();page.locator('#room-play').click()
                    page.locator('#mode-audio').click();page.locator('#room-seek').fill('3.5')
                    page.set_viewport_size({'width':390,'height':844})
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                    page.screenshot(path=str(RESULTS/'v04-lyrics-mobile.png'),full_page=True,animations='disabled')
                    page.locator('#lyrics-settings').click();assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                    page.screenshot(path=str(RESULTS/'v04-settings-mobile.png'),full_page=True,animations='disabled');page.locator('#lyrics-cancel').click()
                    page.emulate_media(reduced_motion='reduce')
                    assert page.locator('.lyric-line.current').evaluate('(e)=>getComputedStyle(e).animationName')=='none'
                    assert page.locator('.lyric-word').first.evaluate('(e)=>getComputedStyle(e).transform')=='none'
                    page.locator('#mode-video').click();page.locator('#room-fullscreen').click()
                    page.wait_for_function('() => document.fullscreenElement?.id==="room-view"')
                    assert page.locator('#live-video').bounding_box()['height']==844
                    page.locator('#room-fullscreen').click()
                    assert not errors,errors
                    browser.close()
                report={'temporary_storage':True,'lrc_import':True,'word_timing_and_clip_clock':True,'manual_and_auto_mood':True,'offset_preview_cancel_align_save':True,'invalid_import_preserves_lyrics':True,'paused_reload':True,'video_full_viewport_and_fit':True,'ambient_from_video':True,'fullscreen_idle':True,'mobile_and_reduced_motion':True,'browser_errors':errors}
                (RESULTS/'room-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
                print('PASS: v0.4 lyrics, clocks, moods, persisted settings, video light and fullscreen',flush=True)
            finally:
                server.terminate()
                try:server.wait(timeout=15)
                except subprocess.TimeoutExpired:server.kill();server.wait()


if __name__=='__main__':run()
