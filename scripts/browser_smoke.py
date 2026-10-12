"""Real local browser → API → Bilibili/FFmpeg → SQLite → export acceptance test.
Runs against disposable storage and never changes the user's library.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.media import probe
BASE = 'http://127.0.0.1:8766'
RESULTS = ROOT / 'test-results'
RESULTS.mkdir(exist_ok=True)


def log(text):
    print(text, flush=True)


def wait_ready(tid, duration=None):
    for _ in range(600):
        tracks = httpx.get(BASE + '/api/tracks').json()
        t = next(t for t in tracks if t['id'] == tid)
        if t['status'] == 'error':
            raise AssertionError(t['error'])
        if t['status'] == 'ready' and (duration is None or abs(t['duration'] - duration) < .1):
            return t
        time.sleep(.3)
    raise AssertionError('Audio processing timed out')


def wait_audio(page, selector, minimum):
    for _ in range(150):
        if page.locator(selector).evaluate('(audio) => audio.currentTime') >= minimum:
            return
        time.sleep(.1)
    raise AssertionError(f'Audio did not advance: {selector}')


def run():
    errors=[]
    with tempfile.TemporaryDirectory(prefix='lsj-qa-') as tmp:
        tmp=Path(tmp)
        env={**os.environ,'LIUSHENGJIAN_DATA':str(tmp/'data'),'LIUSHENGJIAN_SEED':'0'}
        with (RESULTS/'server-test.log').open('w') as serverlog:
            process=subprocess.Popen([sys.executable,'-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8766','--log-level','warning'],cwd=ROOT,env=env,stdout=serverlog,stderr=serverlog)
            try:
                for _ in range(80):
                    try:
                        if httpx.get(BASE+'/api/status',timeout=.3).status_code==200:break
                    except httpx.HTTPError:time.sleep(.1)
                with sync_playwright() as p:
                    browser=p.chromium.launch(channel='chrome',headless=True)
                    context=browser.new_context(viewport={'width':1440,'height':1000},accept_downloads=True)
                    page=context.new_page()
                    page.on('pageerror',lambda e:errors.append(str(e)))
                    page.goto(BASE,wait_until='networkidle')
                    page.locator('#empty-add').wait_for(state='visible')
                    log('PASS: Empty library renders without sample fixtures')
                    page.locator('#add-button').click()
                    page.locator('#video-url').fill('https://www.bilibili.com/video/BV1CFG66UELP/')
                    page.locator('#url-form button[type=submit]').click()
                    page.locator('.track-card').wait_for(timeout=15000)
                    tid=httpx.get(BASE+'/api/tracks').json()[0]['id']
                    log('RUN: Real Bilibili extraction and audio processing')
                    track=wait_ready(tid)
                    page.locator('.card-play').wait_for(timeout=15000,state='attached')
                    page.screenshot(path=str(RESULTS/'bilibili-import.png'),full_page=True)
                    log(f"PASS: Bilibili → library, duration {track['duration']:.2f}s")
                    page.evaluate('document.querySelector("#audio").muted=true')
                    page.locator('.artwork').hover()
                    page.locator('.card-play').click()
                    wait_audio(page, '#audio', .2)
                    assert page.locator('#audio').evaluate('(a)=>!a.paused')
                    log('PASS: Browser audio playback advances')
                    page.locator('[data-action=favorite]').click()
                    page.locator('[data-view=favorites]').click()
                    page.locator('.track-card').wait_for()
                    assert page.locator('.track-card').count()==1
                    page.locator('[data-action=edit]').click()
                    form=page.locator('#edit-form')
                    form.locator('[name=title]').fill('Automatic')
                    form.locator('[name=artist]').fill('宇多田光')
                    form.locator('[name=version]').fill('MVP 试听片段')
                    form.locator('[name=album]').fill('端到端验证')
                    form.locator('[name=trim_start]').fill('0:10')
                    form.locator('[name=trim_end]').fill('0:18.50')
                    page.evaluate('document.querySelector("#preview-audio").muted=true')
                    page.locator('#preview-trim').click()
                    wait_audio(page, '#preview-audio', 10)
                    form.locator('button[type=submit]').click()
                    track=wait_ready(tid,8.5)
                    page.locator('[data-action=export]').wait_for(timeout=15000)
                    page.locator('[data-action=export]').click()
                    assert '手动导入' in page.locator('#export-dialog').inner_text()
                    with page.expect_download() as dl:
                        page.locator('#export-mp3').click()
                    downloaded=tmp/'downloaded.mp3';dl.value.save_as(downloaded)
                    info=probe(downloaded)
                    assert abs(info['duration']-8.5)<.1
                    assert info['tags']['title']=='Automatic（MVP 试听片段）'
                    assert info['tags']['artist']=='宇多田光'
                    response=httpx.get(BASE+f'/api/tracks/{tid}/audio',headers={'Range':'bytes=0-99'})
                    assert response.status_code==206 and len(response.content)==100
                    page.locator('#export-dialog .close-dialog').click()
                    log('PASS: UI trim preview, metadata edit, MP3 download and byte-range playback')
                    page.locator('[data-action=edit]').click()
                    art=tmp/'cover.png';Image.new('RGB',(400,400),'#586c51').save(art)
                    page.locator('#cover-file').set_input_files(art)
                    page.locator('#edit-cover img').wait_for()
                    page.locator('#edit-dialog .dialog-heading .close-dialog').click()
                    page.locator('[data-action=archive]').click()
                    page.locator('[data-view=archive]').click()
                    page.locator('[data-action=restore]').wait_for()
                    page.locator('[data-action=restore]').click()
                    page.locator('[data-view=library]').click()
                    page.locator('.track-card').wait_for()
                    log('PASS: Cover upload, favorite, archive and restore')
                    page.locator('#search').fill('no matching music')
                    page.locator('#empty-state').wait_for(state='visible')
                    assert page.locator('.track-card').count()==0
                    page.locator('#search').fill('')
                    page.locator('#add-button').click()
                    page.locator('#video-url').fill('BV1CFG66UELP')
                    page.locator('#url-form button[type=submit]').click()
                    page.locator('#import-dialog').wait_for(state='hidden')
                    assert len(httpx.get(BASE+'/api/tracks').json())==1
                    log('PASS: Search and Bilibili duplicate detection')
                    page.locator('#add-button').click()
                    page.locator('[data-import=file]').click()
                    page.locator('#media-file').set_input_files(downloaded)
                    page.locator('#file-form button[type=submit]').click()
                    page.locator('#import-dialog').wait_for(state='hidden')
                    newtracks=httpx.get(BASE+'/api/tracks').json()
                    local=next(t for t in newtracks if t['id']!=tid)
                    local=wait_ready(local['id'])
                    assert local['artist']=='宇多田光'
                    log('PASS: Local file import retains embedded artist metadata')
                    page.locator('[data-view=room]').click()
                    page.locator('[data-theme=night].theme').click()
                    assert page.locator('.listening-room').get_attribute('data-theme')=='night'
                    page.screenshot(path=str(RESULTS/'listening-room.png'),full_page=True)
                    page.reload(wait_until='networkidle')
                    assert page.locator('.listening-room').get_attribute('data-theme')=='night'
                    page.locator('.track-card').first.wait_for()
                    assert page.locator('.track-card').count()==2
                    page.set_viewport_size({'width':390,'height':844})
                    page.screenshot(path=str(RESULTS/'mobile-library.png'),full_page=True)
                    assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
                    page.locator('#add-button').click()
                    page.screenshot(path=str(RESULTS/'mobile-import.png'),full_page=True)
                    assert page.locator('#video-url').is_visible()
                    assert not errors, errors
                    browser.close()
                # Restart using the same SQLite library and verify saved edits and covers.
                process.terminate();process.wait(timeout=15)
                process=subprocess.Popen([sys.executable,'-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8766','--log-level','warning'],cwd=ROOT,env=env,stdout=serverlog,stderr=serverlog)
                for _ in range(80):
                    try:
                        if httpx.get(BASE+'/api/status',timeout=.3).status_code==200:break
                    except httpx.HTTPError:time.sleep(.1)
                saved=httpx.get(BASE+'/api/tracks').json()
                assert len(saved)==2
                sample=next(t for t in saved if t['id']==tid)
                assert sample['favorite'] and sample['has_cover'] and sample['duration']==8.5 and not sample['archived']
                log('PASS: Restart persistence, mobile layout, room theme and zero browser errors')
                (RESULTS/'browser-report.json').write_text(json.dumps({'bilibili_import':True,'local_import':True,'playback':True,'trim_preview':True,'metadata_and_download':True,'covers_and_favorites':True,'archive_restore':True,'search_and_dedup':True,'restart_persistence':True,'mobile_overflow':False,'browser_errors':errors,'cloud_upload':'manual; not automated'},ensure_ascii=False,indent=2))
            finally:
                process.terminate()
                try:process.wait(timeout=15)
                except subprocess.TimeoutExpired:process.kill();process.wait()


if __name__=='__main__':
    run()
