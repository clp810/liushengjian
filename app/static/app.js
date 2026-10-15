'use strict';
const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];
const audio = new CollectionPlayer($('#audio'), $('#live-video'));
let tracks = [], jobs = [], view = 'library', currentId = null, editId = null, exportId = null;
let previousData = '', toastTimer, pollTimer, previewEnd = null;
const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const time = seconds => { seconds = Math.max(0, Number(seconds) || 0); return `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`; };
const preciseTime = s => `${Math.floor(s / 60)}:${(s % 60).toFixed(2).padStart(5, '0')}`;
function parseTime(value) { const parts = value.trim().split(':'); if (parts.length > 3 || parts.some(p => !/^\d+(\.\d+)?$/.test(p))) throw Error('时间格式应为 分:秒，或直接填写秒数。'); return parts.reduce((sum, p) => sum * 60 + Number(p), 0); }
function toast(message) { clearTimeout(toastTimer); $('#toast').textContent = message; $('#toast').classList.add('visible'); toastTimer = setTimeout(() => $('#toast').classList.remove('visible'), 4500); }
async function api(path, options = {}) {
  const headers = {...(options.body && !(options.body instanceof FormData) ? {'Content-Type':'application/json'} : {}), ...(options.headers || {})};
  let response;
  try { response = await fetch(path, {...options, headers}); } catch { throw Error('本地服务没有连接上，请重新打开「启动留声间.command」。'); }
  if (!response.ok) { let error; try { error = await response.json(); } catch { error = {}; } const detail = error.detail; throw Error(typeof detail === 'string' ? detail : Array.isArray(detail) ? '输入内容不完整或格式不正确，请检查后重试。' : '操作未完成，请稍后重试。'); }
  return response.json();
}
const trackById = id => tracks.find(t => t.id === id);
function coverHTML(track, large = false) {
  if (track?.has_cover && ['original','uploaded'].includes(track.cover_style || 'original')) return `<img src="/api/tracks/${track.id}/cover?kind=${track.cover_style || 'original'}&v=${encodeURIComponent(track.updated_at)}" alt="" loading="lazy">`;
  const palette = ['groove','collage','type','dusk'].includes(track?.cover_style) ? track.cover_style : ['clay','sage','ink','honey'][parseInt((track?.id || '00').slice(0, 2), 16) % 4];
  return `<div class="generated-cover ${palette} ${large ? 'large' : ''} ${((track?.title || '').length>18||/[A-Za-z]{9}/.test(track?.title||''))?'title-long':''}"><small>留声间 · PRIVATE COLLECTION</small><div class="cover-lines"></div><strong>${escapeHTML((track?.title || '留声间').slice(0,24))}</strong><span>${escapeHTML(track?.artist || 'A SOUND TO KEEP')}</span></div>`;
}
function setView(next) {
  view = next; document.body.dataset.view = view; document.dispatchEvent(new Event('viewchange'));
  $$('.nav[data-view]').forEach(b => b.classList.toggle('active', b.dataset.view === view));
  $('#library-view').hidden = ['room','tasks'].includes(view);
  $('#room-view').hidden = view !== 'room'; $('#tasks-view').hidden = view !== 'tasks';
  const names = {library:'全部收藏',favorites:'心动版本',room:'听音室',tasks:'导入记录',archive:'已收起'};
  $('#breadcrumb').textContent = `我的空间 / ${names[view]}`;
  const headings = {library:['声音，在这里有了形状。','挑一张唱片，把时间留给喜欢的声音。','我的唱片墙'],favorites:['想再听一遍的，留在这里。','把心动的声音，放在离自己更近的地方。','心动版本'],archive:['暂时收起，想听时再回来。','音频仍然保存在本机，随时可以恢复到唱片墙。','已收起的收藏']};
  if (headings[view]) { $('#page-title').innerHTML = headings[view][0]; $('#page-description').textContent = headings[view][1]; $('#list-title').textContent = headings[view][2]; }
  renderLibrary(); renderJobs();
}
function renderLibrary() {
  const focused = document.activeElement?.closest('#track-grid [data-action], #track-grid .card-menu summary');
  const focusKey = focused ? {id:focused.closest('[data-id]').dataset.id, action:focused.dataset.action || 'menu'} : null;
  const openMenus = $$('#track-grid details[open]').map(d=>d.closest('[data-id]').dataset.id);
  const active = tracks.filter(t => !t.archived);
  $('#nav-count').textContent = active.length;
  const ready = active.filter(t => t.status === 'ready');
  $('#collection-stats').textContent = `${ready.length} 个收藏版本 · ${ready.length && ready.reduce((sum,t) => sum+t.duration,0) < 60 ? '不足 1' : Math.round(ready.reduce((sum,t) => sum+t.duration,0)/60)} 分钟声音记忆`;
  const query = $('#search').value.trim().toLowerCase();
  let shown = tracks.filter(t => (view === 'archive' ? t.archived : !t.archived) && (view !== 'favorites' || t.favorite) && [t.title,t.artist,t.version,t.album].join(' ').toLowerCase().includes(query));
  const sort = $('#sort').value;
  if (sort !== 'new') shown.sort((a,b) => a[sort].localeCompare(b[sort], 'zh-CN'));
  $('#result-count').textContent = `${shown.length} 个版本`;
  $('#track-grid').innerHTML = shown.map(t => {
    const job = jobs.find(j => j.track_id === t.id && ['queued','running'].includes(j.status));
    const isPlaying = t.id === currentId && !audio.paused;
    return `<article class="track-card ${t.id === currentId ? 'selected' : ''}" data-id="${t.id}"><div class="artwork">${coverHTML(t)}<button class="inspect-cover" data-action="inspect" aria-label="查看 ${escapeHTML(t.title)} 的唱片详情"></button><span class="source-badge">${t.source_kind === 'bilibili' ? 'B站收藏' : '本地音源'}</span>${t.status === 'ready' ? `<button class="card-play" data-action="play" aria-label="播放 ${escapeHTML(t.title)}">${isPlaying ? 'Ⅱ' : '▶'}</button><span class="duration-badge">${t.has_video ? 'LIVE · ' : ''}${time(t.duration)}</span>` : `<div class="processing-overlay">${t.status === 'error' ? '!' : '<span class="spinner"></span>'}</div>`}</div><div class="track-meta"><div class="track-title-row"><h3 title="${escapeHTML(t.title)}">${escapeHTML(t.title)}</h3><button class="heart ${t.favorite ? 'on' : ''}" data-action="favorite" aria-label="${t.favorite ? '取消心动' : '设为心动'}" aria-pressed="${t.favorite}">${t.favorite ? '♥' : '♡'}</button></div><p class="artist">${escapeHTML(t.artist || '演唱者待补充')}</p><p class="version">${escapeHTML(t.version || t.album || '自己的音乐收藏')}</p>${t.status === 'processing' ? `<p class="track-status">${escapeHTML(job?.phase || '正在处理…')}</p>` : t.error ? `<p class="track-error">${escapeHTML(t.error)}</p>` : ''}<details class="card-menu"><summary aria-label="${escapeHTML(t.title)} 的更多操作">•••</summary><div class="card-actions">${t.archived ? '<button data-action="restore">恢复收藏 ↗</button>' : t.status === 'ready' ? '<button data-action="room">进入听音室</button><button data-action="edit">编辑版本</button><button data-action="export">导出 ↗</button><button class="archive-button" data-action="archive" aria-label="收起这首收藏">收起</button>' : t.status === 'error' ? '<button data-action="retry">重新尝试 ↻</button><button data-action="archive">收起</button>' : '<span>完成后即可试听</span>'}</div></details></div></article>`;
  }).join('');
  openMenus.forEach(id=>{const menu=$(`[data-id="${id}"] details`);if(menu)menu.open=true;});
  if(focusKey) $(`[data-id="${focusKey.id}"] ${focusKey.action==='menu'?'.card-menu summary':`[data-action="${focusKey.action}"]`}`)?.focus({preventScroll:true});
  document.dispatchEvent(new Event('libraryrender'));
  $('#empty-state').hidden = shown.length > 0;
  if (!shown.length) { $('#empty-state h3').textContent = query ? '没有找到这个版本' : view === 'favorites' ? '把心动的版本，放在这里。' : view === 'archive' ? '这里还没有收起的收藏。' : '给喜欢的声音，留一个位置。'; $('#empty-state p').textContent = query ? '试试其他歌名、演唱者或版本名称。' : view === 'favorites' ? '点击歌曲卡片上的爱心，就能在这里找到它。' : view === 'archive' ? '收起后仍然保留文件，随时可以恢复。' : '粘贴 B站链接，或导入电脑里的音视频文件。'; $('#empty-add').hidden = !!query || view !== 'library'; }
}
function renderJobs() {
  $('#task-dot').hidden = !jobs.some(j => ['queued','running'].includes(j.status));
  $('#job-list').innerHTML = jobs.length ? jobs.map(j => `<div class="job-row"><div class="job-icon ${j.status}">${j.status === 'done' ? '✓' : j.status === 'error' ? '!' : '↧'}</div><div><h3>${escapeHTML(trackById(j.track_id)?.title || '音频任务')}</h3><p>${escapeHTML(j.error || j.phase)}</p><small>${new Date(j.created_at).toLocaleString('zh-CN')} · ${j.kind === 'edit' ? '更新音频' : j.kind === 'video' ? '准备现场视频' : '导入收藏'}</small></div>${j.status === 'error' ? `<button class="button subtle" data-retry="${j.track_id}">重试</button>` : `<span class="job-label">${({done:'已完成',error:'未完成',running:'处理中',queued:'排队中'})[j.status]}</span>`}</div>`).join('') : '<div class="empty-state"><h3>还没有导入记录</h3><p>添加音乐后，这里会显示处理进度和结果。</p></div>';
}
async function refresh() {
  try {
    const result = await Promise.all([api('/api/tracks'), api('/api/jobs')]);
    const serialized = JSON.stringify(result);
    if (serialized !== previousData) { const oldCurrent = trackById(currentId); [tracks, jobs] = result; previousData = serialized; renderLibrary(); renderJobs(); const nowCurrent = trackById(currentId); if (nowCurrent && oldCurrent && oldCurrent.revision !== nowCurrent.revision && nowCurrent.status === 'ready') { const playing = !audio.paused; const position = audio.currentTime; audio.setTrack(nowCurrent); audio.src = `/api/tracks/${currentId}/audio?v=${nowCurrent.revision}`; audio.currentTime = Math.min(position, nowCurrent.duration); if (playing) audio.play().catch(() => {}); } renderPlayer(); }
    $('#environment-status').textContent = '● 本地空间已连接';
  } catch (e) { $('#environment-status').textContent = '本地服务未连接'; }
  clearTimeout(pollTimer); pollTimer = setTimeout(refresh, jobs.some(j => ['queued','running'].includes(j.status)) ? 1500 : 5000);
}
async function mutate(path, method, body) { const result = await api(path,{method,body: body ? JSON.stringify(body) : undefined}); await refresh(); return result; }
function renderPlayer() {
  document.dispatchEvent(new Event('playerchange'));
  const t = trackById(currentId);
  if (!t) { $('#room-cover').innerHTML = coverHTML(null,true); return; }
  audio.setTrack(t);
  $('#mode-video').disabled = !t.has_video; $('#mode-audio').setAttribute('aria-pressed',audio.mode==='audio'); $('#mode-video').setAttribute('aria-pressed',audio.mode==='video');
  $('#live-video').hidden = audio.mode !== 'video'; $('#room-cover').hidden = audio.mode === 'video'; $('#room-stage').classList.toggle('watching',audio.mode==='video');
  $('#video-hint').textContent = t.has_video ? '现场已保存在本地 · 切换时接着当前片段播放' : t.can_prepare_video ? '这份收藏仅有音频，可以从来源准备现场视频。' : '这份收藏为纯音频；观看现场需要导入带画面的文件。';
  $('#prepare-video').hidden = t.has_video || !t.can_prepare_video || t.status !== 'ready'; $('#room-note').textContent = t.note || '';
  $('#player-title').textContent = t.title + (t.version ? ` · ${t.version}` : ''); $('#player-artist').textContent = t.artist || '演唱者待补充';
  $('#player-cover').innerHTML = coverHTML(t); $('#room-cover').innerHTML = coverHTML(t,true); $('#room-title').textContent = t.title; $('#room-artist').textContent = [t.artist,t.version].filter(Boolean).join(' · ');
  $('#player-favorite').disabled = false; $('#player-favorite').textContent = t.favorite ? '♥' : '♡'; $('#player-favorite').classList.toggle('on',t.favorite);
  $('#room-seek').disabled = false; $('#room-seek').max = t.duration; $('#room-duration').textContent = time(t.duration); $('#play-toggle').disabled = false; $('#seek').disabled = false; $('#total-time').textContent = time(t.duration); updatePlayButton();
}
function updatePlayButton() { $('#play-toggle').textContent = audio.paused ? '▶' : 'Ⅱ'; $('#play-toggle').setAttribute('aria-label',audio.paused ? '播放' : '暂停'); $('#room-play').textContent = audio.paused ? '开始播放' : '暂停播放'; }
async function playTrack(id) {
  const t = trackById(id); if (!t || !t.has_audio) return;
  $('#preview-audio').pause();
  if (currentId === id) { if (audio.paused) await audio.play(); else audio.pause(); }
  else { currentId = id; audio.setTrack(t); audio.src = `/api/tracks/${id}/audio?v=${t.revision}`; await audio.play(); }
  renderPlayer(); renderLibrary();
}
function playNext(direction, ended = false) { const available = tracks.filter(t => t.status === 'ready' && !t.archived); if (!available.length) return; let index = available.findIndex(t => t.id === currentId); if (ended && index === available.length - 1) { updatePlayButton(); return; } if (index < 0 && direction < 0) index = 0; const next = available[(index+direction+available.length)%available.length]; if (next?.id !== currentId) playTrack(next.id).catch(e => toast(e.message)); else { audio.currentTime = 0; audio.play().catch(()=>{}); } }
async function favorite(id) { const t = trackById(id); if (t) await mutate(`/api/tracks/${id}`,'PATCH',{favorite:!t.favorite}); }
function showImport() { $('#import-error').textContent = ''; $('#import-dialog').showModal(); }
function openEdit(id) {
  const t = trackById(id); if (!t || t.status !== 'ready') return;
  editId = id; previewEnd = null; $('#edit-error').textContent = ''; $('#edit-cover').innerHTML = coverHTML(t); $('#edit-source-title').textContent = t.source_title || t.title; $('#source-duration').textContent = `原始音频 ${time(t.source_duration)} · 所有裁剪均可重新调整`;
  const form = $('#edit-form'); for (const key of ['title','artist','album','version','note']) form.elements[key].value = t[key];
  form.elements.trim_start.value = preciseTime(t.trim_start); form.elements.trim_end.value = preciseTime(t.trim_end ?? t.source_duration);
  $('#source-link').hidden = !t.source_url; $('#source-link').href = t.source_url || '#'; $('#preview-audio').src = `/api/tracks/${id}/audio?kind=source`;
  renderCoverChoices(); $('#edit-dialog').showModal();
}
function openExport(id) { const t = trackById(id); if (!t?.has_audio) return; exportId = id; $('#export-title').textContent = [t.artist,t.title,t.version].filter(Boolean).join(' · '); $('#quality-note').textContent = t.quality_note || '音质取决于原始音源。'; for (const ext of ['m4a','mp3']) $(`#export-${ext}`).href = `/api/tracks/${id}/audio?kind=${ext}&download=true`; $('#export-dialog').showModal(); }
$$('.nav[data-view]').forEach(b=>b.addEventListener('click',()=>setView(b.dataset.view)));
$$('#add-button,#empty-add').forEach(b=>b.addEventListener('click',showImport));
$('#help-button').addEventListener('click',()=>$('#help-dialog').showModal());
$$('.close-dialog').forEach(b=>b.addEventListener('click',()=>b.closest('dialog').close()));
$('#edit-dialog').addEventListener('close',()=>{ $('#preview-audio').pause(); previewEnd = null; });
$$('dialog').forEach(d=>d.addEventListener('click',e=>{ if (e.target===d) { const r=d.getBoundingClientRect(); if (e.clientX<r.left || e.clientX>r.right || e.clientY<r.top || e.clientY>r.bottom) d.close(); }}));
$$('[data-import]').forEach(b=>b.addEventListener('click',()=>{ $$('[data-import]').forEach(t=>t.classList.toggle('active',t===b)); $('#url-form').hidden=b.dataset.import!=='url'; $('#file-form').hidden=b.dataset.import!=='file'; $('#import-error').textContent=''; }));
$('#search').addEventListener('input',renderLibrary); $('#sort').addEventListener('change',renderLibrary);
$('#track-grid').addEventListener('click',async e=>{ const button=e.target.closest('[data-action]'); if (!button) return; const id=button.closest('[data-id]').dataset.id; try { switch(button.dataset.action) { case 'inspect': document.dispatchEvent(new CustomEvent('inspecttrack',{detail:id})); break; case 'play': await playTrack(id); break; case 'room': if(currentId!==id) await playTrack(id); setView('room'); break; case 'favorite': await favorite(id); break; case 'edit': openEdit(id); break; case 'export': openExport(id); break; case 'retry': await mutate(`/api/tracks/${id}/retry`,'POST'); toast('已重新加入处理队列'); break; case 'archive': case 'restore': await mutate(`/api/tracks/${id}`,'PATCH',{archived:button.dataset.action==='archive'}); toast(button.dataset.action==='archive'?'已收起，可以在「已收起」中恢复':'已恢复到唱片墙'); break; } } catch(error) { toast(error.message); } });
$('#job-list').addEventListener('click',async e=>{const b=e.target.closest('[data-retry]'); if (b) try { await mutate(`/api/tracks/${b.dataset.retry}/retry`,'POST'); } catch(error) { toast(error.message); }});
$('#url-form').addEventListener('submit',async e=>{e.preventDefault(); const b=$('button[type=submit]',e.target); b.disabled=true; $('#import-error').textContent=''; try { const r=await api('/api/imports/url',{method:'POST',body:JSON.stringify({url:$('#video-url').value,keep_video:$('#import-content').value==='video',cover_style:$('#import-cover').value})}); $('#import-dialog').close(); await refresh(); setView('library'); toast(r.duplicate?'这个视频已经在收藏库中，可直接编辑或重试。':'已开始准备收藏，可在导入记录查看进度。'); } catch(error){$('#import-error').textContent=error.message;} finally{b.disabled=false;}});
$('#file-form').addEventListener('submit',async e=>{e.preventDefault(); const b=$('button[type=submit]',e.target); b.disabled=true; b.textContent='正在导入文件…'; $('#import-error').textContent=''; try { const file=$('#media-file').files[0]; if (!file) throw Error('请先选择文件。'); if (file.size>512*1024*1024) throw Error('单个文件最多 512 MB。'); const data=new FormData(); data.append('file',file); data.append('keep_video',$('#import-content').value==='video'); data.append('cover_style',$('#import-cover').value); await api('/api/imports/file',{method:'POST',body:data}); $('#import-dialog').close(); await refresh(); setView('library'); toast('文件已保存，正在准备可播放的音频。'); } catch(error){$('#import-error').textContent=error.message;} finally{b.disabled=false;b.textContent='导入并加入收藏';}});
$('#edit-form').addEventListener('submit',async e=>{e.preventDefault(); const b=$('button[type=submit]',e.target); b.disabled=true; $('#edit-error').textContent=''; try{ const form=e.target; const body=Object.fromEntries(['title','artist','album','version','note'].map(k=>[k,form.elements[k].value.trim()])); body.trim_start=parseTime(form.elements.trim_start.value); body.trim_end=parseTime(form.elements.trim_end.value); await mutate(`/api/tracks/${editId}`,'PUT',body); $('#edit-dialog').close(); toast('正在更新音频，原始音轨会保留。'); } catch(error){$('#edit-error').textContent=error.message;} finally{b.disabled=false;}});
$('#reset-trim').addEventListener('click',()=>{const t=trackById(editId); $('#edit-form').elements.trim_start.value='0:00'; $('#edit-form').elements.trim_end.value=preciseTime(t.source_duration); previewEnd=null;});
$('#preview-trim').addEventListener('click',async()=>{try { const form=$('#edit-form'),t=trackById(editId); const start=parseTime(form.elements.trim_start.value),end=parseTime(form.elements.trim_end.value); if(end-start<.25 || end>t.source_duration+.05) throw Error('请设置有效的开始和结束时间。'); audio.pause(); previewEnd=end; $('#preview-audio').currentTime=start; await $('#preview-audio').play(); } catch(error){$('#edit-error').textContent=error.message;}});
$('#preview-audio').addEventListener('play',()=>audio.pause());
$('#preview-audio').addEventListener('timeupdate',()=>{if(previewEnd!==null && $('#preview-audio').currentTime>=previewEnd) { $('#preview-audio').pause(); previewEnd=null; }});
$('#preview-audio').addEventListener('error',()=>{$('#edit-error').textContent='原音轨暂时无法试听，请等待处理完成后重试。';});
$('#cover-file').addEventListener('change',async e=>{const file=e.target.files[0];if(!file)return;try {const data=new FormData();data.append('file',file);await api(`/api/tracks/${editId}/cover`,{method:'POST',body:data});await refresh();$('#edit-cover').innerHTML=coverHTML(trackById(editId));renderCoverChoices();toast('封面已保存');}catch(error){$('#edit-error').textContent=error.message;} e.target.value='';});
$('#reveal-file').addEventListener('click',async()=>{try{await api(`/api/tracks/${exportId}/reveal`,{method:'POST'});}catch(error){toast(error.message);}});
$('#play-toggle').addEventListener('click',()=>{if(currentId)playTrack(currentId).catch(e=>toast(e.message));});
$('#room-play').addEventListener('click',()=>{if(currentId)playTrack(currentId).catch(e=>toast(e.message));else toast('先在唱片墙选择一首音乐。');});
$('#previous').addEventListener('click',()=>playNext(-1)); $('#next').addEventListener('click',()=>playNext(1));
$('#player-favorite').addEventListener('click',()=>favorite(currentId).catch(e=>toast(e.message)));
$('#open-room').addEventListener('click',()=>setView('room'));
$('#volume').addEventListener('input',e=>{audio.volume=Number(e.target.value);localStorage.setItem('lsj-volume',e.target.value);});
$('#seek').addEventListener('input',e=>{if(Number.isFinite(audio.duration))audio.currentTime=Number(e.target.value);});
$('#repeat').addEventListener('click',()=>{audio.loop=!audio.loop;$('#repeat').setAttribute('aria-pressed',audio.loop);$('#repeat').classList.toggle('on',audio.loop);toast(audio.loop?'已开启单曲循环':'已关闭单曲循环');});
audio.addEventListener('timeupdate',()=>{$('#current-time').textContent=time(audio.currentTime);$('#seek').value=audio.currentTime;});
audio.addEventListener('loadedmetadata',()=>{$('#seek').max=audio.duration;$('#total-time').textContent=time(audio.duration);});
audio.addEventListener('play',()=>{updatePlayButton();renderLibrary();});audio.addEventListener('pause',()=>{updatePlayButton();renderLibrary();});
audio.addEventListener('ended',()=>playNext(1,true));audio.addEventListener('error',()=>toast('播放未成功，请确认本地服务正在运行，或重新导入这个文件。'));
function setTheme(theme){if(!['warm','night','forest'].includes(theme))theme='warm';$('.listening-room').dataset.theme=theme;$$('.theme').forEach(b=>b.classList.toggle('active',b.dataset.theme===theme));localStorage.setItem('lsj-theme',theme);}
$$('.theme').forEach(b=>b.addEventListener('click',()=>setTheme(b.dataset.theme)));
setTheme(localStorage.getItem('lsj-theme')||'warm');audio.volume=Math.max(0,Math.min(1,Number(localStorage.getItem('lsj-volume')??.8)));$('#volume').value=audio.volume;
api('/api/status').then(s=>{$('#storage-path').textContent=s.storage;if(!s.ffmpeg||!s.ffprobe)toast('需要安装 FFmpeg 才能处理音频，请查看 README 中的启动说明。');}).catch(e=>toast(e.message));
renderImportCover();renderPlayer();refresh();

const presetNames = {original:'原始／默认',uploaded:'上传的封面',groove:'声纹',collage:'剪影',type:'字间',dusk:'暮色'};
function renderImportCover() {
  $('#import-cover-preview').innerHTML = coverHTML({id:'00',title:'留住这一刻',artist:'你的私人收藏',cover_style:$('#import-cover').value});
}
function renderCoverChoices() {
  const t=trackById(editId);
  $('#cover-presets').innerHTML=Object.entries(presetNames).filter(([key])=>key!=='uploaded'||t.has_uploaded_cover).map(([key,label])=>`<button type="button" class="cover-choice" data-cover="${key}" aria-pressed="${t.cover_style===key}"><span class="cover-sample">${coverHTML({...t,cover_style:key,has_cover:key==='original'?t.has_original_cover:key==='uploaded'?t.has_uploaded_cover:false})}</span><span>${label}</span></button>`).join('');
}
$('#import-cover').addEventListener('change',renderImportCover);
$('#cover-presets').addEventListener('click',async e=>{
  const button=e.target.closest('[data-cover]');if(!button)return;
  try {await mutate(`/api/tracks/${editId}/cover-choice`,'PUT',{style:button.dataset.cover});$('#edit-cover').innerHTML=coverHTML(trackById(editId));renderCoverChoices();toast('封面已保存');}
  catch(error){$('#edit-error').textContent=error.message;}
});
audio.addEventListener('modechange',renderPlayer);
for(const mode of ['audio','video']) $('#mode-'+mode).addEventListener('click',async()=>{
  const buttons=[$('#mode-audio'),$('#mode-video')];buttons.forEach(b=>b.disabled=true);
  try {await audio.switchMode(mode);}catch(error){toast(error.message);}finally{$('#mode-audio').disabled=false;renderPlayer();}
});
$('#prepare-video').addEventListener('click',async()=>{
  try {await mutate(`/api/tracks/${currentId}/video`,'POST');toast('正在准备本地现场视频，完成后即可观看。');}catch(error){toast(error.message);}
});
$('#room-back').addEventListener('click',async()=>{if(document.fullscreenElement)await document.exitFullscreen();setView('library');});
$('#room-fullscreen').addEventListener('click',async()=>{
  try {if(document.fullscreenElement)await document.exitFullscreen();else await $('#room-view').requestFullscreen();}catch{toast('当前环境无法全屏，请使用浏览器窗口的全屏功能。');}
});
document.addEventListener('fullscreenchange',()=>{$('#room-fullscreen').textContent=document.fullscreenElement?'退出全屏 ↙':'全屏 ↗';});

// Fullscreen keeps the same playback clock, volume and repeat setting.
$('#room-seek').addEventListener('input',e=>{audio.currentTime=Number(e.target.value);});
$('#room-previous').addEventListener('click',()=>playNext(-1));
$('#room-next').addEventListener('click',()=>playNext(1));
$('#room-repeat').addEventListener('click',()=>$('#repeat').click());
$('#repeat').addEventListener('click',()=>$('#room-repeat').setAttribute('aria-pressed',audio.loop));
$('#room-volume').value=audio.volume;
$('#room-volume').addEventListener('input',e=>{$('#volume').value=e.target.value;$('#volume').dispatchEvent(new Event('input'));});
$('#volume').addEventListener('input',e=>{$('#room-volume').value=e.target.value;});
audio.addEventListener('timeupdate',()=>{$('#room-current').textContent=time(audio.currentTime);$('#room-seek').value=audio.currentTime;});
