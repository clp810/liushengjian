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


def open_settings(page):
    page.mouse.move(200,200)
    if not page.locator('#room-settings-panel').is_visible(): page.locator('#room-settings-toggle').click()
    page.locator('#lyrics-settings').click()
    page.locator('#lyrics-dialog').wait_for(state='visible')


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
                    page=context.new_page();page.on('pageerror',lambda e:(errors.append(str(e)),print('BROWSER ERROR:',e,flush=True)))
                    page.goto(BASE,wait_until='networkidle')
                    page.locator('.inspect-cover').click();page.locator('#focus-listen').click()
                    page.wait_for_function('() => !audio.paused')
                    page.locator('#room-play').click();page.locator('#room-seek').fill('3.5')
                    open_settings(page)
                    page.locator('#lyrics-file').set_input_files(str(lrc))
                    try:
                        page.wait_for_function('() => document.querySelector("#lyrics-filename").textContent.includes("6 句")',timeout=5000)
                    except Exception:
                        page.screenshot(path=str(RESULTS/'v06-room-failure.png'))
                        print(page.locator('#lyrics-form').inner_text(),flush=True)
                        raise
                    page.locator('#lyrics-cancel').click()
                    page.wait_for_function('() => document.querySelector(".lyric-line.current")?.textContent==="阳光 落在 你的微笑"')
                    assert page.locator('#room-view').get_attribute('data-mood')=='bright'
                    assert page.locator('.lyric-word').nth(0).evaluate('(e)=>e.style.getPropertyValue("--progress")')=='100.0%'
                    assert page.locator('.lyric-word').nth(1).evaluate('(e)=>parseFloat(e.style.getPropertyValue("--progress"))')>40
                    assert page.evaluate('audio.paused')
                    page.screenshot(path=str(RESULTS/'v06-lyrics-desktop.png'),full_page=True,animations='disabled')
                    page.locator('#room-seek').fill('7')
                    page.wait_for_function('() => document.querySelector("#room-view").dataset.mood==="intense"')
                    page.locator('#room-seek').fill('11')
                    page.wait_for_function('() => document.querySelector(".lyric-interlude")?.textContent==="间奏"')
                    page.locator('#room-seek').fill('3.5')
                    open_settings(page)
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
                    open_settings(page);page.locator('#lyric-offset').fill('4');page.locator('#lyrics-cancel').click()
                    page.wait_for_function('() => document.querySelector(".lyric-line.current")?.textContent.includes("阳光")')
                    open_settings(page);page.locator('#lyric-clock').select_option('source')
                    page.locator('#lyric-anchor').select_option('1');page.locator('#lyric-align-now').click()
                    assert abs(float(page.locator('#lyric-offset').input_value())-1.5)<.1
                    page.locator('#lyric-offset').fill('0');page.locator('#lyric-mood').select_option('auto')
                    page.locator('#lyrics-form button[type=submit]').click()
                    page.locator('#lyrics-dialog').wait_for(state='hidden')
                    # Invalid imports keep the previously saved lyrics and show an actionable error.
                    open_settings(page)
                    page.locator('#lyrics-file').set_input_files({'name':'broken.lrc','mimeType':'text/plain','buffer':b'not lrc'})
                    page.wait_for_function('() => document.querySelector("#lyrics-error").textContent.includes("时间标记")')
                    assert len(httpx.get(BASE+f'/api/tracks/{tid}/lyrics').json()['lines'])==6
                    page.locator('#lyrics-cancel').click()
                    page.reload(wait_until='networkidle');page.locator('[data-view=room]').click()
                    page.wait_for_function('() => document.querySelector(".lyric-line.current")?.textContent.includes("阳光")')
                    assert page.evaluate('audio.paused')
                    # Fullscreen settings remain in the fullscreen subtree and can be operated.
                    page.locator('#room-fullscreen').click();open_settings(page)
                    assert page.locator('#lyrics-dialog').is_visible();page.locator('#lyrics-cancel').click()
                    page.locator('#room-fullscreen').click()
                    page.locator('#mode-video').click()
                    page.wait_for_function('() => audio.mode==="video"')
                    assert abs(page.locator('#live-video').evaluate('(e)=>e.currentTime')-13.5)<.2
                    assert page.locator('#room-title').bounding_box()['y'] < 120
                    assert page.locator('#live-video').bounding_box()['height']==1000
                    page.locator('#room-play').click()
                    page.wait_for_function('() => document.querySelector("#live-video").currentTime>13.8')
                    page.screenshot(path=str(RESULTS/'v06-live-desktop.png'),full_page=True,animations='disabled')
                    assert page.evaluate('document.querySelector("#audio").paused')
                    assert page.locator('#stage-light').evaluate('(e)=>e.getContext("2d").getImageData(0,0,32,18).data.some(v=>v>0)')
                    page.locator('#room-fullscreen').click()
                    page.wait_for_function('() => document.fullscreenElement?.id==="room-view"')
                    page.wait_for_function('() => {const r=document.querySelector("#live-video").getBoundingClientRect();return Math.abs(r.width-innerWidth)<2&&Math.abs(r.height-innerHeight)<2&&r.x===0&&r.y===0;}')
                    page.locator('#room-settings-toggle').click()
                    page.locator('#video-fit').click()
                    assert page.locator('#live-video').evaluate('(e)=>getComputedStyle(e).objectFit')=='cover'
                    page.locator('#video-fit').click()
                    assert page.locator('#live-video').evaluate('(e)=>getComputedStyle(e).objectFit')=='contain'
                    page.locator('#room-settings-toggle').click()
                    page.locator('#live-video').click();page.mouse.move(1,1)
                    page.wait_for_function('() => document.querySelector("#room-view").classList.contains("quiet")',timeout=7000)
                    page.screenshot(path=str(RESULTS/'v06-live-fullscreen.png'),animations='disabled')
                    page.mouse.move(200,200);page.locator('#room-fullscreen').click();page.locator('#room-play').click()
                    page.locator('#mode-audio').click();page.locator('#room-seek').fill('3.5')
                    page.wait_for_function('() => audio.mode==="audio"')
                    page.set_viewport_size({'width':390,'height':844})
                    page.wait_for_function('() => document.querySelector(".lyric-line.current").getBoundingClientRect().width>30')
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                    page.screenshot(path=str(RESULTS/'v06-lyrics-mobile.png'),full_page=True,animations='disabled')
                    open_settings(page);assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                    page.screenshot(path=str(RESULTS/'v06-settings-mobile.png'),full_page=True,animations='disabled');page.locator('#lyrics-cancel').click()
                    page.emulate_media(reduced_motion='reduce')
                    assert page.locator('.lyric-line.current').evaluate('(e)=>getComputedStyle(e).animationName')=='none'
                    assert page.locator('.lyric-word').first.evaluate('(e)=>getComputedStyle(e).transform')=='none'
                    page.locator('#mode-video').click();page.locator('#room-fullscreen').click()
                    page.wait_for_function('() => document.fullscreenElement?.id==="room-view"')
                    assert page.locator('#live-video').bounding_box()['height']==844
                    page.locator('#room-fullscreen').click()
                    # Manage from a record without entering the room or changing playback.
                    page.locator('#room-back').click();page.locator('.inspect-cover').click()
                    old_id=page.evaluate('currentId');old_time=page.evaluate('audio.currentTime')
                    page.locator('#focus-lyrics').click();page.locator('#lyrics-dialog').wait_for(state='visible')
                    assert page.evaluate('view')=='library' and page.evaluate('currentId')==old_id
                    page.locator('#lyrics-file').set_input_files({'name':'bilingual.lrc','mimeType':'text/plain','buffer':b'[00:10]The evening light is falling softly.\n[00:17]A song to keep inside my heart.'})
                    page.wait_for_function('() => document.querySelector("#lyrics-filename").textContent.includes("2 句")')
                    page.locator('#translation-editor summary').click()
                    page.locator('#translation-rows textarea').nth(0).fill('暮色轻轻落下。')
                    page.locator('#translation-rows textarea').nth(1).fill('把这首歌留在心里。')
                    page.locator('#lyrics-form button[type=submit]').click();page.locator('#lyrics-dialog').wait_for(state='hidden')
                    assert page.evaluate('audio.paused') and abs(page.evaluate('audio.currentTime')-old_time)<.2
                    saved=httpx.get(BASE+f'/api/tracks/{tid}/lyrics').json()
                    assert saved['lines'][0]['translation']=='暮色轻轻落下。' and saved['lines'][0]['start']==10
                    page.locator('[data-view=room]').click();page.locator('#mode-audio').click()
                    page.wait_for_function('() => audio.mode==="audio"')
                    page.locator('#room-seek').fill('3')
                    page.wait_for_function('() => document.querySelector("#lyric-translation").textContent==="暮色轻轻落下。"')
                    page.emulate_media(reduced_motion='no-preference')
                    page.wait_for_function('() => !document.querySelector("#toast").classList.contains("visible")')
                    page.screenshot(path=str(RESULTS/'v06-bilingual-mobile.png'),animations='disabled')
                    page.set_viewport_size({'width':1440,'height':1000})
                    page.wait_for_function('() => document.querySelector("#room-view").getBoundingClientRect().height===1000 && document.querySelector("#room-view").getBoundingClientRect().width===1440')
                    page.evaluate('() => new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
                    page.screenshot(path=str(RESULTS/'v06-bilingual-desktop.png'),animations='disabled')
                    page.locator('#room-settings-toggle').click();page.locator('#translation-toggle').click()
                    assert not page.locator('#lyric-translation').is_visible()
                    page.locator('#translation-toggle').click();assert page.locator('#lyric-translation').is_visible()
                    page.locator('#room-settings-toggle').click()
                    # A live offset affects original and translation together, and survives reload.
                    open_settings(page);page.locator('#lyric-offset').fill('2');page.locator('#lyrics-form button[type=submit]').click()
                    page.locator('#lyrics-dialog').wait_for(state='hidden');page.locator('#room-seek').fill('0')
                    assert not page.locator('#lyric-translation').is_visible()
                    page.locator('#room-seek').fill('3')
                    page.wait_for_function('() => document.querySelector("#lyric-translation").textContent==="暮色轻轻落下。"')
                    page.reload(wait_until='networkidle');page.locator('[data-view=room]').click()
                    page.wait_for_function('() => document.querySelector("#lyric-translation").textContent==="暮色轻轻落下。"')
                    assert page.evaluate('audio.paused')
                    # Normal-window idle controls also hide; keyboard and pointer can bring them back.
                    page.locator('#room-play').click();page.locator('#lyric-stage').click();page.mouse.move(1,1)
                    page.wait_for_function('() => document.querySelector("#room-view").classList.contains("quiet")',timeout=6000)
                    assert not page.locator('.sidebar').is_visible()
                    assert page.evaluate('document.documentElement.scrollHeight<=innerHeight')
                    page.keyboard.press('Tab');assert page.locator('#room-back').is_visible()
                    page.mouse.move(200,200);page.locator('#room-play').click()
                    assert not errors,errors
                    browser.close()
                report={'temporary_storage':True,'lrc_import':True,'word_timing_and_clip_clock':True,'manual_and_auto_mood':True,'offset_preview_cancel_align_save':True,'invalid_import_preserves_lyrics':True,'paused_reload':True,'video_full_viewport_and_fit':True,'ambient_from_video':True,'fullscreen_idle':True,'mobile_and_reduced_motion':True,'translations_save_sync_toggle_reload':True,'record_details_management':True,'normal_window_idle':True,'browser_errors':errors}
                (RESULTS/'room-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
                print('PASS: v0.6 lyrics, clocks, moods, persisted settings, video light and fullscreen',flush=True)
            finally:
                server.terminate()
                try:server.wait(timeout=15)
                except subprocess.TimeoutExpired:server.kill();server.wait()


if __name__=='__main__':run()
