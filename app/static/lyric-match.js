'use strict';
// Search, editable text, then a persisted alignment draft. Only Apply replaces saved lyrics.
(() => {
  const dialog=$('#match-dialog'), preview=$('#match-preview-audio');
  let tid=null, generation=0, timer=0, task=null, source='手动输入', previewEnd=null, renderedTask=null;
  function step(name) {
    for(const key of ['search','text','review']) $('#match-'+key+'-panel').hidden=key!==name;
    $$('.match-steps span').forEach(e=>e.classList.toggle('active',e.dataset.step===name));
    $('#match-error').textContent='';
    if(name!=='review')preview.pause();
  }
  function activeTask() {return task && ['queued','running'].includes(task.status);}
  function showTask() {
    const active=activeTask();$('#match-progress').hidden=!active;
    ['match-text','match-language','match-scope','match-start','match-back-search'].forEach(id=>$('#'+id).disabled=!!active);
    if(active) {
      step('text');$('#match-phase').textContent=task.phase;
      if(task.percent===null)$('#match-progress-bar').removeAttribute('value');else $('#match-progress-bar').value=task.percent;
    } else if(task?.status==='done') {
      if(renderedTask!==task.id)renderReview();
    } else if(task?.status==='error') $('#match-error').textContent=task.error;
    else if(task?.status==='cancelled') $('#match-error').textContent='本次处理已取消；已保存的歌词保持不变。';
  }
  async function poll(token=generation) {
    if(!dialog.open||token!==generation)return;clearTimeout(timer);
    try {
      const result=await api(`/api/tracks/${tid}/lyrics/alignment`);
      if(token!==generation||!dialog.open)return;
      task=result.task;showTask();
      if(activeTask())timer=setTimeout(()=>poll(token),1500);
    } catch(error) {if(token===generation){$('#match-error').textContent=error.message;timer=setTimeout(()=>poll(token),4000);}}
  }
  async function open() {
    const track=trackById(currentId);if(!track?.has_audio)return;
    clearTimeout(timer);
    $('#match-search-form button').disabled=false;$('#match-apply').disabled=false;
    tid=track.id;const token=++generation;task=null;renderedTask=null;source='手动输入';
    $('#match-song').textContent=[track.title,track.artist].filter(Boolean).join(' · ');
    $('#match-query').value=[track.title,track.artist].filter(Boolean).join(' ').slice(0,200);
    $('#match-existing').disabled=!track.has_lyrics;
    $('#match-results').innerHTML='<p class="match-placeholder">找到文字，再让它跟上这场演唱。</p>';
    $('#match-text').value='';$('#match-source').textContent='';$('#match-scope').value=track.source_duration>900?'clip':'source';
    step('search');dialog.showModal();
    try {
      const result=await api(`/api/tracks/${tid}/lyrics/alignment`);
      if(token!==generation||!dialog.open)return;
      task=result.task;
      if(task && ['queued','running','done','error','cancelled'].includes(task.status)) {
        source=task.source;$('#match-text').value=task.text;$('#match-language').value=task.language;$('#match-scope').value=task.scope;$('#match-source').textContent=source;
        step('text');showTask();if(activeTask())timer=setTimeout(()=>poll(token),1500);
      } else showTask();
    } catch(error) {if(token===generation)$('#match-error').textContent=error.message;}
  }
  $('#match-lyrics').addEventListener('click',open);$('#lyric-empty-import').addEventListener('click',open);
  $('#match-close').addEventListener('click',()=>dialog.close());
  // A native close event can arrive after a rapid reopen; it must not invalidate the new session.
  dialog.addEventListener('close',()=>{if(dialog.open)return;++generation;clearTimeout(timer);preview.pause();preview.removeAttribute('src');preview.load();previewEnd=null;});
  document.addEventListener('playerchange',()=>{if(dialog.open&&currentId!==tid)dialog.close();$('#match-lyrics').disabled=!trackById(currentId)?.has_audio;});
  $('#match-search-form').addEventListener('submit',async e=>{
    e.preventDefault();const token=++generation, button=$('button',e.target);button.disabled=true;$('#match-error').textContent='';$('#match-results').textContent='正在查找歌词候选…';
    try {
      const result=await api(`/api/tracks/${tid}/lyrics/search`,{method:'POST',body:JSON.stringify({query:$('#match-query').value.trim(),provider:$('#match-provider').value})});
      if(token!==generation||!dialog.open)return;
      const results=$('#match-results');results.replaceChildren();
      if(!result.items.length)results.textContent='没有找到匹配版本。试试简化歌名、改用原唱姓名，或切换来源。';
      result.items.forEach(item=>{
        const b=document.createElement('button');b.className='match-candidate';b.type='button';
        const info=document.createElement('span'),title=document.createElement('strong'),meta=document.createElement('small'),action=document.createElement('span');
        title.textContent=item.title;meta.textContent=[item.artist,item.album,time(item.duration),item.source].filter(Boolean).join(' · ');action.textContent='查看歌词 →';
        info.append(title,meta);b.append(info,action);b.addEventListener('click',()=>select(item,b));results.append(b);
      });
    } catch(error) {if(token===generation){$('#match-results').textContent='可以切换来源后再试，或直接粘贴歌词。';$('#match-error').textContent=error.message;}}
    finally {button.disabled=false;}
  });
  async function select(item,button) {
    const token=++generation;button.disabled=true;$('#match-error').textContent='';
    try {
      const result=await api(`/api/lyrics/candidates/${item.token}`);
      if(token!==generation||!dialog.open)return;
      source=`${result.source} · ${result.title} · ${result.artist}`.slice(0,300);
      $('#match-source').textContent=source;$('#match-text').value=result.text;step('text');task=null;showTask();
    } catch(error) {if(token===generation)$('#match-error').textContent=error.message;}
    finally {button.disabled=false;}
  }
  $('#match-paste').addEventListener('click',()=>{source='手动输入';$('#match-source').textContent='粘贴已有歌词，无需 LRC 文件。';task=null;step('text');showTask();$('#match-text').focus();});
  $('#match-existing').addEventListener('click',async()=>{
    const token=++generation;
    try {const data=await api(`/api/tracks/${tid}/lyrics`);if(token!==generation||!dialog.open)return;source='已保存的歌词文字';$('#match-source').textContent=source;$('#match-text').value=data.lines.filter(l=>l.text).map(l=>l.text).join('\n');task=null;step('text');showTask();}
    catch(error){if(token===generation)$('#match-error').textContent=error.message;}
  });
  $('#match-back-search').addEventListener('click',()=>{++generation;step('search');});
  $('#match-start').addEventListener('click',async()=>{
    const text=$('#match-text').value.trim();if(!text){$('#match-error').textContent='先填写这场演唱的歌词。';return;}
    const token=++generation;$('#match-start').disabled=true;$('#match-error').textContent='';renderedTask=null;
    try {
      const result=await api(`/api/tracks/${tid}/lyrics/alignment`,{method:'POST',body:JSON.stringify({text,source,language:$('#match-language').value,scope:$('#match-scope').value})});
      if(token!==generation||!dialog.open)return;
      task=result;showTask();timer=setTimeout(()=>poll(token),1000);
    } catch(error){if(token===generation){$('#match-error').textContent=error.message;$('#match-start').disabled=false;}}
  });
  $('#match-cancel-task').addEventListener('click',async()=>{
    if(!task)return;const token=generation;$('#match-cancel-task').disabled=true;
    try{const result=await api(`/api/tracks/${tid}/lyrics/alignment/${task.id}/cancel`,{method:'POST'});if(token===generation){task=result;showTask();}}
    catch(error){if(token===generation)$('#match-error').textContent=error.message;}
    finally{$('#match-cancel-task').disabled=false;}
  });
  function renderReview() {
    renderedTask=task.id;step('review');const container=$('#match-lines');container.replaceChildren();
    const uncertain=task.lines.filter(l=>l.review).length;
    const missed=task.lines.filter(l=>l.end-l.start<.15).length;
    $('#match-review-summary').textContent=`生成 ${task.lines.length} 句初稿 · ${uncertain} 句建议重点检查${missed ? `，其中 ${missed} 句未定位或时长过短` : ''}。`+(uncertain>task.lines.length/2?'本次结果不够可靠，建议先核对歌词版本、段落和语言，再重新对齐。':'模型提示不代表准确率，其余句子也请试听确认。');
    preview.src=`/api/tracks/${tid}/audio?kind=source`;
    task.lines.forEach((line,index)=>{
      const row=document.createElement('div');row.className='match-line'+(line.review?' needs-review':'');row.dataset.index=index;
      const words=document.createElement('input');words.className='line-words';words.value=line.text;words.maxLength=500;words.setAttribute('aria-label',`第 ${index+1} 句歌词`);row.append(words);
      const actions=document.createElement('div');actions.className='line-actions';
      const listen=document.createElement('button');listen.className='button subtle';listen.textContent='▶ 试听这句';listen.addEventListener('click',()=>{audio.pause();$('#preview-audio').pause();preview.currentTime=Number(row.querySelector('[data-time=start]').value);previewEnd=Number(row.querySelector('[data-time=end]').value);preview.play().catch(e=>{$('#match-error').textContent=e.message;});});actions.append(listen);
      const mark=document.createElement('button');mark.className='button subtle';mark.textContent='此刻开始';mark.addEventListener('click',()=>{row.querySelector('[data-time=start]').value=preview.currentTime.toFixed(2);});actions.append(mark);row.append(actions);
      for(const [key,label] of [['start','开始'],['end','结束']]){const wrap=document.createElement('label');wrap.textContent=label;const input=document.createElement('input');input.type='number';input.step='.01';input.min='0';input.max=task.source_duration;input.value=line[key];input.dataset.time=key;input.setAttribute('aria-label',`第 ${index+1} 句${label}秒数`);wrap.append(input);row.append(wrap);}
      if(line.review){const note=document.createElement('p');note.className='line-note';note.textContent='待检查：'+line.review;row.append(note);}container.append(row);
    });
  }
  preview.addEventListener('play',()=>{audio.pause();$('#preview-audio').pause();});
  audio.addEventListener('play',()=>preview.pause());$('#preview-audio').addEventListener('play',()=>preview.pause());
  preview.addEventListener('timeupdate',()=>{if(previewEnd!==null&&preview.currentTime>=previewEnd){preview.pause();previewEnd=null;}});
  preview.addEventListener('error',()=>{if(dialog.open&&preview.getAttribute('src'))$('#match-error').textContent='原音轨暂时无法试听，请检查本地服务。';});
  $('#match-redo').addEventListener('click',()=>{renderedTask=null;step('text');});
  $('#match-apply').addEventListener('click',async()=>{
    if(!task)return;let previous=-1, invalid=null;
    const lines=$$('.match-line').map(row=>{const line={text:$('.line-words',row).value.trim(),start:Number($('[data-time=start]',row).value),end:Number($('[data-time=end]',row).value)};const bad=!line.text||!Number.isFinite(line.start)||!Number.isFinite(line.end)||line.start<0||line.start>=line.end||line.start<previous||line.end>task.source_duration+.05;row.classList.toggle('invalid',bad);if(bad&&!invalid)invalid=row;previous=line.end;return line;});
    if(invalid){$('#match-error').textContent='请修正红框句子的文字或时间：开始早于结束、按顺序排列且不重叠。';invalid.scrollIntoView({block:'center'});return;}
    const token=generation;$('#match-apply').disabled=true;
    try {await api(`/api/tracks/${tid}/lyrics/alignment/${task.id}/apply`,{method:'POST',body:JSON.stringify({lines})});if(token!==generation)return;dialog.close();await refresh();toast('已保存这场演唱的歌词时间轴。');}
    catch(error){if(token===generation)$('#match-error').textContent=error.message;}
    finally{$('#match-apply').disabled=false;}
  });
})();
