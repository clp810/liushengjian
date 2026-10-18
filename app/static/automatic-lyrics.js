'use strict';
// Listening starts preparation once; progress stays out of the immersive lyric stage.
(() => {
  const dialog=$('#auto-lyrics-dialog'),room=$('#room-view'),entries=new Map(),timers=new Map(),attempted=new Set();
  let selected=null,enabled=true,openEpoch=0;
  try{enabled=localStorage.getItem('lsj-auto-lyrics')!=='off';}catch{}
  $('#auto-enabled').checked=enabled;
  const active=data=>['queued','running'].includes(data?.status);
  function render(){
    const data=entries.get(selected),track=trackById(selected);if(!dialog.open||!track)return;
    $('#auto-song').textContent=data?.song?[data.song.title,data.song.artist].filter(Boolean).join(' · '):track.title;
    $('#auto-status').textContent=data?.message||'正在查看歌词…';$('#auto-activity').hidden=!active(data);
    $('#auto-retry').hidden=active(data);$('#auto-retry').disabled=!data;
    $('#auto-retry').textContent=data?.status==='ready'?'重新自动匹配':data?.status==='idle'?'自动准备歌词':'再试一次';
    $('#auto-cancel').hidden=!active(data);
    const lines=data?.plain_lines||[],copy=$('#auto-copy');
    const key=JSON.stringify(lines);
    if(copy.dataset.content!==key){copy.dataset.content=key;copy.replaceChildren();for(const line of lines){const p=document.createElement('p'),small=document.createElement('small');p.textContent=line.text;small.textContent=line.translation||'';p.append(small);copy.append(p);}}
    $('#auto-text').hidden=!lines.length;
  }
  function stageStatus(){
    const data=entries.get(currentId),el=$('#room-lyrics-status');
    el.hidden=!data||view!=='room'||audio.mode!=='audio';
    el.textContent=active(data)?'正在准备歌词…':data?.status==='text_only'?'歌词已找到 · 暂未同步，可在「歌词」中查看':'';
    if(!el.textContent)el.hidden=true;
  }
  async function remember(id,data){
    const previous=entries.get(id);entries.set(id,data);render();stageStatus();
    if(active(data)){clearTimeout(timers.get(id));timers.set(id,setTimeout(()=>poll(id),1800));}
    else{clearTimeout(timers.get(id));timers.delete(id);if(previous&&active(previous))await refresh();}
  }
  async function poll(id){
    try{await remember(id,await api(`/api/tracks/${id}/lyrics/auto`));}
    catch{clearTimeout(timers.get(id));timers.set(id,setTimeout(()=>poll(id),6000));}
  }
  async function prepare(id,retry=false,query=''){
    const old=entries.get(id);if(active(old)&&!retry){render();return;}
    entries.set(id,{status:'queued',message:'正在准备歌词…'});render();stageStatus();
    try{await remember(id,await api(`/api/tracks/${id}/lyrics/auto`,{method:'POST',body:JSON.stringify({retry,query})}));}
    catch(error){entries.set(id,{status:'unavailable',message:error.message});render();stageStatus();}
  }
  async function open(id){
    if(!trackById(id)?.has_audio)return;selected=id;const epoch=++openEpoch;
    document.dispatchEvent(new Event('openAutoLyrics'));$('#auto-more').open=false;$('#auto-text').open=false;$('#auto-query').value='';
    (document.fullscreenElement||document.body).append(dialog);dialog.showModal();render();
    try{const data=await api(`/api/tracks/${id}/lyrics/auto`);if(epoch!==openEpoch)return;await remember(id,data);if(data.status==='idle'&&enabled)prepare(id);}
    catch(error){entries.set(id,{status:'unavailable',message:error.message});render();}
  }
  function ensure(){
    stageStatus();const track=trackById(currentId);
    if(!enabled||view!=='room'||audio.mode!=='audio'||!track?.has_audio||track.status!=='ready')return;
    const key=`${track.id}/${track.revision}/${track.title}/${track.artist}`;
    if(attempted.has(key))return;attempted.add(key);prepare(track.id);
  }
  $('#lyrics-settings').addEventListener('click',()=>open(currentId));document.addEventListener('manageLyrics',e=>open(e.detail));
  $('#auto-close').addEventListener('click',()=>dialog.close());$('#auto-dismiss').addEventListener('click',()=>dialog.close());
  dialog.addEventListener('close',()=>{if(dialog.open)return;++openEpoch;room.dispatchEvent(new Event('pointermove'));});
  $('#auto-retry').addEventListener('click',()=>prepare(selected,true));
  $('#auto-query-form').addEventListener('submit',e=>{e.preventDefault();const query=$('#auto-query').value.trim();if(query)prepare(selected,true,query);});
  $('#auto-cancel').addEventListener('click',async()=>{const id=selected,data=entries.get(id);if(!data?.id)return;try{await remember(id,await api(`/api/tracks/${id}/lyrics/auto/${data.id}/cancel`,{method:'POST'}));}catch(error){toast(error.message);}});
  $('#auto-manual').addEventListener('click',()=>{const id=selected;dialog.close();document.dispatchEvent(new CustomEvent('manualLyrics',{detail:id}));});
  $('#auto-enabled').addEventListener('change',()=>{enabled=$('#auto-enabled').checked;try{localStorage.setItem('lsj-auto-lyrics',enabled?'on':'off');}catch{}if(enabled){attempted.clear();ensure();}});
  document.addEventListener('viewchange',()=>queueMicrotask(ensure));document.addEventListener('playerchange',()=>queueMicrotask(ensure));
  audio.addEventListener('modechange',()=>queueMicrotask(ensure));
})();
