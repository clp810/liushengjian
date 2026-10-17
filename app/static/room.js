'use strict';
// One media clock; translations travel with their original line, including live offsets.
(() => {
  const room=$('#room-view'), dialog=$('#lyrics-dialog'), form=$('#lyrics-form'), previewAudio=$('#lyrics-preview');
  const reduced=matchMedia('(prefers-reduced-motion: reduce)'), defaults={offset:0,clock:'source',mood:'auto'};
  const canvas=$('#stage-light'), ctx=canvas.getContext('2d'), panel=$('#room-settings-panel');
  let documentData=null, settings={...defaults}, loadedKey='', loadToken=0, artKey='';
  let editDoc=null, editSettings={...defaults}, dialogTrack=null, busy=false, openToken=0;
  let frame=0,lastFrame=0,lastLight=0,lineKey='',wordNodes=[],showTranslation=true;
  try {room.classList.toggle('no-ambient',localStorage.getItem('lsj-room-light')==='off');showTranslation=localStorage.getItem('lsj-translation')!=='off';}catch{}
  function lightLabel() {const enabled=!room.classList.contains('no-ambient');$('#ambient-toggle').setAttribute('aria-pressed',enabled);$('#ambient-toggle').textContent=`环境光：${enabled?'开启':'关闭'}`;}
  function translationLabel() {$('#translation-toggle').setAttribute('aria-pressed',showTranslation);$('#translation-toggle').textContent=`中文译文：${showTranslation?'显示':'隐藏'}`;}
  lightLabel();translationLabel();
  function clockTime() {return audio.currentTime+(settings.clock==='source'?(trackById(currentId)?.trim_start||0):0);}
  function editClock() {return previewAudio.currentTime-(editSettings.clock==='clip'?(trackById(dialogTrack)?.trim_start||0):0);}
  function mood(line) {
    if(settings.mood!=='auto')return settings.mood;
    const text=line?.text||'';
    if(/燃烧|呐喊|奔跑|烈火|怒放|战斗|嘶吼|\b(fire|fight|scream|burn)\b/i.test(text))return 'intense';
    if(/阳光|微笑|晴天|快乐|欢笑|跳舞|盛开|\b(sunshine|smile|happy|dance)\b/i.test(text))return 'bright';
    return 'calm';
  }
  function fitLine() {
    const line=$('.lyric-line.current');if(!line)return;
    const stage=$('#lyric-window').getBoundingClientRect();
    if(stage.width<1||stage.height<1)return;
    line.style.setProperty('--line-fit',1);
    const font=Math.min(116,Math.max(26,stage.width/9));
    line.style.setProperty('--line-font',`${line.classList.contains('long-line')?font*.66:font}px`);
    // Measure transformed glyphs as well as the flex box; long CJK and narrow windows must fit.
    let left=Infinity,right=-Infinity,top=Infinity,bottom=-Infinity;
    $$('.kinetic-unit',line).forEach(e=>{const r=e.getBoundingClientRect();left=Math.min(left,r.left);right=Math.max(right,r.right);top=Math.min(top,r.top);bottom=Math.max(bottom,r.bottom);});
    const box=line.getBoundingClientRect(),cx=box.x+box.width/2,cy=box.y+box.height/2;
    const w=Math.max(cx-left,right-cx)*2,h=Math.max(cy-top,bottom-cy)*2;
    line.style.setProperty('--line-fit',Math.min(1,stage.width*.94/Math.max(1,w),stage.height*.93/Math.max(1,h)));
  }
  function pieces(row) {
    if(row.words?.length)return row.words.map(w=>({text:w.text,word:w}));
    // Whitespace stays attached to the preceding word. CJK uses small visual phrases.
    return (row.text.match(/[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}ー]{1,4}|[^\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}ー\s]+\s*|\s+/gu)||[row.text]).map(text=>({text}));
  }
  function renderLyrics(force=false) {
    const lines=documentData?.lines||[],now=clockTime()-settings.offset;
    let lo=0,hi=lines.length;while(lo<hi){const m=(lo+hi)>>1;if(lines[m].start<=now)lo=m+1;else hi=m;}
    const index=lo-1,line=lines[index],active=!!line?.text&&now<line.end;
    const key=`${index}/${active}/${loadedKey}`;
    $('#lyric-empty').hidden=!!lines.length;$('#lyric-window').hidden=!lines.length;
    const translated=active?line.translation||'':'';
    if($('#lyric-translation').textContent!==translated)$('#lyric-translation').textContent=translated;$('#lyric-translation').hidden=!showTranslation||!translated;
    room.dataset.mood=mood(active?line:null);
    if(force||key!==lineKey){
      lineKey=key;wordNodes=[];const container=$('#lyric-window');container.replaceChildren();
      if(active){
        const node=document.createElement('div');node.className='lyric-line current'+(line.words?.length?'':' untimed')+(line.text.length>50?' long-line':'');node.setAttribute('aria-label',line.text);
        let seed=Array.from(line.text).reduce((s,c)=>(s*31+c.codePointAt(0))>>>0,17);
        const random=()=>{seed=(Math.imul(seed,1664525)+1013904223)>>>0;return seed/4294967296;};
        for(const piece of pieces(line)){
          const unit=document.createElement('span');unit.className='kinetic-unit';unit.setAttribute('aria-hidden','true');
          unit.style.setProperty('--angle',`${(random()-.5)*12}deg`);unit.style.setProperty('--dy',`${(random()-.5)*.24}em`);unit.style.setProperty('--word-size',`${.85+random()*.38}em`);
          if(piece.word){const span=document.createElement('span');span.className='lyric-word';span.textContent=piece.text;unit.append(span);wordNodes.push({span,word:piece.word});}
          else unit.textContent=piece.text;
          node.append(unit);
        }
        container.append(node);fitLine();
      }else if(lines.length){const el=document.createElement('div');el.className='lyric-interlude';el.textContent=index<0?'前奏':index===lines.length-1?'余韵':'间奏';container.append(el);}
    }
    for(const {span,word} of wordNodes){const progress=Math.max(0,Math.min(1,(now-word.start)/Math.max(.01,word.end-word.start)));span.style.setProperty('--progress',`${(progress*100).toFixed(1)}%`);span.style.setProperty('--rise',`${reduced.matches?0:-Math.sin(progress*Math.PI)*({calm:3,bright:7,intense:10}[room.dataset.mood])}px`);}
  }
  new ResizeObserver(fitLine).observe($('#lyric-window'));
  function paintLight(){if(view!=='room'||audio.mode!=='video'||audio.video.readyState<2||reduced.matches||room.classList.contains('no-ambient')||performance.now()-lastLight<250)return;lastLight=performance.now();try{ctx.drawImage(audio.video,0,0,32,18);}catch{}}
  function tick(timestamp){frame=0;if(document.hidden||view!=='room'||audio.paused||reduced.matches)return;if(timestamp-lastFrame>=32){if(audio.mode==='audio')renderLyrics();lastFrame=timestamp;}paintLight();frame=requestAnimationFrame(tick);}
  function wake(){cancelAnimationFrame(frame);frame=0;room.classList.toggle('playing',view==='room'&&!audio.paused&&!document.hidden);if(view==='room'&&!document.hidden){renderLyrics();paintLight();if(!audio.paused&&!reduced.matches)frame=requestAnimationFrame(tick);}}
  async function sync(){
    room.dataset.mode=audio.mode;$('#video-fit').hidden=audio.mode!=='video';
    const track=trackById(currentId),key=track?`${track.id}/${track.lyrics_revision||''}`:'';
    $('#lyrics-settings').disabled=!track?.has_audio;
    const art=track?`${track.id}/${track.cover_style}/${track.updated_at}`:'';
    if(art!==artKey){artKey=art;$('#room-backdrop-cover').innerHTML=track?coverHTML(track,true):'';}
    $('#lyric-empty h3').textContent=track?.title||'此刻，只留声音。';$('#lyric-empty-hint').textContent=track?.artist||'从唱片墙选一份喜欢的声音';
    if(key!==loadedKey){loadedKey=key;const token=++loadToken;documentData=null;settings={...defaults};lineKey='';wake();
      try{const data=await api(`/api/tracks/${track.id}/lyrics`);if(token!==loadToken)return;documentData=data;settings={...defaults,...data.settings};}
      catch(error){if(token!==loadToken)return;loadedKey='';$('#lyric-empty-hint').textContent='歌词暂时无法读取，可从角落设置重试。';}
      renderLyrics(true);
    }fitLine();wake();
  }
  function togglePanel(open){panel.hidden=!open;$('#room-settings-toggle').setAttribute('aria-expanded',open);room.classList.remove('quiet');room.dispatchEvent(new Event('pointermove'));}
  $('#room-settings-toggle').addEventListener('click',()=>togglePanel(panel.hidden));
  room.addEventListener('pointerdown',e=>{if(!panel.hidden&&!panel.contains(e.target)&&!$('#room-settings-toggle').contains(e.target))togglePanel(false);});
  document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!panel.hidden){e.preventDefault();e.stopImmediatePropagation();togglePanel(false);$('#room-settings-toggle').focus();}},true);
  $('#translation-toggle').addEventListener('click',()=>{showTranslation=!showTranslation;translationLabel();try{localStorage.setItem('lsj-translation',showTranslation?'on':'off');}catch{}renderLyrics();});
  function clockLabel(){$('#lyric-clock-now').textContent=`试听${editSettings.clock==='source'?'原音轨':'片段'}时间：${editClock().toFixed(2)} 秒`;}
  function fillForm(){
    $('#lyrics-filename').textContent=editDoc?.lines.length?`${editDoc.filename} · ${editDoc.lines.length} 句`:'尚未导入歌词';
    $('#lyric-offset').value=editSettings.offset;$('#lyric-clock').value=editSettings.clock;$('#lyric-mood').value=editSettings.mood;
    const select=$('#lyric-anchor'),rows=$('#translation-rows');select.replaceChildren();rows.replaceChildren();
    (editDoc?.lines||[]).forEach((line,i)=>{
      if(line.text){const option=document.createElement('option');option.value=i;option.textContent=`${time(Math.max(0,line.start))} · ${line.text.slice(0,70)}`;select.append(option);}
      const label=document.createElement('label'),text=document.createElement('span'),input=document.createElement('textarea');text.textContent=`${i+1}. ${line.text||'（间奏）'}`;input.value=line.translation||'';input.maxLength=1000;input.rows=1;input.setAttribute('aria-label',`第 ${i+1} 句中文译文`);label.append(text,input);rows.append(label);
    });
    select.disabled=!select.options.length;$('#lyric-align-now').disabled=select.disabled;clockLabel();
  }
  async function openSettings(id=currentId){
    const track=trackById(id);if(!track?.has_audio)return;const token=++openToken;
    togglePanel(false);$('#lyrics-error').textContent='';
    try{const data=await api(`/api/tracks/${id}/lyrics`);if(token!==openToken)return;dialogTrack=id;editDoc=data;editSettings={...defaults,...data.settings};$('#lyrics-song').textContent=[track.title,track.artist].filter(Boolean).join(' · ');fillForm();
      previewAudio.src=`/api/tracks/${id}/audio?kind=source`;const position=(id===currentId?audio.currentTime:0)+(track.trim_start||0);
      previewAudio.onloadedmetadata=()=>{previewAudio.currentTime=position;clockLabel();};
      (document.fullscreenElement||document.body).append(dialog);dialog.showModal();
    }catch(error){toast(error.message);}
  }
  function preview(){const offset=Number($('#lyric-offset').value);if(!Number.isFinite(offset)||Math.abs(offset)>600)return;editSettings={offset,clock:$('#lyric-clock').value,mood:$('#lyric-mood').value};if(dialogTrack===currentId){settings={...editSettings};renderLyrics(true);}clockLabel();}
  function setBusy(value){busy=value;$$('button,input,select,textarea',form).forEach(e=>e.disabled=value);if(!value){$('#lyric-anchor').disabled=!$('#lyric-anchor').options.length;$('#lyric-align-now').disabled=$('#lyric-anchor').disabled;}}
  $('#lyrics-settings').addEventListener('click',()=>openSettings());document.addEventListener('manageLyrics',e=>openSettings(e.detail));
  $('#lyrics-close').addEventListener('click',()=>dialog.close());$('#lyrics-cancel').addEventListener('click',()=>dialog.close());
  dialog.addEventListener('cancel',e=>{if(busy)e.preventDefault();});
  dialog.addEventListener('close',()=>{if(dialog.open)return;++openToken;previewAudio.pause();settings={...defaults,...documentData?.settings};renderLyrics(true);});
  $('#match-lyrics').addEventListener('click',()=>{const id=dialogTrack;dialog.close();document.dispatchEvent(new CustomEvent('matchLyrics',{detail:id}));});
  previewAudio.addEventListener('play',()=>{audio.pause();$('#preview-audio').pause();$('#match-preview-audio').pause();});previewAudio.addEventListener('timeupdate',clockLabel);
  audio.addEventListener('play',()=>previewAudio.pause());$('#preview-audio').addEventListener('play',()=>previewAudio.pause());
  ['lyric-clock','lyric-mood','lyric-offset'].forEach(id=>$('#'+id).addEventListener('input',preview));
  function nudge(delta){$('#lyric-offset').value=Math.max(-600,Math.min(600,Number($('#lyric-offset').value||0)+delta)).toFixed(2);preview();}
  $('#lyric-earlier').addEventListener('click',()=>nudge(-.25));$('#lyric-later').addEventListener('click',()=>nudge(.25));
  $('#lyric-align-now').addEventListener('click',()=>{const line=editDoc?.lines[Number($('#lyric-anchor').value)];if(!line)return;const offset=editClock()-line.start;if(Math.abs(offset)>600){$('#lyrics-error').textContent='偏移超过 600 秒，请检查歌词对应原音轨还是片段。';return;}$('#lyric-offset').value=offset.toFixed(2);preview();});
  $('#lyrics-file').addEventListener('change',async e=>{const file=e.target.files[0],id=dialogTrack;if(!file||!id)return;$('#lyrics-error').textContent='';setBusy(true);try{const data=new FormData();data.append('file',file);editDoc=await api(`/api/tracks/${id}/lyrics`,{method:'POST',body:data});editSettings={...defaults,...editDoc.settings};fillForm();await refresh();toast('歌词已导入；时间和译文调整后请保存。');}catch(error){$('#lyrics-error').textContent=error.message;}finally{e.target.value='';setBusy(false);}});
  form.addEventListener('submit',async e=>{
    e.preventDefault();preview();const id=dialogTrack;if(!id||busy)return;setBusy(true);$('#lyrics-error').textContent='';
    try{const translations=$$('textarea',$('#translation-rows')).map(e=>e.value.trim());
      if(translations.some((t,i)=>t!==(editDoc.lines[i].translation||''))){const result=await api(`/api/tracks/${id}/lyrics/translations`,{method:'PUT',body:JSON.stringify({revision:editDoc.revision,translations})});editDoc.revision=result.revision;editDoc.lines.forEach((l,i)=>l.translation=translations[i]);}
      const result=await api(`/api/tracks/${id}/lyrics/settings`,{method:'PUT',body:JSON.stringify(editSettings)});if(id===currentId){documentData=result;settings={...defaults,...result.settings};}dialog.close();await refresh();toast('歌词与中文译文已保存。');
    }catch(error){$('#lyrics-error').textContent=error.message;}finally{setBusy(false);}
  });
  $('#video-fit').addEventListener('click',()=>{const fill=room.dataset.fit!=='fill';room.dataset.fit=fill?'fill':'contain';$('#video-fit').setAttribute('aria-pressed',fill);$('#video-fit').textContent=fill?'画面：裁切铺满':'画面：完整显示';});
  $('#ambient-toggle').addEventListener('click',()=>{const enabled=!room.classList.toggle('no-ambient');lightLabel();try{localStorage.setItem('lsj-room-light',enabled?'on':'off');}catch{}wake();});
  document.addEventListener('playerchange',()=>queueMicrotask(sync));document.addEventListener('viewchange',()=>{togglePanel(false);queueMicrotask(wake);});
  ['play','pause','loadedmetadata','modechange','ended'].forEach(name=>audio.addEventListener(name,()=>queueMicrotask(wake)));
  audio.addEventListener('timeupdate',()=>{if(view==='room'&&!document.hidden){renderLyrics();paintLight();}});
  document.addEventListener('visibilitychange',wake);document.addEventListener('fullscreenchange',()=>{if(!document.fullscreenElement){for(const d of [dialog,$('#match-dialog')])if(!d.open)document.body.append(d);}wake();});
  reduced.addEventListener('change',()=>{renderLyrics(true);wake();});sync();
})();
