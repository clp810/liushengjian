'use strict';
// Timed text and ambient light consume the existing player; neither owns playback.
(() => {
  const room = $('#room-view'), dialog = $('#lyrics-dialog'), form = $('#lyrics-form');
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const defaults = {offset:0, clock:'source', mood:'auto'};
  const names = {calm:'舒缓 · 呼吸', bright:'明亮 · 轻盈', intense:'热烈 · 光晕'};
  let pendingLyrics = null;
  let documentData = null, settings = {...defaults}, loadedKey = '', loadToken = 0;
  let frame = 0, lastFrame = 0, lastLight = 0, lineKey = '', wordNodes = [], busy = false, dialogTrack = null;
  const canvas = $('#stage-light'), ctx = canvas.getContext('2d');
  try { room.classList.toggle('no-ambient',localStorage.getItem('lsj-room-light')==='off'); } catch {}
  $('#ambient-toggle').setAttribute('aria-pressed',!room.classList.contains('no-ambient'));
  $('#ambient-toggle').textContent = `环境光：${room.classList.contains('no-ambient')?'关闭':'开启'}`;

  function clockTime() { return audio.currentTime + (settings.clock==='source' ? (trackById(currentId)?.trim_start || 0) : 0); }
  function seekTime(line) { return line.start + settings.offset - (settings.clock==='source' ? (trackById(currentId)?.trim_start || 0) : 0); }
  function resolveMood(line) {
    if(settings.mood!=='auto')return settings.mood;
    const text = line?.text || '';
    if(/燃烧|呐喊|奔跑|烈火|怒放|战斗|嘶吼|\b(fire|fight|scream|burn)\b/i.test(text))return 'intense';
    if(/阳光|微笑|晴天|快乐|欢笑|跳舞|盛开|\b(sunshine|smile|happy|dance)\b/i.test(text))return 'bright';
    return 'calm';
  }
  function setMood(line) {
    const mood=resolveMood(line);room.dataset.mood=mood;
    $('#room-mood-label').textContent=(settings.mood==='auto'?'自动 · ':'')+names[mood];
  }
  function renderLyrics(force=false) {
    const lines=documentData?.lines || [], now=clockTime()-settings.offset;
    let lo=0,hi=lines.length;
    while(lo<hi) {const mid=(lo+hi)>>1;if(lines[mid].start<=now)lo=mid+1;else hi=mid;}
    const index=lo-1, line=lines[index], active=!!line?.text && now<line.end;
    const key=`${index}/${active}/${loadedKey}/${trackById(currentId)?.revision}`;
    $('#lyric-empty').hidden=!!lines.length;
    $('#lyric-window').hidden=!lines.length;
    if(force || key!==lineKey) {
      lineKey=key;wordNodes=[];
      const windowEl=$('#lyric-window');windowEl.replaceChildren();
      setMood(active?line:null);
      if(lines.length) {
        const indices=active?[index-1,index,index+1]:[index,index+1];
        for(const i of indices) {
          if(!active && i===index+1) {
            const interlude=document.createElement('div');interlude.className='lyric-interlude';
            interlude.textContent=index<0?'前奏':index===lines.length-1?'余韵':'间奏';windowEl.append(interlude);
          }
          const row=lines[i];if(!row?.text)continue;
          const button=document.createElement('button');button.type='button';button.className='lyric-line';
          const position=seekTime(row);button.disabled=position<0 || position>=audio.duration;
          button.setAttribute('aria-label',row.text+(button.disabled?'（在当前片段以外）':'，定位到这一句'));
          button.addEventListener('click',()=>{audio.currentTime=position;renderLyrics(true);});
          if(active && i===index) {
            button.classList.add('current');if(row.text.length>50)button.classList.add('long-line');
            button.setAttribute('aria-current','true');
            if(row.words.length) for(const word of row.words) {
              const span=document.createElement('span');span.className='lyric-word';span.textContent=word.text;
              span.setAttribute('aria-hidden','true');button.append(span);wordNodes.push({span,word});
            }
            else button.textContent=row.text;
          } else button.textContent=row.text;
          windowEl.append(button);
        }
        windowEl.scrollTop=0;
      }
      $('#lyric-timing-label').textContent=lines.length ? `${documentData.timed_words?'逐字时间 · 逐字亮起':'逐句时间 · 整句聚焦'}  /  ${settings.clock==='source'?'原音轨':'片段'}时间  /  ${settings.offset>=0?'延后':'提前'} ${Math.abs(settings.offset).toFixed(2)} 秒` : '';
    }
    for(const {span,word} of wordNodes) {
      const progress=Math.max(0,Math.min(1,(now-word.start)/Math.max(.01,word.end-word.start)));
      span.style.setProperty('--progress',`${(progress*100).toFixed(1)}%`);
      const lift=reduced.matches?0:Math.sin(progress*Math.PI)*({calm:3,bright:7,intense:10}[room.dataset.mood]);
      span.style.setProperty('--rise',`${-lift}px`);
    }
    if(dialog.open) $('#lyric-clock-now').textContent=`当前${settings.clock==='source'?'原音轨':'片段'}时间：${clockTime().toFixed(2)} 秒`;
  }
  function paintLight() {
    if(view!=='room'||audio.mode!=='video'||audio.video.readyState<2||reduced.matches||room.classList.contains('no-ambient')||document.fullscreenElement===room)return;
    if(performance.now()-lastLight<250)return;
    lastLight=performance.now();
    try {ctx.drawImage(audio.video,0,0,32,18);}catch{} // A not-yet-decoded frame keeps the previous light.
  }
  // At most 30 text updates / second and 4 tiny video samples / second, visible playback only.
  function needsFrames() {
    return audio.mode==='audio' || (!room.classList.contains('no-ambient') && document.fullscreenElement!==room);
  }
  function tick(timestamp) {
    frame=0;
    if(document.hidden||view!=='room'||audio.paused||reduced.matches||!needsFrames())return;
    if(timestamp-lastFrame>=32) {if(audio.mode==='audio')renderLyrics();lastFrame=timestamp;}
    paintLight();
    frame=requestAnimationFrame(tick);
  }
  function wake() {
    cancelAnimationFrame(frame);frame=0;
    room.classList.toggle('playing',view==='room'&&!audio.paused&&!document.hidden);
    if(view==='room'&&!document.hidden) {renderLyrics();paintLight();if(!audio.paused&&!reduced.matches&&needsFrames())frame=requestAnimationFrame(tick);}
  }
  async function sync() {
    room.dataset.mode=audio.mode;
    $('#video-fit').hidden=audio.mode!=='video';
    $('#room-kind').textContent=audio.mode==='video'?'LIVE SESSION · 此刻在场':'PRIVATE LISTENING SESSION';
    const track=trackById(currentId), key=track?`${track.id}/${track.lyrics_revision || ''}`:'';
    $('#lyrics-settings').disabled=!track?.has_audio;$('#lyric-empty-import').disabled=!track?.has_audio;
    if(dialog.open&&dialogTrack!==currentId)dialog.close();
    if(key!==loadedKey) {
      loadedKey=key;const token=++loadToken;
      documentData=null;settings={...defaults};lineKey='';$('#lyric-empty-hint').textContent='正在读取这份收藏的歌词…';wake();
      try {
        pendingLyrics=track?api(`/api/tracks/${track.id}/lyrics`):Promise.resolve(null);
        const result=await pendingLyrics;
        if(token!==loadToken)return;
        documentData=result;settings={...defaults,...result?.settings};
        $('#lyric-empty-hint').textContent='导入这份版本的歌词，让文字随声音亮起。';
      } catch(error) {
        if(token!==loadToken)return;
        loadedKey='';$('#lyric-empty-hint').textContent=`歌词读取失败：${error.message} 可打开设置重试。`;
      }
      if(token===loadToken)pendingLyrics=null;
      renderLyrics(true);
    } else if(pendingLyrics) {try {await pendingLyrics;}catch{}}
    wake();
  }
  function fillForm() {
    $('#lyrics-filename').textContent=documentData?.lines.length ? `${documentData.filename} · ${documentData.lines.length} 句${documentData.file_offset ? ` · 文件自带${documentData.file_offset>0?'提前':'延后'} ${Math.abs(documentData.file_offset)} 秒` : ''}`:'尚未导入歌词';
    $('#lyric-offset').value=settings.offset;$('#lyric-clock').value=settings.clock;$('#lyric-mood').value=settings.mood;
    const select=$('#lyric-anchor');select.replaceChildren();
    (documentData?.lines || []).forEach((line,i)=>{if(line.text){const option=document.createElement('option');option.value=i;option.textContent=`${time(Math.max(0,line.start))} · ${line.text.slice(0,70)}`;select.append(option);}});
    const current=documentData?.lines.findIndex(l=>l.start<=clockTime()-settings.offset && l.end>clockTime()-settings.offset);
    if(current>=0)select.value=current;
    select.disabled=!select.options.length;$('#lyric-align-now').disabled=!select.options.length;renderLyrics();
  }
  async function openSettings() {
    if(!currentId)return;
    dialogTrack=currentId;$('#lyrics-error').textContent='';
    await sync();if(dialogTrack!==currentId)return;
    settings={...defaults,...documentData?.settings};fillForm();dialog.showModal();
  }
  function preview() {
    const offset=Number($('#lyric-offset').value);
    if(!Number.isFinite(offset)||Math.abs(offset)>600)return;
    settings={offset,clock:$('#lyric-clock').value,mood:$('#lyric-mood').value};renderLyrics(true);
  }
  function setBusy(value) {
    busy=value;$$('button,input,select',form).forEach(e=>e.disabled=value);
    if(!value){$('#lyric-anchor').disabled=!$('#lyric-anchor').options.length;$('#lyric-align-now').disabled=$('#lyric-anchor').disabled;}
  }
  $('#lyrics-settings').addEventListener('click',openSettings);
  $('#lyric-empty-import').addEventListener('click',openSettings);
  $('#lyrics-close').addEventListener('click',()=>dialog.close());$('#lyrics-cancel').addEventListener('click',()=>dialog.close());
  dialog.addEventListener('cancel',e=>{if(busy)e.preventDefault();});
  dialog.addEventListener('close',()=>{settings={...defaults,...documentData?.settings};renderLyrics(true);});
  ['lyric-clock','lyric-mood','lyric-offset'].forEach(id=>$('#'+id).addEventListener('input',preview));
  function nudge(delta) {$('#lyric-offset').value=Math.max(-600,Math.min(600,Number($('#lyric-offset').value||0)+delta)).toFixed(2);preview();}
  $('#lyric-earlier').addEventListener('click',()=>nudge(-.25));$('#lyric-later').addEventListener('click',()=>nudge(.25));
  $('#lyric-align-now').addEventListener('click',()=>{
    const line=documentData?.lines[Number($('#lyric-anchor').value)];if(!line)return;
    const offset=clockTime()-line.start;
    if(Math.abs(offset)>600){$('#lyrics-error').textContent='偏移超过 600 秒，请检查歌词对应完整音轨还是当前片段。';return;}
    $('#lyric-offset').value=offset.toFixed(2);preview();
  });
  $('#lyrics-file').addEventListener('change',async e=>{
    const file=e.target.files[0], id=dialogTrack;if(!file||!id)return;
    $('#lyrics-error').textContent='';setBusy(true);
    try {
      const data=new FormData();data.append('file',file);
      const result=await api(`/api/tracks/${id}/lyrics`,{method:'POST',body:data});
      if(id===currentId){documentData=result;settings={...defaults,...result.settings};fillForm();renderLyrics(true);}
      await refresh();toast('歌词已导入；时间和动效调整后请保存。');
    }catch(error){$('#lyrics-error').textContent=error.message;}
    finally{e.target.value='';setBusy(false);}
  });
  form.addEventListener('submit',async e=>{
    e.preventDefault();preview();const id=dialogTrack;if(!id||busy)return;
    setBusy(true);$('#lyrics-error').textContent='';
    try {
      const result=await api(`/api/tracks/${id}/lyrics/settings`,{method:'PUT',body:JSON.stringify(settings)});
      if(id===currentId){documentData=result;settings={...defaults,...result.settings};}
      dialog.close();await refresh();toast('这份收藏的歌词与动效已保存。');
    }catch(error){$('#lyrics-error').textContent=error.message;}
    finally{setBusy(false);}
  });
  $('#video-fit').addEventListener('click',()=>{
    const fill=room.dataset.fit!=='fill';room.dataset.fit=fill?'fill':'contain';
    $('#video-fit').setAttribute('aria-pressed',fill);$('#video-fit').textContent=fill?'画面：裁切铺满':'画面：完整显示';
  });
  $('#ambient-toggle').addEventListener('click',()=>{
    const enabled=!room.classList.toggle('no-ambient');$('#ambient-toggle').setAttribute('aria-pressed',enabled);
    $('#ambient-toggle').textContent=`环境光：${enabled?'开启':'关闭'}`;
    try{localStorage.setItem('lsj-room-light',enabled?'on':'off');}catch{}wake();
  });
  document.addEventListener('playerchange',()=>queueMicrotask(sync));
  document.addEventListener('viewchange',()=>queueMicrotask(wake));
  ['play','pause','loadedmetadata','modechange','ended'].forEach(name=>audio.addEventListener(name,()=>queueMicrotask(wake)));
  audio.addEventListener('timeupdate',()=>{if(view==='room'&&!document.hidden){renderLyrics();paintLight();}});
  document.addEventListener('visibilitychange',wake);document.addEventListener('fullscreenchange',wake);
  reduced.addEventListener('change',()=>{renderLyrics(true);wake();});sync();
})();
