'use strict';
// Presentation and spatial interaction. Collection data stays in the existing API.
(() => {
  const wall = $('#track-grid');
  const focus = $('#record-focus');
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const finePointer = matchMedia('(hover: hover) and (pointer: fine)');
  let focusId = null, paletteKey = '', paletteToken = 0, tiltFrame = 0, tilted = null;
  let quietTimer, restored = false, lastSaved = 0;
  const colors = new Map();
  const read = key => { try { return JSON.parse(localStorage.getItem(key)); } catch { return null; } };
  const write = (key,value) => { try { localStorage.setItem(key,JSON.stringify(value)); } catch {} };

  function wallMode(mode) {
    document.body.dataset.wall = mode === 'flat' ? 'flat' : 'space';
    const spatial = document.body.dataset.wall === 'space';
    $('#wall-depth').setAttribute('aria-pressed',spatial);
    $('#wall-depth').textContent = spatial ? '◈ 空间陈列' : '▦ 平面陈列';
    write('lsj-wall-mode',document.body.dataset.wall);
    resetTilt();
  }
  wallMode(read('lsj-wall-mode') || 'space');
  $('#wall-depth').addEventListener('click',()=>wallMode(document.body.dataset.wall==='space'?'flat':'space'));
  $('#wall-immerse').addEventListener('click',()=> {
    const active = document.body.classList.toggle('immersed');
    $('#wall-immerse').setAttribute('aria-pressed',active);
    $('#wall-immerse').textContent = active ? '⛶ 退出沉浸' : '⛶ 沉浸浏览';
  });
  $('#wall-fullscreen').addEventListener('click',async()=> {
    try {
      if(document.fullscreenElement) await document.exitFullscreen();
      else await document.documentElement.requestFullscreen();
    } catch { toast('当前环境不支持网页全屏，可使用浏览器窗口全屏。'); }
  });
  document.addEventListener('fullscreenchange',()=> {
    $('#wall-fullscreen').textContent = document.fullscreenElement===document.documentElement ? '退出全屏 ↙' : '全屏 ↗';
    wakeRoom();
  });
  document.addEventListener('viewchange',()=> {
    if(!['library','favorites'].includes(view)) {
      document.body.classList.remove('immersed');
      $('#wall-immerse').setAttribute('aria-pressed',false);
      $('#wall-immerse').textContent='⛶ 沉浸浏览';
    }
    wakeRoom();
  });

  // Only the hovered record updates; no perpetual animation loop or per-card blur.
  function resetTilt() {
    cancelAnimationFrame(tiltFrame);
    if(tilted) ['--rx','--ry','--mx','--my'].forEach(p=>tilted.style.removeProperty(p));
    tilted=null;
  }
  wall.addEventListener('pointermove',e=> {
    if(reduced.matches || !finePointer.matches || document.body.dataset.wall!=='space') return;
    const card=e.target.closest('.track-card');
    if(!card || card.querySelector('details[open]')) return;
    if(card!==tilted) {resetTilt();tilted=card;}
    const r=card.getBoundingClientRect();
    const x=Math.max(0,Math.min(1,(e.clientX-r.left)/r.width));
    const y=Math.max(0,Math.min(1,(e.clientY-r.top)/r.height));
    cancelAnimationFrame(tiltFrame);
    tiltFrame=requestAnimationFrame(()=> {
      card.style.setProperty('--rx',`${(0.5-y)*7}deg`);
      card.style.setProperty('--ry',`${(x-0.5)*9}deg`);
      card.style.setProperty('--mx',`${x*100}%`);
      card.style.setProperty('--my',`${y*100}%`);
    });
  });
  wall.addEventListener('pointerleave',resetTilt);
  reduced.addEventListener('change',resetTilt);
  document.addEventListener('pointerdown',e=> {
    $$('.card-menu[open]').forEach(menu=> {if(!menu.contains(e.target)) menu.open=false;});
  });
  wall.addEventListener('keydown',e=> {
    if(e.target.matches('input,button:not(.inspect-cover),summary') || e.altKey || e.metaKey || e.ctrlKey) return;
    const buttons=$$('.inspect-cover',wall),index=buttons.indexOf(e.target);
    if(index<0) return;
    const columns=getComputedStyle(wall).gridTemplateColumns.split(' ').length;
    const steps={ArrowRight:1,ArrowLeft:-1,ArrowDown:columns,ArrowUp:-columns};
    if(steps[e.key]!==undefined) {
      e.preventDefault();buttons[Math.max(0,Math.min(buttons.length-1,index+steps[e.key]))]?.focus();
    }
  });

  function renderFocus() {
    const t=trackById(focusId);if(!t)return;
    $('#focus-art').innerHTML=coverHTML(t,true);
    $('#focus-title').textContent=t.title;
    $('#focus-artist').textContent=t.artist||'演唱者待补充';
    $('#focus-version').textContent=[t.version,t.album].filter(Boolean).join(' · ');
    $('#focus-note').textContent=t.note||'有些版本，值得好好留下。';
    $('#focus-facts').innerHTML=[t.source_kind==='bilibili'?'B站收藏':'本地音源',time(t.duration),t.has_video?'含现场视频':'音频收藏','私人收藏'].map(v=>`<span>${v}</span>`).join('');
    $('#focus-heart').textContent=t.favorite?'♥':'♡';
    $('#focus-heart').setAttribute('aria-pressed',t.favorite);
    $('#focus-heart').setAttribute('aria-label',t.favorite?'取消心动':'设为心动');
    $('#focus-play').textContent=currentId===t.id&&!audio.paused?'暂停':'播放';
    $('#focus-play').disabled=!t.has_audio;
    $('#focus-listen').disabled=!t.has_audio;
    $('#focus-edit').disabled=t.status!=='ready';
    $('#focus-export').disabled=!t.has_audio;
    setPalette(t);
  }
  document.addEventListener('inspecttrack',e=> {
    focusId=e.detail;renderFocus();focus.showModal();resetTilt();
  });
  focus.addEventListener('close',()=> {
    const id=focusId;focusId=null;
    $(`[data-id="${id}"] .inspect-cover`)?.focus({preventScroll:true});
    setPalette(trackById(currentId));
  });
  $('#focus-play').addEventListener('click',async()=> {try{await playTrack(focusId);renderFocus();}catch(e){toast(e.message);}});
  $('#focus-heart').addEventListener('click',async()=> {try{await favorite(focusId);renderFocus();}catch(e){toast(e.message);}});
  $('#focus-listen').addEventListener('click',async()=> {
    const id=focusId;focus.close();
    try {if(currentId!==id||audio.paused) await playTrack(id);setView('room');}catch(e){toast(e.message);}
  });
  $('#focus-edit').addEventListener('click',()=>{const id=focusId;focus.close();openEdit(id);});
  $('#focus-export').addEventListener('click',()=>{const id=focusId;focus.close();openExport(id);});

  // Sample a tiny same-origin image once per revision. Stale loads cannot change the active light.
  async function setPalette(t) {
    const key=t?`${t.id}/${t.cover_style}/${t.updated_at}`:'default';
    if(key===paletteKey)return;
    paletteKey=key;const token=++paletteToken;
    const fallback={groove:['#426b68','#a09367'],collage:['#8e6554','#9d976e'],type:['#5f6c83','#a07259'],dusk:['#647b9c','#b28c76']};
    let palette=fallback[t?.cover_style]||['#476777','#99734f'];
    if(t?.has_cover&&['original','uploaded'].includes(t.cover_style)) {
      const cached=colors.get(key);
      if(cached)palette=cached;
      else {
        try {
          const img=new Image();img.src=`/api/tracks/${t.id}/cover?kind=${t.cover_style}&v=${encodeURIComponent(t.updated_at)}`;
          await img.decode();
          const canvas=document.createElement('canvas');canvas.width=canvas.height=16;
          const ctx=canvas.getContext('2d',{willReadFrequently:true});ctx.drawImage(img,0,0,16,16);
          const data=ctx.getImageData(0,0,16,16).data;let r=0,g=0,b=0,n=0;
          for(let i=0;i<data.length;i+=4){const lum=(data[i]+data[i+1]+data[i+2])/3;if(lum>30&&lum<225){r+=data[i];g+=data[i+1];b+=data[i+2];n++;}}
          if(n)palette=[`rgb(${Math.round(r/n)} ${Math.round(g/n)} ${Math.round(b/n)})`,palette[1]];
          if(colors.size>=100)colors.delete(colors.keys().next().value);
          colors.set(key,palette);
        }catch{} // Missing images keep a readable neutral room.
      }
    }
    if(token!==paletteToken)return;
    document.documentElement.style.setProperty('--light-a',palette[0]);
    document.documentElement.style.setProperty('--light-b',palette[1]);
  }
  document.addEventListener('libraryrender',()=> {resetTilt();if(focus.open)renderFocus();restorePlayback();});
  document.addEventListener('playerchange',()=> {if(focus.open)renderFocus();else setPalette(trackById(currentId));});
  audio.addEventListener('play',()=>{if(focus.open)renderFocus();$('#resume-banner').hidden=true;wakeRoom();});
  audio.addEventListener('pause',()=>{if(focus.open)renderFocus();savePlayback(true);wakeRoom();});

  // Restore to a paused state only; users explicitly choose whether to resume.
  function savePlayback(force=false) {
    const t=trackById(currentId);
    if(!t?.has_audio||audio.audio.readyState<1||!audio.audio.getAttribute('src')?.includes(t.id)||(!force&&Date.now()-lastSaved<5000))return;
    lastSaved=Date.now();write('lsj-playback',{id:currentId,revision:t.revision,position:audio.currentTime,mode:audio.mode});
  }
  function restorePlayback() {
    if(restored)return;restored=true;
    const saved=read('lsj-playback');const t=trackById(saved?.id);
    if(currentId||!t?.has_audio||t.status!=='ready')return;
    currentId=t.id;audio.setTrack(t);audio.src=`/api/tracks/${t.id}/audio?v=${t.revision}`;
    const epoch=audio.epoch;
    const position=saved.revision===t.revision&&Number.isFinite(saved.position)&&saved.position<t.duration-.05?Math.max(0,saved.position):0;
    const restore=async()=> {
      if(epoch!==audio.epoch||!audio.paused)return;
      audio.currentTime=position;
      try {if(saved.mode==='video'&&t.has_video)await audio.switchMode('video');}catch{}
      if(currentId===t.id)renderPlayer();
    };
    if(audio.audio.readyState>=1)restore();else audio.audio.addEventListener('loadedmetadata',restore,{once:true});
    renderPlayer();
    $('#resume-play').textContent=`▶ 继续播放 · ${t.title} · ${time(position)}`;
    $('#resume-banner').hidden=false;
  }
  audio.addEventListener('timeupdate',()=>savePlayback());
  audio.addEventListener('modechange',()=>savePlayback(true));
  window.addEventListener('pagehide',()=>savePlayback(true));
  $('#resume-play').addEventListener('click',()=> {$('#resume-banner').hidden=true;audio.play().catch(e=>toast(e.message));});
  $('#resume-dismiss').addEventListener('click',()=>{$('#resume-banner').hidden=true;});

  function wakeRoom() {
    clearTimeout(quietTimer);$('#room-view').classList.remove('quiet');
    if(document.fullscreenElement===$('#room-view')&&!audio.paused) quietTimer=setTimeout(()=> {
      if(!$('#room-view').querySelector(':focus-visible')) $('#room-view').classList.add('quiet');
    },4000);
  }
  ['pointermove','pointerdown','focusin','keydown'].forEach(name=>$('#room-view').addEventListener(name,wakeRoom));
  document.addEventListener('keydown',e=> {
    if(e.key==='Escape') {
      $$('.card-menu[open]').forEach(menu=>{menu.open=false;menu.querySelector('summary').focus();});
      if(!document.querySelector('dialog[open]')&&document.body.classList.contains('immersed'))$('#wall-immerse').click();
    }
    if(e.defaultPrevented||e.altKey||e.ctrlKey||e.metaKey||document.querySelector('dialog[open]')||e.target.closest('input,textarea,select,button,summary,a,[contenteditable=true]'))return;
    if(e.code==='Space'&&currentId){e.preventDefault();playTrack(currentId).catch(err=>toast(err.message));}
    if(view==='room'&&currentId&&['ArrowLeft','ArrowRight'].includes(e.key)){e.preventDefault();audio.currentTime+=e.key==='ArrowRight'?5:-5;}
  });
})();
