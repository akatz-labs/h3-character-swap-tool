// One visible segment per diffusion pass. Shot masks and shared settings stay
// in their existing panels; only the selected segment's controls are expanded.
let segmentEditor = null;
function segmentActive(){return !!segmentEditor && S.tab==='render' && segmentEditor.clipId===clip()?.id && segmentEditor.shotId===shot()?.id && !!$('#segment-controls');}
function segmentSelected(){return segmentEditor?.plan?.segments.find(s=>s.id===segmentEditor.selected);}
function segmentStatus(){return segmentEditor?.plan?.states.find(s=>s.id===segmentEditor.selected);}
function segmentTime(frame){const seconds=frame/24;return `${Math.floor(seconds/60)}:${(seconds%60).toFixed(1).padStart(4,'0')}`;}
function segmentPath(){return `clips/${segmentEditor.clipId}/shots/${segmentEditor.shotId}/segments`;}
function segmentDirty(){return !!segmentEditor?.fields?.some(({field})=>field.value!==field.dataset.remoteValue);}
function mergeSegmentClip(c){const i=S.data.clips.findIndex(v=>v.id===c.id);if(i>=0)S.data.clips[i]=c;}
async function flushSegmentEdits(){
  const e=segmentEditor;if(!segmentActive()||!e.fields?.length)return;
  clearTimeout(e.timer);if(e.pending)await e.pending;
  if(e!==segmentEditor)return;
  if(!segmentDirty()){if(!autosaveDirty()){S.dirty=!!maskHasDraft();S.renderDraftDirty=false;autosaveStatus('All changes saved');}return;}
  const run=async()=>{
    while(e===segmentEditor&&segmentDirty()){
      const changes={}, saved=[];
      for(const {field,key} of e.fields){
        if(field.value===field.dataset.remoteValue)continue;
        if(field.type==='number'&&(!field.value||!field.checkValidity()))throw Error('Enter a valid whole-number seed.');
        if(field.dataset.conflict==='true')throw Error('This segment changed elsewhere. Your edits are preserved; reload saved values before continuing.');
        if(key==='anchor_choice'){
          changes.anchor_mode=field.value.startsWith('asset:')?'custom':field.value;
          changes.anchor=field.value.startsWith('asset:')?field.value.slice(6):null;
        }else changes[key]=['seed','history_frames'].includes(key)?Number(field.value):field.value;
        saved.push({field,value:field.value});
      }
      const key=e.selected,base=structuredClone(e.editBase);
      const task=writeQueue.then(async()=>{
        const c=await api(`clips/${e.clipId}`);mergeSegmentClip(c);const s=c.shots.find(s=>s.id===e.shotId);
        const remote=s.segments?.find(v=>v.id===key);
        if(remote)for(const k of Object.keys(changes))if(JSON.stringify(remote[k]??null)!==JSON.stringify(base[k]??null))throw Error('This segment changed elsewhere. Your edits are kept here.');
        const result=await api(`clips/${e.clipId}/shots/${e.shotId}/segments/${key}`,'PATCH',{revision:c.revision,changes});
        mergeSegmentClip(result);return result;
      });writeQueue=task.catch(()=>{});
      autosaveStatus('Saving…');await task;
      for(const {field,value} of saved){field.dataset.remoteValue=value;delete field.dataset.conflict;}
      Object.assign(e.plan.segments.find(s=>s.id===key),changes);Object.assign(e.editBase,changes);
    }
    if(e===segmentEditor){S.dirty=autosaveDirty()||!!maskHasDraft();S.renderDraftDirty=autosaveDirty();autosaveStatus('All changes saved');await loadSegmentPlan(true);}
  };
  e.pending=run().catch(error=>{S.dirty=true;autosaveStatus('Not saved: '+error.message,true);if(error.status===409)e.fields.forEach(({field})=>field.dataset.conflict='true');throw error;});
  try{await e.pending;}finally{e.pending=null;}
}
async function loadSegmentPlan(force=false){
  if(!segmentActive())return;const e=segmentEditor,c=clip();
  if(!force&&e.loadedRevision===c.revision)return;
  const serial=++e.serial;
  try{
    const plan=await api(segmentPath());if(e!==segmentEditor||serial!==e.serial||!segmentActive())return;
    const initial=!e.plan;e.plan=plan;e.loadedRevision=plan.revision;
    if(!plan.segments.some(s=>s.id===e.selected))e.selected=plan.segments[0].id;
    renderSegmentStrip();
    if(!segmentDirty())renderSegmentControls();
    updateSegmentHistory();
    if(initial)maskSeek(segmentSelected().start);
  }catch(error){if(e===segmentEditor){$('#segment-controls').textContent=error.message;autosaveStatus(error.message,true);}}
}
function renderSegmentStrip(){
  const e=segmentEditor;if(!e.plan)return;
  $('#segment-strip').innerHTML=e.plan.segments.map((s,i)=>{
    const state=e.plan.states[i];return `<button class="segment-chip ${s.id===e.selected?'active':''}" style="flex-grow:${s.end-s.start}" data-segment="${esc(s.id)}" aria-pressed="${s.id===e.selected}" title="${s.connection==='continue'?'Continues previous segment':'Starts fresh'}"><span><i class="segment-dot state-${state.status}"></i>Segment ${i+1}</span><small>${segmentTime(s.start)}–${segmentTime(s.end)}</small></button>`;
  }).join('');
  $('#segment-count').textContent=`${e.plan.segments.length} segment${e.plan.segments.length===1?'':'s'}`;
  $$('[data-segment]').forEach(b=>b.onclick=guarded(async()=>{
    await flushShotAutosave();if(!segmentActive())return;e.selected=b.dataset.segment;localStorage.setItem(`h3segment:${e.clipId}:${e.shotId}`,e.selected);
    renderSegmentStrip();renderSegmentControls();updateSegmentHistory();maskSeek(segmentSelected().start);
  }));
}
function renderSegmentControls(){
  const e=segmentEditor,s=segmentSelected(),state=segmentStatus();if(!s)return;
  const i=e.plan.segments.indexOf(s),continuing=s.connection==='continue';
  const active=document.activeElement,focus=e.controlsSegment===s.id&&active?.id&&active.closest('#segment-controls, #segment-text-editors')?{id:active.id,start:active.selectionStart,end:active.selectionEnd}:null;
  e.controlsSegment=s.id;
  let chainStart=0;for(const previous of e.plan.segments.slice(0,i))if(previous.connection==='start')chainStart=previous.start;
  const choices=e.plan.history_choices.filter(n=>n<=s.start-chainStart);
  const anchorChoice=s.anchor_mode==='custom'?'asset:'+s.anchor:s.anchor_mode;
  $('#segment-controls').innerHTML=`<div class="row spread"><strong>Segment ${i+1}</strong><span class="note">${segmentTime(s.start)}–${segmentTime(s.end)}</span></div>
    <div class="segment-continuity-row"><div class="field"><label for="segment-connection">Connection</label><select id="segment-connection" ${i===0?'disabled':''}><option value="start" ${!continuing?'selected':''}>Start fresh</option><option value="continue" ${continuing?'selected':''}>Continue previous</option></select></div>
    <div class="field" ${continuing?'':'hidden'}><label for="segment-history">History from previous segment</label><select id="segment-history">${[...new Set([...choices,s.history_frames])].sort((a,b)=>a-b).map(n=>`<option value="${n}" ${n===s.history_frames?'selected':''} ${n>s.start-chainStart?'disabled':''}>${n===0?'None':n===1?'Boundary frame only':`${n} frames · ${(n/24).toFixed(2)}s`}</option>`).join('')}</select></div>
    </div><section class="panel segment-dialogue-panel"><div class="panel-title"><span>Segment dialogue</span><button id="transcribe" class="quiet">${uiIcon('audio')}Transcribe clip</button></div>
    <div class="panel-body"><p id="transcribe-status" role="status" aria-live="polite" hidden></p><textarea id="segment-dialogue" rows="4" aria-label="Segment dialogue" placeholder="Words spoken during this segment">${esc(state.dialogue)}</textarea>
    <div class="segment-dialogue-actions"><label class="label" for="segment-speaker">Speaker</label><select id="segment-speaker">${[['target','Selected character'],['other','Another person'],['offscreen','Offscreen voice'],['none','No dialogue']].map(([value,label])=>`<option value="${value}" ${state.speaker===value?'selected':''}>${label}</option>`).join('')}</select><button id="segment-prompt-refresh" class="quiet" title="Rebuild the prompt from this segment's dialogue, speaker and references">${uiIcon('refresh')}Update prompt</button></div></div></section>
    <section class="panel segment-prompt-panel"><div class="panel-title"><label for="segment-prompt">Performance prompt</label></div><div class="panel-body"><textarea id="segment-prompt" rows="9" placeholder="Exact prompt for this segment">${esc(state.prompt)}</textarea><p class="note">This is the exact text sent to H3. Update prompt rebuilds it from this segment’s dialogue, speaker and references.</p></div></section>
    ${s.needs_review?'<p class="note">Text was carried over from the previous layout. Review it for this range.</p>':''}
    <div class="field"><label for="segment-anchor">${continuing?'Additional reference':'Scene anchor'}</label><div class="segment-anchor-row"><select id="segment-anchor"><option value="none" ${anchorChoice==='none'?'selected':''}>${continuing?'Use generated continuity':'No anchor'}</option><option value="shot" ${anchorChoice==='shot'?'selected':''} ${shot().anchor?'':'disabled'}>Use shot anchor</option>${s.anchors.map((a,j)=>`<option value="asset:${esc(a.asset)}" ${anchorChoice==='asset:'+a.asset?'selected':''}>Segment reference ${j+1}</option>`).join('')}</select><button id="segment-reference-edit" class="h3-icon-button" title="Manage segment reference" aria-label="Manage segment reference">${uiIcon('image')}</button></div></div>

    <p class="note segment-pass-summary">${s.end-s.start} output frames · one ${state.window_frames}-frame diffusion pass${state.window_frames>s.end-s.start?'; extra boundary/padding frames are removed':''}.</p>`;
  $('#segment-editor-heading').textContent=`Segment ${i+1} · ${segmentTime(s.start)}–${segmentTime(s.end)}`;
  $('#segment-editor-panels').replaceChildren($('#segment-controls .segment-dialogue-panel'),$('#segment-controls .segment-prompt-panel'));
  const oldSeed=$('#render-seed'),seed=oldSeed.cloneNode(true);oldSeed.replaceWith(seed);seed.value=String(s.seed);seed.dataset.remoteValue=String(s.seed);e.editBase=structuredClone(s);
  e.fields=[['#segment-dialogue','dialogue'],['#segment-speaker','speaker'],['#segment-prompt','performance_prompt'],['#segment-connection','connection'],['#segment-history','history_frames'],['#segment-anchor','anchor_choice'],['#render-seed','seed']].map(([selector,key])=>{const field=$(selector);field.dataset.remoteValue=field.value;return {field,key};});
  for(const {field} of e.fields)field.addEventListener('input',()=>{
    S.dirty=true;S.renderDraftDirty=true;autosaveStatus('Saving…');clearTimeout(e.timer);
    e.timer=setTimeout(()=>{void flushSegmentEdits().catch(()=>{});},400);
  });
  $('#segment-reference-edit').onclick=guarded(openSegmentReference);
  $('#transcribe').onclick=guarded(transcribeClip);
  updateTranscriptionStatus(clip());
  $('#segment-prompt-refresh').onclick=guarded(async()=>{
    const key=e.selected;await flushShotAutosave();
    if(e!==segmentEditor||key!==e.selected)return;
    const result=await api(segmentPath()+'/'+key+'/prompt','POST',{revision:clip().revision});
    mergeSegmentClip(result);await loadSegmentPlan(true);toast('Segment prompt updated');
  });
  if(focus){const field=$('#'+focus.id);field?.focus({preventScroll:true});if(field&&focus.start!==null)field.setSelectionRange(focus.start,focus.end);}

}
async function openSegmentReference(){
  await flushShotAutosave();const e=segmentEditor,seg=segmentSelected(),i=e.plan.segments.indexOf(seg),base=segmentPath()+'/'+seg.id;
  dialog(`Segment ${i+1} reference`,`<div class="segment-reference-preview"><figure><img src="api/${base}/source-frame" alt="Segment opening source frame"><figcaption>Source at ${segmentTime(seg.start)}</figcaption></figure>${(seg.anchor_mode==='shot'?shot().anchor:seg.anchor)?`<figure><img src="${asset(seg.anchor_mode==='shot'?shot().anchor:seg.anchor)}" alt="Selected reference"><figcaption>Selected reference</figcaption></figure>`:''}</div><p class="note">${seg.connection==='continue'?'An additional reference guides this segment without replacing generated history.':'Match the source pose and scene at the start of this segment.'}</p><div class="row"><label class="button" for="segment-image-upload">Import image</label><input id="segment-image-upload" type="file" accept="image/*" hidden><button id="segment-generate-anchor">Generate reference</button><a href="api/${base}/source-frame" download="segment-source.png">Download source frame</a></div><p id="segment-image-status" class="note" role="status"></p>`);
  $('#segment-image-upload').onchange=guarded(async event=>{
    const file=event.target.files[0];if(!file)return;$('#segment-image-status').textContent='Importing…';
    const data=new FormData();data.append('revision',clip().revision);data.append('file',file);
    const response=await fetch('api/'+base+'/image',{method:'POST',body:data});const result=await response.json();if(!response.ok)throw Error(result.detail||'Image import failed');
    mergeSegmentClip(result);$('#dialog').close();await loadSegmentPlan(true);
  });
  $('#segment-generate-anchor').onclick=guarded(async()=>{await queue('anchor',e.shotId,e.clipId,undefined,'cpu',{segment_id:seg.id});$('#dialog').close();});
}
async function segmentLayoutPreview(operation){
  await flushShotAutosave();const e=segmentEditor;$('.segment-arrange').open=false;
  const preview=await api(segmentPath()+'/preview','POST',{revision:clip().revision,operation});
  dialog('Preview segment layout',`<p>${preview.segments.length} segment${preview.segments.length===1?'':'s'} · ${(shot().end-shot().start)/24}s total</p><div class="segment-layout-preview">${preview.segments.map((s,i)=>`<div><strong>Segment ${i+1}</strong><span>${segmentTime(s.start)}–${segmentTime(s.end)}</span><p class="note">${esc(preview.dialogue[i]||'No transcribed dialogue')}${s.needs_review?' · Review carried-over directions':''}</p></div>`).join('')}</div><p class="note">The current layout, directions, and ${preview.preserved_takes} take${preview.preserved_takes===1?'':'s'} will stay in Previous layouts.</p><div class="row"><button id="segment-layout-apply" class="primary">Apply layout</button><button id="segment-layout-cancel">Keep current layout</button></div>`);
  $('#segment-layout-cancel').onclick=()=>$('#dialog').close();
  $('#segment-layout-apply').onclick=guarded(async()=>{
    const result=await api(segmentPath()+'/layout','POST',{revision:preview.revision,operation});mergeSegmentClip(result);$('#dialog').close();e.fields=[];await loadSegmentPlan(true);maskSeek(segmentSelected().start);
  });
}
async function segmentBoundary(){
  await flushShotAutosave();const e=segmentEditor,seg=segmentSelected(),i=e.plan.segments.indexOf(seg);
  if(i===e.plan.segments.length-1)throw Error('Select a segment that has a following segment.');
  const next=e.plan.segments[i+1];
  dialog(`Move boundary after segment ${i+1}`,`<label class="label" for="segment-boundary-frame">Source frame within this shot</label><input id="segment-boundary-frame" type="number" min="${seg.start+1}" max="${next.end-1}" value="${seg.end}"><p class="note">Both segments keep the same combined duration.</p><button id="segment-boundary-preview" class="primary">Preview change</button>`);
  $('#segment-boundary-preview').onclick=guarded(()=>segmentLayoutPreview({operation:'boundary',index:i+1,frame:Number($('#segment-boundary-frame').value)}));
}
function segmentPreviousLayouts(){
  const history=shot().segment_layout_history||[];
  dialog('Previous segment layouts',history.length?history.slice().reverse().map(v=>`<details><summary>${v.segments.length} segments · ${new Date(v.created*1000).toLocaleString()}</summary>${v.segments.map((s,i)=>`<div class="segment-previous"><strong>Segment ${i+1} · ${segmentTime(s.start)}–${segmentTime(s.end)}</strong><p>${esc(s.prompt)}</p>${s.takes.map((t,j)=>`<a href="${asset(t.asset,true)}">Download take ${j+1}</a>`).join(' · ')}</div>`).join('')}</details>`).join(''):'<p>No previous layouts yet.</p>');
}
async function generateSegments(mode){
  await flushShotAutosave();if(shot().use_mask!==false)await saveMaskEdits();await loadSegmentPlan(true);
  const e=segmentEditor,seg=segmentSelected(),s=shot();
  const expected={seed:s.seed,prompt:s.prompt,render_mp:s.render_mp??1,...generationSettings(s)};
  await queue('render',e.shotId,e.clipId,expected,$('#worker').value,{model_offloading:$('#render-model-offloading').checked,
    segments:{mode,segment_id:seg.id,plan_revision:e.plan.plan_revision}});
  updateSegmentHistory();
}
function segmentTakeButton(seg,t,state){
  const selected=t.id===seg.selected_take;
  const changed=Object.entries(t.settings||{}).some(([k,v])=>['prompt','performance_prompt','dialogue','speaker','seed','connection','history_frames','anchor_mode','anchor'].includes(k)&&JSON.stringify(seg[k])!==JSON.stringify(v));
  const label=!selected?'Use this take':state.current?'Selected':changed?'Restore saved settings':'Selected · needs refresh';
  return `<button data-select-segment-take="${esc(t.id)}" ${selected&&(state.current||!changed)?'disabled':''}>${label}</button>`;
}
function segmentJobScope(job){
  if(job.data.segment_scope)return job.data.segment_scope;
  const request=job.data.options?.segments,items=segmentEditor.plan.segments;
  if(!request)return [];
  const start=items.findIndex(s=>s.id===request.segment_id);
  return items.map((s,index)=>({...s,index})).filter(s=>request.mode==='all'||(request.mode==='segment'?s.index===start:s.index>=start));
}
function segmentJobPending(job,id){
  return segmentJobScope(job).some(s=>s.id===id)&&!(job.data.segment_results||[]).includes(job.id+'-'+id);
}
function segmentJobState(job,id){
  if(job.state==='running'&&job.data.segment_progress?.segment_id!==id)return 'queued';
  return job.state;
}
function closeSegmentGenerateMenu(focus=false){
  const menu=$('#segment-generate-menu'),button=$('#segment-generate-more');
  if(menu)menu.hidden=true;if(button){button.setAttribute('aria-expanded','false');if(focus)button.focus();}
}
document.addEventListener('pointerdown',event=>{
  if(!event.target.closest('.render-actions'))closeSegmentGenerateMenu();
});
document.addEventListener('keydown',event=>{
  if(event.key==='Escape'&&$('#segment-generate-menu')&&!$('#segment-generate-menu').hidden){event.preventDefault();closeSegmentGenerateMenu(true);}
});
function updateSegmentHistory(){
  if(!segmentActive()||!segmentEditor.plan)return;
  const e=segmentEditor,seg=segmentSelected(),state=segmentStatus(),s=shot(),c=clip();
  const jobs=S.data.jobs.filter(j=>j.clip_id===c.id&&j.shot_id===s.id&&j.kind==='render');
  const activeJobs=jobs.filter(j=>['submitting','queued','waiting','running','cancelling'].includes(j.state));
  const active=activeJobs.find(j=>segmentJobPending(j,seg.id));
  const legacyActive=activeJobs.find(j=>!j.data.options?.segments);
  const awaiting=state.dependencies.filter(d=>!e.plan.states.find(v=>v.id===d.segment_id)?.current);
  const canWait=awaiting.every(d=>activeJobs.some(j=>j.state!=='cancelling'&&segmentJobPending(j,d.segment_id)));
  const blocked=state.blocked&&!canWait;
  const selectedIndex=e.plan.segments.indexOf(seg);
  const entries=seg.takes.map((t,i)=>({key:t.id,html:`<article class="render-card ${t.id===seg.selected_take?'segment-selected':''}"><button class="render-thumb" data-open-segment-take="${esc(t.id)}"><img src="${asset(t.asset)}/thumbnail" alt="Segment ${selectedIndex+1}, take ${i+1}" loading="lazy"><span class="play-mark" aria-hidden="true">▶</span><span class="take-duration">${((seg.end-seg.start)/24).toFixed(1)}s</span></button><div class="render-card-body"><div class="row spread"><strong>Take ${i+1}</strong><a href="${asset(t.asset,true)}" aria-label="Download take ${i+1}">${uiIcon('download')}</a></div><p class="note">${esc(renderLoRALabel(t))} · Seed ${t.seed}</p>${segmentTakeButton(seg,t,state)}</div></article>`}));
  for(const j of jobs.filter(j=>['submitting','queued','waiting','running','cancelling','failed','cancelled','interrupted','unconfirmed'].includes(j.state))){
    if(!segmentJobPending(j,seg.id))continue;
    const displayState=segmentJobState(j,seg.id),isFuture=displayState==='queued'&&j.state==='running';
    const phase=isFuture?`Waiting for segment ${j.data.segment_progress?.number||selectedIndex+1}`:j.data.phase||'';
    entries.push({key:j.id,html:`<article class="render-card"><div class="render-placeholder"><strong>${esc(renderStatus({...j,state:displayState}))} · Segment ${selectedIndex+1}</strong><span>${esc(phase)}</span></div><div class="render-card-body">${j.data.error?`<details><summary>Details</summary><p>${esc(j.data.error)}</p></details>`:''}${j.state==='failed'&&j.data.capacity_failure?.offloading===false?`<button data-segment-offload="${esc(j.id)}" ${active?'disabled':''}>Retry with model offloading</button>`:''}${['queued','waiting','running'].includes(j.state)?`<button data-cancel-segment-job="${esc(j.id)}">${j.state==='running'?(segmentJobScope(j).length>1?'Stop remaining segments':'Stop generation'):'Cancel queued generation'}</button>`:''}</div></article>`});
  }
  $('#render-count').textContent=`Segment ${selectedIndex+1} · ${seg.takes.length}`;
  reconcileItems($('#render-history-cards'),entries.length?entries:[{key:'empty',html:'<div class="render-empty"><strong>No takes for this segment yet</strong><p>Review its directions and generate a take.</p></div>'}]);
  const reason=s.mode==='preserve'?'Set this shot to Generate to render segments.':!c.reference?'Add a character reference.':legacyActive?'Wait for the whole-shot render to finish.':active?'This segment is already queued or generating. Edits apply to your next take.':blocked?'Generate or queue the preceding segment first.':awaiting.length?'Queue this segment now; it will start when its preceding take is ready.':state.status==='stale'?'Settings or the preceding take changed. This segment needs a new take.':'';
  $('#render-queue-reason').textContent=reason;
  $('#queue-shot').disabled=!!active||!!legacyActive||s.mode==='preserve'||!c.reference||blocked;
  uiButton($('#queue-shot'),active?(segmentJobState(active,seg.id)==='running'?'Generating…':'Queued'):activeJobs.length?'Queue segment':'Generate segment','play');
  $('#segment-generate-more').disabled=!!legacyActive||s.mode==='preserve'||!c.reference;
  for(const [id,from] of [['segment-all',0],['segment-from',selectedIndex]]){
    const eligible=e.plan.segments.some((v,i)=>i>=from&&(!activeJobs.length||(!e.plan.states[i].current&&!activeJobs.some(j=>segmentJobScope(j).some(entry=>entry.id===v.id)))));
    $('#'+id).disabled=!eligible;
  }
  $('#segment-whole-takes').textContent=`Whole-shot takes${s.renders.length?' · '+s.renders.length:''}`;
  $$('[data-open-segment-take]').forEach(b=>b.onclick=()=>{
    const t=seg.takes.find(t=>t.id===b.dataset.openSegmentTake);
    dialog(`Segment ${selectedIndex+1} · Take ${seg.takes.indexOf(t)+1}`,`<video class="segment-take-player" src="${asset(t.asset)}" controls playsinline></video><p class="note">${segmentTime(t.start)}–${segmentTime(t.end)} · ${t.history_frames} history frames · ${esc(renderLoRALabel(t))}</p><details><summary>Saved generation prompt</summary><pre class="saved-prompt">${esc(t.prompt)}</pre></details><div class="row"><a href="${asset(t.asset,true)}">Download video</a><a href="${asset(t.api,true)}">Workflow JSON</a><a href="${asset(t.master,true)}">Continuation master</a></div>`);
  });
  $$('[data-select-segment-take]').forEach(b=>b.onclick=guarded(async()=>{
    await flushShotAutosave();const expected=segmentEditor.plan.plan_revision;
    // Assembly can update the clip revision between polls without changing its plan.
    const latest=await api(segmentPath());
    if(latest.plan_revision!==expected)throw Error('The segment plan changed. Refresh it before choosing a take.');
    const result=await api(segmentPath()+'/'+seg.id+'/take','POST',{revision:latest.revision,take_id:b.dataset.selectSegmentTake});
    mergeSegmentClip(result.clip);if(result.assembly_job)mergeJob(result.assembly_job);await loadSegmentPlan(true);if(result.assembly_job)toast('Assembling the selected segments');
  }));
  $$('[data-segment-offload]').forEach(b=>b.onclick=guarded(async()=>{
    const result=await api('jobs/'+b.dataset.segmentOffload+'/retry-offload','POST');mergeJob(result.job);updateSegmentHistory();
  }));
  $$('[data-cancel-segment-job]').forEach(b=>b.onclick=guarded(async()=>{
    const job=jobs.find(j=>j.id===b.dataset.cancelSegmentJob);
    if(job?.state==='running'&&!confirm('Stop this generation? Completed segment takes are retained. Other worker jobs continue.'))return;
    await api('jobs/'+b.dataset.cancelSegmentJob+'/cancel','POST',{confirm_running:job?.state==='running'});await refresh(false);updateSegmentHistory();
  }));
}
function syncSegmentWorkspace(s){
  if(!segmentActive())return;
  for(const [selector,value] of [['#render-mp',s.render_mp??1],['#render-lora',s.lora_id||'v1-1000'],['#render-use-mask',s.use_mask??true]])syncEditableField(selector,value);
  updateTranscriptionStatus(clip());
  $('.generation-model').textContent='MiniMax H3 · '+loRALabel(s.lora_id)+' · Turbo 8';syncRenderSize();
  if(!segmentDirty()&&!segmentEditor.pending)void loadSegmentPlan();updateSegmentHistory();
}
function drawRender(c,s){
  clearTimeout(segmentEditor?.timer);segmentEditor=null;
  drawLegacyRender(c,s);
  const player=$('.render-controls'),main=document.createElement('div');main.className='render-main';player.before(main);main.append(player);
  main.insertAdjacentHTML('beforeend','<section id="segment-text-editors" class="segment-text-editors" aria-labelledby="segment-editor-heading"><h2 id="segment-editor-heading">Segment text</h2><div id="segment-editor-panels" class="segment-editor-panels"></div></section>');
  segmentEditor={clipId:c.id,shotId:s.id,selected:localStorage.getItem(`h3segment:${c.id}:${s.id}`),plan:null,serial:0,fields:[],pending:null,timer:null};
  $('.render-window-controls')?.remove();$('#render-window-summary')?.remove();$('#render-use-anchor').closest('label').remove();
  const advanced=$('.render-generation details');advanced.classList.add('segment-shared-settings');
  advanced.querySelector('label[for="render-prompt"]').remove();$('#render-prompt').remove();
  const offloading=$('#render-model-offloading').closest('label'),offloadingNote=offloading.nextElementSibling;advanced.append(offloading);
  if(offloadingNote?.classList.contains('note'))advanced.append(offloadingNote);
  $('label[for="render-seed"]').textContent='Segment seed';
  const oldLora=$('#render-lora');oldLora.outerHTML=`<select id="render-lora" aria-label="Character-swap LoRA">${[['v2-1000','Character Swap LoRA V1.5'],['v1-1000','Character Swap LoRA V1'],['none','No character-swap LoRA']].map(([id,label])=>`<option value="${id}" ${(s.lora_id||'v1-1000')===id?'selected':''}>${label}</option>`).join('')}</select>`;
  const lora=$('#render-lora');advanced.prepend(lora);lora.insertAdjacentHTML('beforebegin','<label class="label" for="render-lora">Character-swap LoRA</label>');
  advanced.insertAdjacentHTML('beforebegin','<div id="segment-controls" class="segment-controls"><p class="note">Loading segments…</p></div>');
  $('#mask-resize').insertAdjacentHTML('afterend',`<div class="segment-timeline"><div class="row spread"><strong id="segment-count">Segments</strong><details class="segment-arrange"><summary>Arrange segments</summary><div class="segment-arrange-menu"><span class="note">Split into</span>${[5,10,15].map(n=>`<button data-segment-partition="${n*24}">${n}-second segments</button>`).join('')}<button id="segment-split">Split at playhead</button><button id="segment-boundary">Move selected boundary…</button><button id="segment-merge">Merge with next</button><button id="segment-previous">Previous layouts</button></div></details></div><div id="segment-strip" class="segment-strip" role="group" aria-label="Generation segments"></div></div>`);
  $('#render-filter').hidden=true;
  $('#queue-shot').insertAdjacentHTML('afterend',`<button id="segment-generate-more" class="h3-icon-button" title="More generation options" aria-label="More generation options" aria-expanded="false" aria-controls="segment-generate-menu">⌄</button><div id="segment-generate-menu" class="segment-generate-menu" hidden><button id="segment-all">Queue all segments</button><button id="segment-from">Queue from here</button><p class="note">Runs in order. While rendering, skips current and already queued segments.</p></div>`);
  $('#render-history-cards').insertAdjacentHTML('afterend','<button id="segment-whole-takes" class="quiet">Whole-shot takes</button>');
  bindShotAutosave([['#render-mp','render_mp',s.render_mp??1],['#render-lora','lora_id',s.lora_id||'v1-1000'],['#render-use-mask','use_mask',s.use_mask??true]]);
  $('#render-seed').oninput=null;
  $('#queue-shot').onclick=guarded(()=>generateSegments('segment'));
  $('#segment-generate-more').onclick=()=>{
    const menu=$('#segment-generate-menu');menu.hidden=!menu.hidden;
    $('#segment-generate-more').setAttribute('aria-expanded',String(!menu.hidden));
    if(!menu.hidden)menu.querySelector('button:not(:disabled)')?.focus();
  };
  $('#segment-from').onclick=guarded(async()=>{closeSegmentGenerateMenu();await generateSegments('from');});
  $('#segment-all').onclick=guarded(async()=>{closeSegmentGenerateMenu();await generateSegments('all');});
  $$('[data-segment-partition]').forEach(b=>b.onclick=guarded(()=>segmentLayoutPreview({operation:'partition',frames:Number(b.dataset.segmentPartition)})));
  $('#segment-split').onclick=guarded(()=>segmentLayoutPreview({operation:'split',frame:maskEditState?.frame??0}));
  $('#segment-boundary').onclick=guarded(segmentBoundary);
  $('#segment-merge').onclick=guarded(()=>segmentLayoutPreview({operation:'merge',index:segmentEditor.plan.segments.indexOf(segmentSelected())+1}));
  $('#segment-previous').onclick=segmentPreviousLayouts;
  $('#segment-whole-takes').onclick=()=>{
    const s=shot();
    dialog('Whole-shot takes',s.renders.length?`<div class="render-grid">${s.renders.map((r,i)=>`<button data-whole-take="${esc(r.id)}"><img src="${asset(r.asset)}/thumbnail" alt="Whole-shot take ${i+1}"><span>Take ${i+1}${r.segment_chain?' · assembled segments':''}</span></button>`).join('')}</div>`:'<p>Generate and select current takes for every segment to assemble the shot.</p>');
    $$('[data-whole-take]').forEach(b=>b.onclick=()=>openRender(b.dataset.wholeTake));
  };
  $('#mask-play').onclick=guarded(async()=>{
    const e=maskEditState,seg=segmentSelected();if(!e?.ready||!seg||e.viewing==='anchor')return;if(e.wanted){maskPause();return;}
    e.wanted=true;await e.player.start(e.frame<seg.start||e.frame>=seg.end-1?seg.start:null);maskFrameUI(e);
  });
  $('#mask-video').addEventListener('timeupdate',()=>{
    const e=maskEditState,seg=segmentSelected();if(!segmentActive()||!e?.wanted||!seg||e.player.video.currentTime*24<seg.end)return;
    if($('#mask-loop').checked){e.player.video.currentTime=seg.start/24;}else maskPause();
  });
  bindShotViewer(c,s);
  void loadSegmentPlan(true);
}
