// One visible segment per diffusion pass. Shot masks and shared settings stay
// in their existing panels; only the selected segment's controls are expanded.
let segmentEditor = null;
function disposeSegmentEditor(){
  clearTimeout(segmentEditor?.timer);segmentEditor?.guideResize?.disconnect();
  segmentEditor?.planAbort?.abort();segmentEditor=null;
}
function segmentActive(){return !!segmentEditor && S.tab==='render' && segmentEditor.clipId===clip()?.id && segmentEditor.shotId===shot()?.id && !!$('#segment-controls');}
function segmentSelected(){return segmentEditor?.plan?.segments.find(s=>s.id===segmentEditor.selected);}
function segmentStatus(){return segmentEditor?.plan?.states.find(s=>s.id===segmentEditor.selected);}
function segmentTime(frame){const seconds=frame/24;return `${Math.floor(seconds/60)}:${(seconds%60).toFixed(1).padStart(4,'0')}`;}
function segmentPath(){return `clips/${segmentEditor.clipId}/shots/${segmentEditor.shotId}/segments`;}
function segmentDirty(){return !!segmentEditor?.fields?.some(({field})=>fieldValue(field)!==field.dataset.remoteValue||field.dataset.promptEdited==='true');}
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
        if(fieldValue(field)===field.dataset.remoteValue&&field.dataset.promptEdited!=='true')continue;
        if(field.type==='number'&&(!field.value||!field.checkValidity()))throw Error('Enter a valid whole-number seed.');
        if(field.dataset.conflict==='true')throw Error('This segment changed elsewhere. Your edits are preserved; reload saved values before continuing.');
        if(key==='anchor_choice'){
          changes.anchor_mode=field.value.startsWith('asset:')?'custom':field.value;
          changes.anchor=field.value.startsWith('asset:')?field.value.slice(6):null;
        }else changes[key]=field.type==='checkbox'?field.checked:['seed','history_frames'].includes(key)?Number(field.value):field.value;
        saved.push({field,value:fieldValue(field)});
      }
      const key=e.selected,base=structuredClone(e.editBase);
      const task=writeQueue.then(async()=>{
        const c=await api(`clips/${e.clipId}`);mergeSegmentClip(c);const s=c.shots.find(s=>s.id===e.shotId);
        const remote=s.segments?.find(v=>v.id===key);
        if(remote)for(const k of Object.keys(changes))if(!sameSettings(remote[k]??null,base[k]??null))throw Error('This segment changed elsewhere. Your edits are kept here.');
        const result=await api(`clips/${e.clipId}/shots/${e.shotId}/segments/${key}`,'PATCH',{revision:c.revision,changes});
        mergeSegmentClip(result);return result;
      });writeQueue=task.catch(()=>{});
      autosaveStatus('Saving…');await task;
      for(const {field,value} of saved){field.dataset.remoteValue=value;delete field.dataset.conflict;delete field.dataset.promptEdited;}
      retainFieldDraft(e,true);
      Object.assign(e.plan.segments.find(s=>s.id===key),changes);Object.assign(e.editBase,changes);
    }
    if(e===segmentEditor){S.dirty=autosaveDirty()||!!maskHasDraft();S.renderDraftDirty=autosaveDirty();autosaveStatus('All changes saved');await loadSegmentPlan(true);}
  };
  e.pending=run().catch(error=>{S.dirty=true;autosaveStatus('Not saved: '+error.message,true);if(error.status===409)e.fields.forEach(({field})=>field.dataset.conflict='true');throw error;});
  try{await e.pending;}finally{e.pending=null;}
}
// First-load status belongs in the timeline as well as the inspector. Never
// replace an already loaded timeline during background refreshes.
function segmentTimelineStatus(state){
  const strip=$('#segment-strip');if(!strip)return;
  strip.dataset.loadState=state;strip.setAttribute('aria-busy',String(state==='loading'));
  $('.segment-arrange').querySelectorAll('button').forEach(button=>button.disabled=state!=='ready');
  if(state==='ready'){strip.innerHTML='';delete strip.dataset.layoutKey;return;}
  strip.innerHTML=state==='loading'
    ? '<div class="segment-timeline-placeholder" role="status"><span class="segment-timeline-skeleton" aria-hidden="true"></span><span>Loading segments…</span></div>'
    : '<div class="segment-timeline-placeholder" role="status"><span>Could not load segments.</span><button id="segment-timeline-retry" class="quiet">Retry</button></div>';
  if(state==='error')$('#segment-timeline-retry').onclick=()=>loadSegmentPlan(true);
}
async function loadSegmentPlan(force=false){
  if(!segmentActive())return;const e=segmentEditor,c=clip();
  if(!force&&e.loadedRevision===c.revision)return;
  // Polling must not supersede a response merely because it takes longer than
  // the poll interval. Explicit edits can invalidate an older pending read.
  if(e.planAbort&&!force)return;
  e.planAbort?.abort();
  const controller=e.planAbort=new AbortController();
  const serial=++e.serial;
  if(!e.plan)segmentTimelineStatus('loading');
  try{
    const plan=await api(segmentPath(),'GET',undefined,AbortSignal.any([controller.signal,AbortSignal.timeout(30000)]));if(e!==segmentEditor||serial!==e.serial||!segmentActive())return;
    if(plan.segments.some((seg,i)=>seg.render_mode!==WORKFLOW.render_mode||!MODEL_PRESETS.some(p=>p.lora_id===seg.lora_id)||seg.connection!==(i?'continue':'start')||seg.history_frames!==(i&&seg.start<WORKFLOW.history_frames?1:WORKFLOW.history_frames))){
      const result=await api(segmentPath()+'/workflow','POST',{revision:plan.revision});
      if(e!==segmentEditor)return;
      mergeSegmentClip(result);e.planAbort=null;await loadSegmentPlan(true);return;
    }
    const initial=!e.plan;e.plan=plan;e.loadedRevision=plan.revision;
    if(!plan.segments.some(s=>s.id===e.selected))e.selected=plan.segments[0].id;
    if(initial)segmentTimelineStatus('ready');
    renderSegmentStrip();
    if(!segmentDirty())renderSegmentControls();
    updateSegmentHistory();
    if(initial&&!autosaveDirty()&&!segmentDirty())autosaveStatus('All changes saved');
  }catch(error){
    if(controller.signal.aborted||e!==segmentEditor||!segmentActive())return;
    // Retain existing editable controls and drafts if a refresh fails.
    if(!e.plan){
      segmentTimelineStatus('error');
      $('#segment-controls').innerHTML='<p class="note" role="status">Could not load segments.</p><button id="segment-load-retry" class="quiet">Retry now</button>';
      $('#segment-controls p').textContent=error.message||'Could not load segments.';
      $('#segment-load-retry').onclick=()=>loadSegmentPlan(true);
    }
  }finally{if(e.planAbort===controller)e.planAbort=null;}
}
function timelineFrame(clientX){
  const rect=$('#segment-strip').getBoundingClientRect(),frames=shot().end-shot().start;
  return Math.max(0,Math.min(frames-1,Math.round((clientX-rect.left)/rect.width*frames)));
}
function syncSegmentPlayhead(frame=maskEditState?.frame??0){
  syncAnchorGeneration();
  const surface=$('#segment-timeline-surface');if(!surface)return;
  const frames=shot().end-shot().start,seg=segmentSelected();
  surface.style.setProperty('--playhead',`${100*(frame+.5)/frames}%`);
  const containing=segmentEditor?.plan?.segments.find(s=>s.start<=frame&&frame<s.end);
  const canSplit=containing&&frame-containing.start>=MIN_SEGMENT_FRAMES&&containing.end-frame>=MIN_SEGMENT_FRAMES;
  const split=$('#segment-split');
  if(split){
    split.disabled=!canSplit;
    split.title=canSplit?'Split at playhead':'Leave at least 1 second on each side of the split.';
  }
  $('#mask-frame').setAttribute('aria-valuetext',`Frame ${frame}, ${segmentTime(frame)}`);
  }
function renderSegmentStrip(){
  const e=segmentEditor;if(!e.plan||e.boundaryDrag)return;
  const frames=shot().end-shot().start,strip=$('#segment-strip');
  // Keep button identity across selection, autosave and status polling so the
  // browser can deliver a double-click on the same segment.
  const layoutKey=JSON.stringify(e.plan.segments.map(s=>[s.id,s.start,s.end]));
  if(strip.dataset.layoutKey!==layoutKey){
    strip.dataset.layoutKey=layoutKey;
    strip.innerHTML=e.plan.segments.map((s,i)=>{
      const state=e.plan.states[i];return `<button class="segment-chip ${s.id===e.selected?'active':''}" style="left:${100*s.start/frames}%;width:${100*(s.end-s.start)/frames}%" data-segment="${esc(s.id)}" aria-pressed="${s.id===e.selected}" title="Segment ${i+1} · ${segmentTime(s.start)}–${segmentTime(s.end)} · ${s.connection==='continue'?'Continues previous segment':'Starts fresh'}"><span><i class="segment-dot state-${state.status}"></i>Segment ${i+1}</span><small>${segmentTime(s.start)}–${segmentTime(s.end)}</small></button>`;
  }).join('')+e.plan.segments.slice(1).map((s,i)=>`<button class="segment-boundary-handle" data-boundary="${i+1}" style="left:${100*s.start/frames}%" aria-label="Adjust boundary after segment ${i+1}" title="Drag to adjust boundary after segment ${i+1}; Enter for exact frame"><span></span></button>`).join('');
  }
  $('#segment-boundary').disabled=e.plan.segments.length<2;
  $('#segment-count').textContent=`${e.plan.segments.length} segment${e.plan.segments.length===1?'':'s'} · ${e.plan.states.filter(s=>s.current).length} generated`;
  strip.querySelectorAll('[data-segment]').forEach((b,i)=>{
    const s=e.plan.segments[i],selected=s.id===e.selected;
    b.classList.toggle('active',selected);b.setAttribute('aria-pressed',String(selected));
    const state=e.plan.states[i], job=S.data.jobs.find(j=>j.clip_id===e.clipId&&j.shot_id===e.shotId&&['render','guide_render'].includes(j.kind)&&['submitting','queued','waiting','running','cancelling'].includes(j.state)&&segmentJobPending(j,s.id));
    const pending=job&&['submitting','queued','waiting','running','cancelling'].includes(job.state);
    const status=pending?'active':state.current?'ready':state.blocked?'blocked':state.status;
    const label=pending?(job.state==='running'?'Generating':'Queued'):state.current?'Generated':state.blocked?'Waiting for previous segment':state.status==='stale'?'Needs regeneration':'Not generated';
    b.querySelector('.segment-dot').className=`segment-dot state-${status}`;
    b.setAttribute('aria-label',`Segment ${i+1} · ${label}`);
    b.dataset.status=label;
    b.title=`Segment ${i+1} · ${segmentTime(s.start)}–${segmentTime(s.end)} · ${s.connection==='continue'?'Continues previous segment':'Starts fresh'} · ${label} · Click to inspect; double-click to jump to first frame`;
  });
  // Delegate to the strip: guarded button actions disable their target while
  // saving, which would otherwise swallow the second click during autosave.
  strip.onclick=strip.ondblclick=guarded(async event=>{
    const button=event.target.closest('[data-segment]');if(!button)return;
    const id=button.dataset.segment,jump=event.type==='dblclick',request=e.selectionRequest=(e.selectionRequest||0)+1;
    await flushShotAutosave();await saveMaskEdits();if(e!==segmentEditor||!segmentActive()||request!==e.selectionRequest)return;
    const selected=e.plan.segments.find(s=>s.id===id);if(!selected)return;
    if(e.selected!==id){
      e.selected=id;localStorage.setItem(`h3segment:${e.clipId}:${e.shotId}`,id);
      renderSegmentStrip();renderSegmentControls();updateSegmentHistory();
    }
    if(jump)maskSeek(selected.start);
    syncSegmentPlayhead();
  });
  $$('[data-boundary]').forEach(handle=>{
    let drag=null;
    const boundary=Number(handle.dataset.boundary);
    const limits=boundaryLimits(e.plan.segments,boundary);
    handle.disabled=limits.min>limits.max||(limits.min===limits.max&&e.plan.segments[boundary].start===limits.min);
    handle.style.left=`${100*e.plan.segments[boundary].start/frames}%`;
    handle.title=`Drag to adjust boundary after segment ${boundary}; Enter for exact frame`;
    handle.onclick=guarded(async event=>{if(event.detail===0)await segmentBoundary(Number(handle.dataset.boundary));});
    handle.onpointerdown=event=>{
      if(event.button!==0||!e.plan)return;
      event.preventDefault();event.stopPropagation();maskPause();
      const index=Number(handle.dataset.boundary),items=e.plan.segments;
      drag={id:event.pointerId,index,frame:items[index].start,original:items[index].start,...limits,plan:e.plan.plan_revision};
      e.boundaryDrag=drag;handle.setPointerCapture(event.pointerId);handle.focus({preventScroll:true});
    };
    handle.onpointermove=event=>{
      if(!drag||event.pointerId!==drag.id)return;
      drag.frame=Math.max(drag.min,Math.min(drag.max,timelineFrame(event.clientX)));
      handle.style.left=`${100*drag.frame/frames}%`;handle.title=`Boundary · frame ${drag.frame} · ${segmentTime(drag.frame)}`;
    };
    const cancel=()=>{drag=null;e.boundaryDrag=null;renderSegmentStrip();};
    handle.onpointercancel=cancel;
    handle.onlostpointercapture=()=>{if(drag)cancel();};
    handle.onpointerup=guarded(async event=>{
      if(!drag||event.pointerId!==drag.id)return;
      const value=drag;drag=null;handle.releasePointerCapture(event.pointerId);
      try{
      if(value.frame===value.original)return;
      if(e.plan.plan_revision!==value.plan)throw Error('Segment layout changed while dragging. Review the current boundaries.');
      await changeSegmentLayout({operation:'boundary',index:value.index,frame:value.frame});
      }finally{e.boundaryDrag=null;if(e===segmentEditor)renderSegmentStrip();}
    });
  });
  syncSegmentPlayhead();
}
async function updateSegmentPrompt(){
  const e=segmentEditor,key=e.selected;await flushShotAutosave();
  if(e!==segmentEditor||key!==e.selected)return;
  const path=segmentPath();
  const task=writeQueue.then(async()=>{
    const result=await api(path+'/'+key+'/prompt','POST',{revision:S.data.clips.find(c=>c.id===e.clipId).revision});
    mergeSegmentClip(result);
  });
  writeQueue=task.catch(()=>{});await task;
  if(e===segmentEditor&&key===e.selected){await loadSegmentPlan(true);toast('Prompt updated · automatic updates on');}
}
function renderSegmentControls(){
  const e=segmentEditor,s=segmentSelected(),state=segmentStatus();if(!s)return;
  const scrollPositions=e.controlsSegment===s.id?[...document.querySelectorAll('.segment-character-list,.anchor-candidates')].map(el=>({selector:'.'+el.classList[0],top:el.scrollTop})):[];
  const i=e.plan.segments.indexOf(s),continuing=s.connection==='continue';
  const active=document.activeElement,focus=e.controlsSegment===s.id&&active?.id&&active.closest('#segment-controls, #segment-performance')?{id:active.id,start:typeof active.selectionStart==='number'?active.selectionStart:null,end:typeof active.selectionEnd==='number'?active.selectionEnd:null}:null;
  e.controlsSegment=s.id;
  const anchorChoice=s.anchor_mode==='custom'?'asset:'+s.anchor:s.anchor_mode;
  $('#render-lora')?.remove();
  $('#segment-controls').innerHTML=`<div class="field segment-model-field"><label for="render-lora">Model</label><select id="render-lora" title="MiniMax H3 model and LoRA configuration">${MODEL_PRESETS.map(p=>`<option value="${p.lora_id}" ${s.lora_id===p.lora_id?'selected':''}>${p.label}</option>`).join('')}</select></div><section class="panel segment-dialogue-panel"><div class="panel-title"><span>Segment dialogue</span><button id="transcribe" class="primary">${uiIcon('audio')}Transcribe clip</button></div>
    <div class="panel-body"><p id="transcribe-status" role="status" aria-live="polite" hidden></p><textarea id="segment-dialogue" rows="4" aria-label="Segment dialogue" placeholder="Words spoken during this segment">${esc(state.dialogue)}</textarea>
    <div class="segment-dialogue-actions"><label class="label" for="segment-speaker">Speaker</label><select id="segment-speaker">${[['target','Selected character'],['other','Another person'],['offscreen','Offscreen voice'],['none','No dialogue']].map(([value,label])=>`<option value="${value}" ${state.speaker===value?'selected':''}>${label}</option>`).join('')}</select><button id="segment-prompt-refresh" class="quiet" title="Replace the prompt with the current references and dialogue, and resume automatic updates">${uiIcon('refresh')}Update prompt</button></div></div></section>
    <section class="panel segment-prompt-panel"><div class="panel-title"><label for="segment-prompt">Prompt</label><span class="note segment-prompt-mode" role="status" title="${state.prompt_mode==='manual'?'Automatic updates are paused because you edited the prompt. Update prompt replaces it and resumes automatic updates.':'Follows this segment’s references and dialogue until you edit the prompt.'}">${state.prompt_mode==='manual'?'Manual':'Automatic'}</span></div><div class="panel-body"><textarea id="segment-prompt" rows="9" placeholder="Exact prompt for this segment">${esc(state.prompt)}</textarea></div></section>
    ${s.needs_review?'<p class="note">Text was carried over from the previous layout. Review it for this range.</p>':''}
    <div class="field"><label for="segment-anchor">${continuing?'Additional reference':'Scene anchor'}</label><div class="segment-anchor-row"><select id="segment-anchor"><option value="none" ${anchorChoice==='none'?'selected':''}>${continuing?'Use generated continuity':'No anchor'}</option><option value="shot" ${anchorChoice==='shot'?'selected':''} ${shot().anchor?'':'disabled'}>Use shot anchor</option>${s.anchors.map((a,j)=>`<option value="asset:${esc(a.asset)}" ${anchorChoice==='asset:'+a.asset?'selected':''}>Segment reference ${j+1}</option>`).join('')}</select><button id="segment-reference-edit" class="h3-icon-button" title="Add guide at playhead" aria-label="Add guide at playhead">${uiIcon('image')}</button></div></div>

    ${segmentCastHtml(s,state)}`;
  if(shot().cast_setup)$('#segment-anchor').closest('.field').hidden=true;
  bindSegmentCast();
  for(const {selector,top} of scrollPositions){const el=$(selector);if(el)el.scrollTop=top;}
  $('#segment-editor-heading').textContent=`Segment ${i+1}`;
  $('#segment-editor-range').textContent=`${segmentTime(s.start)}–${segmentTime(s.end)} · ${((s.end-s.start)/24).toFixed(1)}s`;
  for(const [id,key,fallback] of [['render-lora','lora_id','v1-1000'],['render-use-mask','use_mask',true],['render-use-source-audio','use_source_audio',true]]){const old=$('#'+id),field=old.cloneNode(true);old.replaceWith(field);const value=s[key]??shot()[key]??fallback;if(field.type==='checkbox')field.checked=value;else field.value=value;}
  syncSegmentPlayhead();
  $('#segment-editor-panels').replaceChildren($('#segment-controls .segment-dialogue-panel'),$('#segment-controls .segment-prompt-panel'));
  const oldSeed=$('#render-seed'),seed=oldSeed.cloneNode(true);oldSeed.replaceWith(seed);seed.value=String(s.seed);seed.dataset.remoteValue=String(s.seed);e.editBase=structuredClone(s);
  e.fields=[['#segment-dialogue','dialogue'],['#segment-speaker','speaker'],['#segment-prompt','performance_prompt'],['#segment-anchor','anchor_choice'],['#render-seed','seed'],['#render-lora','lora_id'],['#render-use-mask','use_mask'],['#render-use-source-audio','use_source_audio']].map(([selector,key])=>{const field=$(selector);field.dataset.remoteValue=fieldValue(field);return {field,key};});
  for(const {field,key} of e.fields)field.addEventListener('input',()=>{
    if(key==='performance_prompt')field.dataset.promptEdited='true';
    retainFieldDraft(e,true);
    S.dirty=true;S.renderDraftDirty=true;autosaveStatus('Saving…');clearTimeout(e.timer);
    e.timer=setTimeout(()=>{void flushSegmentEdits().catch(()=>{});},400);
  });
  if(restoreFieldDraft(e,true)){S.dirty=true;S.renderDraftDirty=true;autosaveStatus('Saving restored edits…');e.timer=setTimeout(()=>{void flushSegmentEdits().catch(()=>{});},400);}
  $('#segment-reference-edit').onclick=guarded(openSegmentReference);
  $('#transcribe').onclick=guarded(transcribeClip);
  updateTranscriptionStatus(clip());
  $('#segment-prompt-refresh').onclick=guarded(updateSegmentPrompt);
  bindGuideSegmentMode();
  void syncSegmentMaskEditor().catch(error=>reportError(error));
  if(focus){const field=$('#'+focus.id);field?.focus({preventScroll:true});if(typeof field?.setSelectionRange==='function'&&focus.start!==null&&focus.end!==null)field.setSelectionRange(focus.start,focus.end);}

}
async function openSegmentReference(){
  await addGuideAtPlayhead();
}
const MIN_SEGMENT_FRAMES=24;
function boundaryLimits(items,index){
  return {min:items[index-1].start+MIN_SEGMENT_FRAMES,max:items[index].end-MIN_SEGMENT_FRAMES};
}
async function changeSegmentLayout(operation){
  const editor=segmentEditor,path=segmentPath();
  await flushShotAutosave();await saveMaskEdits();await writeQueue;
  if(editor!==segmentEditor)return;
  const revision=clip().revision,items=segmentEditor.plan.segments;
  const preview=await api(path+'/preview','POST',{revision,operation});
  if(editor!==segmentEditor)return;
  const retained=new Set(preview.segments.map(s=>s.id));
  const changed=items.filter(s=>!retained.has(s.id));
  if(!changed.length)return;
  const masks=(shot().cast_setup?.mask_tracks||[]).filter(t=>changed.some(s=>s.id===t.segment_id));
  if(masks.length){
    const rows=changed.map(seg=>[
      `Segment ${items.indexOf(seg)+1}`,
      masks.filter(t=>t.segment_id===seg.id).map(maskLabel),
    ]).filter(([,names])=>names.length);
    const action={split:'Split segment',merge:'Merge segments',boundary:'Move boundary',partition:'Apply layout'}[operation.operation];
    maskRemovalDialog({title:action+'?',description:`This removes ${masks.length===1?'the mask below and its':'the masks below and their'} points and tracking. Existing takes stay available.`,
      rows,action,confirmId:'segment-layout-confirm'});
    const accepted=await new Promise(resolve=>{
      const modal=$('#dialog');
      const closed=()=>{if(!modal.open){modal.removeEventListener('close',closed);resolve(false);}};
      modal.addEventListener('close',closed);
      $('#segment-layout-confirm').onclick=()=>{modal.removeEventListener('close',closed);modal.close();resolve(true);};
    });
    if(!accepted||editor!==segmentEditor)return;
  }
  await applySegmentLayout({...operation,remove_mask_ids:masks.map(t=>t.id)},revision);
}
async function applySegmentLayout(operation,revision){
  const e=segmentEditor,path=segmentPath(),frame=maskEditState?.frame??0,layout=JSON.stringify(e.plan?.segments.map(s=>[s.id,s.start,s.end]));
  await flushShotAutosave();await saveMaskEdits();
  if(e!==segmentEditor||!segmentActive())return;
  $('.segment-arrange').open=false;
  const task=writeQueue.then(async()=>{
    if(e!==segmentEditor||JSON.stringify(e.plan?.segments.map(s=>[s.id,s.start,s.end]))!==layout)throw Error('Segment layout changed. Try the action again.');
    const result=await api(path+'/layout','POST',{revision,operation});mergeSegmentClip(result);return result;
  });writeQueue=task.catch(()=>{});
  autosaveStatus('Saving…');
  try{
    await task;if(e!==segmentEditor)return;
    e.fields=[];e.selected=shot().segments.find(s=>s.start<=frame&&s.end>frame)?.id;
    localStorage.setItem(`h3segment:${e.clipId}:${e.shotId}`,e.selected);
    await loadSegmentPlan(true);maskSeek(frame);autosaveStatus('All changes saved');
  }catch(error){autosaveStatus('Not saved: '+error.message,true);throw error;}
}
async function segmentBoundary(boundaryIndex=null){
  await flushShotAutosave();const e=segmentEditor,seg=segmentSelected(),i=boundaryIndex===null?Math.min(e.plan.segments.indexOf(seg),e.plan.segments.length-2):boundaryIndex-1;
  if(i<0||i>=e.plan.segments.length-1)throw Error('Split the shot before adjusting a boundary.');
  const current=e.plan.segments[i],{min,max}=boundaryLimits(e.plan.segments,i+1);
  if(min>max||(min===max&&current.end===min))throw Error('Both segments need at least 1 second. Merge them to remove this boundary.');
  dialog(`Move boundary after segment ${i+1}`,`<label class="label" for="segment-boundary-frame">Source frame within this shot</label><input id="segment-boundary-frame" type="number" min="${min}" max="${max}" value="${Math.max(min,Math.min(max,current.end))}"><p class="note">Choose frame ${min}–${max} to keep at least 1 second on each side.</p><button id="segment-boundary-save" class="primary">Move boundary</button>`);
  $('#segment-boundary-save').onclick=guarded(async()=>{const field=$('#segment-boundary-frame');if(!field.reportValidity())return;$('#dialog').close();await changeSegmentLayout({operation:'boundary',index:i+1,frame:Number(field.value)});});
}
function segmentPreviousLayouts(){
  const history=shot().segment_layout_history||[];
  dialog('Previous segment layouts',history.length?history.slice().reverse().map(v=>`<details><summary>${v.segments.length} segments · ${new Date(v.created*1000).toLocaleString()}</summary>${v.segments.map((s,i)=>`<div class="segment-previous"><strong>Segment ${i+1} · ${segmentTime(s.start)}–${segmentTime(s.end)}</strong><p>${esc(s.prompt)}</p>${s.takes.map((t,j)=>`<a href="${asset(t.asset,true)}">Download take ${j+1}</a>`).join(' · ')}</div>`).join('')}</details>`).join(''):'<p>No previous layouts yet.</p>');
}
function segmentValidation(show=false) {
  const reason=guideRenderReason();
  const selector=/character/i.test(reason)?'.segment-cast-section':/mask|corrections/i.test(reason)?'#cast-mask-selector':/guide/i.test(reason)?'#shot-guides':'#segment-settings';
  $$('.generation-invalid').forEach(el=>{el.classList.remove('generation-invalid');el.removeAttribute('aria-invalid');el.querySelector('.generation-error')?.remove();});
  if(reason&&show){
    const target=$(selector)||$('.generation-panel');
    target.classList.add('generation-invalid');target.setAttribute('aria-invalid','true');
    const error=document.createElement('p');error.className='generation-error';error.setAttribute('role','alert');error.textContent=reason;target.append(error);
    target.scrollIntoView({block:'nearest'});target.querySelector('button,input,select')?.focus();
  }
  return reason;
}
function generationTargets(mode) {
  const e=segmentEditor,start=e.plan.segments.indexOf(segmentSelected()),jobs=S.data.jobs.filter(j=>j.clip_id===e.clipId&&j.shot_id===e.shotId&&['render','guide_render'].includes(j.kind)&&['submitting','queued','waiting','running','cancelling'].includes(j.state));
  return e.plan.segments.filter((seg,i)=>(mode==='all'||(mode==='segment'?i===start:i>=start)) &&
    (mode==='segment'||!jobs.some(j=>segmentJobScope(j).some(entry=>entry.id===seg.id))));
}
async function generateSegments(mode){
  const e=segmentEditor;
  if(e.submitting)return;
  if(mode==='segment'&&usesTimelineGuides(segmentSelected())&&segmentValidation(true))return;
  const price = generationPriceInput($(mode === "segment" ? "#queue-shot" : mode === "all" ? "#segment-all" : "#segment-from"));
  const before=JSON.stringify(generationTargets(mode));
  await flushShotAutosave();
  const targets=generationTargets(mode);
  if(targets.some(seg=>seg.render_mode!=='guides'&&(seg.use_mask??shot().use_mask??true)))await saveMaskEdits();
  await loadSegmentPlan(true);
  if(segmentEditor!==e)throw new DOMException('Workspace changed','AbortError');
  const s=shot(),seg=segmentSelected();
  if(connected&&mode!=='segment'){
    if(before!==JSON.stringify(generationTargets(mode)))throw Error('Segment settings changed. Review the range and click again.');
    // Validate the entire range before spending credits on its first job.
    for(const target of targets){
      try{
        planEditorRequest({operation:'render-input',clip:clip(),shotId:s.id,command:{method:'POST',route:'render-input',body:{options:{segments:{mode:'segment',segment_id:target.id,plan_revision:e.plan.plan_revision}}}}});
        const masks=maskRenderReason(s.segments.find(v=>v.id===target.id));if(masks)throw Error(masks);
      }
      catch(error){e.selected=target.id;renderSegmentControls();segmentValidation(true);throw Error(`Segment ${e.plan.segments.findIndex(v=>v.id===target.id)+1}: ${error.message}`);}
    }
    e.submitting=true;updateSegmentHistory();
    let accepted=0;
    try{
      for(const [i,target] of targets.entries()){
        if(segmentEditor!==e)throw new DOMException('Workspace changed','AbortError');
        await queue('render',e.shotId,e.clipId,undefined,adapter.execution().worker,{segments:{mode:'segment',segment_id:target.id,plan_revision:e.plan.plan_revision}},{expected_price:price.expected_prices[i]});
        accepted++;
      }
    }catch(error){throw Error(`${accepted} of ${targets.length} segments queued. ${error.message} Accepted jobs remain in the project queue.`);}
    finally{e.submitting=false;if(segmentEditor===e)updateSegmentHistory();}
  }else{
    const expected={seed:s.seed,prompt:s.prompt,render_mp:s.render_mp??0.5,...generationSettings(s)};
    await queue('render',e.shotId,e.clipId,expected,adapter.execution().worker,{model_offloading:adapter.execution().modelOffloading,
      segments:{mode,segment_id:seg.id,plan_revision:e.plan.plan_revision}}, price);
    updateSegmentHistory();
  }
}
function segmentTakeButton(seg,t,state){
  const selected=t.id===seg.selected_take;
  const changed=(seg.render_mode||'swap')!==(t.settings?.render_mode||'swap')||Object.entries(t.settings||{}).some(([k,v])=>['prompt','performance_prompt','dialogue','speaker','seed','connection','history_frames','anchor_mode','anchor','cast_inputs','render_mode','guide_prompt'].includes(k)&&JSON.stringify(seg[k])!==JSON.stringify(v));
  const label=!selected?'Use this take':state.current?'Selected':changed?'Restore saved settings':'Selected · settings changed';
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
  if(job.kind!=='guide_render'&&job.state==='running'&&job.data.segment_progress?.segment_id!==id)return 'queued';
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
  renderSegmentStrip();
  const e=segmentEditor,seg=segmentSelected(),state=segmentStatus(),s=shot(),c=clip();
  const jobs=S.data.jobs.filter(j=>j.clip_id===c.id&&j.shot_id===s.id&&['render','guide_render'].includes(j.kind));
  const activeJobs=jobs.filter(j=>['submitting','queued','waiting','running','cancelling'].includes(j.state));
  const active=activeJobs.find(j=>segmentJobPending(j,seg.id));
  const legacyActive=activeJobs.find(j=>j.kind==='render'&&!j.data.options?.segments&&!j.data.segment_scope);
  const awaiting=state.dependencies.filter(d=>!e.plan.states.find(v=>v.id===d.segment_id)?.reusable);
  const canWait=awaiting.every(d=>activeJobs.some(j=>j.state!=='cancelling'&&segmentJobPending(j,d.segment_id)));
  const blocked=state.blocked&&!canWait;
  const selectedIndex=e.plan.segments.indexOf(seg);
  const reason=s.mode==='preserve'?'Set this shot to Generate to render segments.':(!s.cast_setup&&!shotReference(c,s))?'Add a character reference.':state.prompt_needs_update?'Update or confirm this segment’s prompt after changing references.':legacyActive?'Wait for the whole-shot render to finish.':active?'This segment is already queued or generating. Edits apply to your next take.':blocked?'Generate or queue the preceding segment first.':awaiting.length?'Queue this segment now; it will start when its preceding take is ready.':state.status==='stale'?'Settings changed since this take.':'';
  $('#render-queue-reason').textContent=reason;
  $('#render-queue-reason').title=state.status==='stale'?'The selected take is kept. Generate again to apply changed settings or preceding takes.':reason;
  let review=$('#segment-queue-review');
  if(!review){review=document.createElement('div');review.id='segment-queue-review';review.className='row';$('#render-queue-reason').after(review);}
  review.hidden=!state.prompt_needs_update;
  if(state.prompt_needs_update&&review.dataset.segment!==seg.id){
    review.dataset.segment=seg.id;
    review.innerHTML='<button id="segment-queue-update-prompt" class="quiet">Update prompt</button><button id="segment-queue-keep-prompt" class="quiet">Keep current prompt</button>';
    $('#segment-queue-update-prompt').onclick=guarded(updateSegmentPrompt);
    $('#segment-queue-keep-prompt').onclick=guarded(async()=>{
      const key=e.selected,text=$('#segment-prompt').value;await flushShotAutosave();if(e!==segmentEditor||key!==e.selected)return;
      mergeSegmentClip(await api(segmentPath()+'/'+key,'PATCH',{revision:clip().revision,changes:{performance_prompt:text}}));await loadSegmentPlan(true);
    });
  }else if(!state.prompt_needs_update){review.replaceChildren();delete review.dataset.segment;}

  $('#queue-shot').disabled=!!active||!!legacyActive||s.mode==='preserve'||(!s.cast_setup&&!shotReference(c,s))||state.prompt_needs_update||blocked;
  if(!usesTimelineGuides(seg))uiButton($('#queue-shot'),active?(segmentJobState(active,seg.id)==='running'?'Generating…':'Queued'):activeJobs.length?'Queue segment':'Generate segment','play');
  $('#segment-generate-more').disabled=!!legacyActive||s.mode==='preserve';
  for(const [id,from] of [['segment-all',0],['segment-from',selectedIndex]]){
    const eligible=e.plan.segments.some((v,i)=>i>=from&&(!activeJobs.length||(!e.plan.states[i].current&&!activeJobs.some(j=>segmentJobScope(j).some(entry=>entry.id===v.id)))));
    $('#'+id).disabled=!eligible;
  }
  if(s.cast_setup&&!seg.cast_inputs.characters.length){$('#queue-shot').disabled=true;$('#render-queue-reason').textContent='Add a character to this segment.';}
  if(usesTimelineGuides(seg)){const reason=guideRenderReason();$('#render-queue-reason').textContent='';$('#queue-shot').disabled=!!active||!!legacyActive||s.mode==='preserve';if(!reason)segmentValidation();uiButton($('#queue-shot'),active?(segmentJobState(active,seg.id)==='running'?'Generating…':'Queued'):'Generate segment','play');review.hidden=true;}
  // Quote the visible selection immediately, including while shot autosave is pending.
  const megapixels=$('#render-mp').value;
  generationPriceButton($('#queue-shot'),{operation:'render-guided-swap',parameters:{megapixels},seconds:(seg.end-seg.start)/24});
  for(const [id,mode] of [['segment-all','all'],['segment-from','from']]){
    const targets=generationTargets(mode),button=$('#'+id);
    button.disabled=!targets.length||!!e.submitting;
    generationPriceButton(button,targets.map(v=>({operation:'render-guided-swap',parameters:{megapixels},seconds:(v.end-v.start)/24})),button.disabled);
  }
  if(e.submitting){$('#queue-shot').disabled=true;$('#segment-generate-more').disabled=true;}
  renderOutputs();
}

function syncSegmentWorkspace(s){
  if(!segmentActive())return;
  for(const [selector,value] of [['#render-mp',s.render_mp??0.5]])syncEditableField(selector,value);
  updateTranscriptionStatus(clip());
  $('.generation-model').textContent=segmentModelLabel(segmentSelected());syncRenderSize();
  if(!segmentDirty()&&!segmentEditor.pending)void loadSegmentPlan();updateSegmentHistory();
}
function drawRender(c,s){
  disposeSegmentEditor();
  drawLegacyRender(c,s);
  const player=$('.render-controls'),main=document.createElement('div');main.className='render-main';player.before(main);main.append(player);
  $('.generation-panel > .panel-title').innerHTML='<h2 id="segment-editor-heading">Segment</h2><span id="segment-editor-range" class="note"></span>';
  $('.render-generation').insertAdjacentHTML('afterbegin','<section id="segment-performance" class="segment-text-editors"><h3 class="inspector-heading"><button type="button" id="segment-performance-toggle" aria-expanded="false" aria-controls="segment-editor-panels">Prompt &amp; dialogue</button></h3><div id="segment-editor-panels" class="segment-editor-panels" role="region" aria-labelledby="segment-performance-toggle" hidden></div></section>');
  segmentEditor?.guideResize?.disconnect();
  segmentEditor={clipId:c.id,shotId:s.id,selected:localStorage.getItem(`h3segment:${c.id}:${s.id}`),plan:null,serial:0,fields:[],pending:null,timer:null};
  $('.render-window-controls')?.remove();$('#render-window-summary')?.remove();$('#render-use-anchor').closest('label').remove();
  const advanced=$('.render-generation > details:not(#segment-performance)');advanced.classList.add('segment-shared-settings');
  advanced.querySelector('label[for="render-prompt"]').remove();$('#render-prompt').remove();
  $('#render-model-offloading').closest('label').remove();
  $('#worker').closest('.field').remove();
  $('label[for="render-seed"]').textContent='Segment seed';
  advanced.hidden=true;
  advanced.insertAdjacentHTML('beforebegin','<div id="segment-controls" class="segment-controls"><p class="note">Loading segments…</p></div>');
  $('#mask-viewport').insertAdjacentHTML('afterend',`<div class="segment-timeline"><div class="row spread"><strong id="segment-count">Segments</strong><details class="segment-arrange"><summary>Arrange segments</summary><div class="segment-arrange-menu"><span class="note">Split into</span>${[5,10,15].map(n=>`<button data-segment-partition="${n*24}">${n}-second segments</button>`).join('')}<button id="segment-single">One segment for this shot</button><button id="segment-split">Split at playhead</button><button id="segment-boundary">Move selected boundary…</button><button id="segment-merge">Merge with next</button><button id="segment-previous">Previous layouts</button></div></details></div><div id="segment-timeline-surface" class="segment-timeline-surface"><div class="segment-ruler"><div id="segment-ruler-labels"></div></div><div id="segment-strip" class="segment-strip" role="group" aria-label="Generation segments"></div><div class="segment-playhead" aria-hidden="true"></div></div></div>`);
  const frames=s.end-s.start;
  $('#segment-ruler-labels').innerHTML=Array.from({length:5},(_,i)=>`<span style="left:${i*25}%">${segmentTime(Math.round(frames*i/4))}</span>`).join('');
  drawOutputToolbar();
  $('#queue-shot').insertAdjacentHTML('afterend',`<button id="segment-generate-more" class="h3-icon-button" title="More generation options" aria-label="More generation options" aria-expanded="false" aria-controls="segment-generate-menu">⌄</button><div id="segment-generate-menu" class="segment-generate-menu" hidden><button id="segment-all">Queue all segments</button><button id="segment-from">Queue from here</button></div>`);
  bindShotAutosave([['#render-mp','render_mp',s.render_mp??0.5]]);
  $('#render-mp').addEventListener('input',updateSegmentHistory);
  const timeline=$('.segment-timeline');player.append(timeline);$('#segment-timeline-surface').insertAdjacentHTML('beforeend','<div id="shot-guides" class="shot-guides" role="group" aria-label="Timeline guide frames"></div>');
  timeline.querySelector(':scope > .row').insertAdjacentHTML('beforeend',`<div class="guide-toolbar"><span id="guide-count" class="note">No guides</span><button id="guide-add" class="quiet">${uiIcon('plus')}Add guide at playhead</button></div>`);
  $('#guide-add').onclick=guarded(addGuideAtPlayhead);
  const guideEditor=segmentEditor;guideEditor.guideResize=new ResizeObserver(()=>{if(segmentEditor===guideEditor&&timeline.isConnected){renderGuideStrip();maskDrawPoints();}else guideEditor.guideResize.disconnect();});guideEditor.guideResize.observe($('#segment-timeline-surface'));guideEditor.guideResize.observe($('#mask-viewport'));
  const transport=document.createElement('div');transport.className='shot-shared-transport';
  transport.append($('.mask-transport'));player.insertBefore(transport,timeline);
  $('.segment-ruler').append($('#mask-frame'));$('#mask-frame').setAttribute('aria-label','Shot timeline');
  const actions=document.createElement('div');actions.className='shot-timeline-actions';
  for(const [id,label,icon] of [['segment-split','Split at playhead','cut'],['segment-boundary','Adjust boundaries','move']]){const button=$('#'+id);uiButton(button,label,icon,true);button.classList.add('h3-icon-button');actions.append(button);}
  $('.h3-playback-main').append(actions);
  actions.insertAdjacentHTML('beforeend',`<button id="shot-capture" class="h3-icon-button" title="Capture frame · download original for editing" aria-label="Capture frame">${uiIcon('camera')}</button>`);
  $('#shot-capture').onclick=guarded(addGuideAtPlayhead);$('#shot-capture').title='Add guide at playhead';$('#shot-capture').setAttribute('aria-label','Add guide at playhead');
  // Keep editing actions on one tool row, in their visible execution order.
  const tools=$('.mask-tools');tools.append($('#cast-mask-selector'),$('#mask-show').closest('label'),$('#mask-update'),$('#mask-track'));$('.shot-viewer-header').remove();
  const deck=$('.mask-control-deck'),details=deck.querySelector('details');details.id='mask-settings-popover';details.setAttribute('popover','auto');details.open=true;details.querySelector('summary').textContent='Edit mask';
  details.querySelector('summary').insertAdjacentHTML('afterend',`<button class="h3-icon-button quiet mask-settings-close" aria-label="Close mask settings" popovertarget="mask-settings-popover" popovertargetaction="hide">${uiIcon('close')}</button>`);
  $('#mask-name-form').after($('.mask-adjustments'));deck.prepend(details);tools.append(details);
  const settings=document.createElement('section');settings.className='segment-render-settings';settings.innerHTML='<h3 class="inspector-heading"><button type="button" id="segment-settings-toggle" aria-expanded="false" aria-controls="segment-settings-panel">Render settings</button></h3><div id="segment-settings-panel" class="segment-render-settings-body" role="region" aria-labelledby="segment-settings-toggle" hidden></div>';
  const generation=$('.render-generation'),controls=$('#segment-controls'),performance=$('#segment-performance');
  const saved=$('#shot-save-status');
  const keep=new Set([controls,performance,...[...generation.children].filter(n=>n.contains($('#queue-shot'))||n.id==='render-queue-reason'||n.id==='render-sync-conflict'||n.classList.contains('autosave-status')||n.contains(saved))]);
  for(const node of [...generation.children])if(!keep.has(node))settings.lastElementChild.append(node);
  generation.prepend(controls,performance,settings);advanced.open=false;
  $('.render-layout').classList.add('unified-shot-layout');
  const inspector=document.createElement('div');inspector.className='generation-scroll';generation.prepend(inspector);inspector.append(controls,performance,settings);
  bindInspectorAccordion(performance,inspector);bindInspectorAccordion(settings,inspector);
  const footer=document.createElement('div');footer.className='generation-footer';generation.append(footer);
  for(const node of [...generation.children])if(node!==inspector&&node!==footer)footer.append(node);
  $('.render-results h2').firstChild.textContent='Outputs ';


  $('#render-seed').oninput=null;
  $('#queue-shot').onclick=guarded(()=>generateSegments('segment'));
  $('#segment-generate-more').onclick=()=>{
    const menu=$('#segment-generate-menu');menu.hidden=!menu.hidden;
    $('#segment-generate-more').setAttribute('aria-expanded',String(!menu.hidden));
    if(!menu.hidden)menu.querySelector('button:not(:disabled)')?.focus();
  };
  $('#segment-from').onclick=guarded(async()=>{closeSegmentGenerateMenu();await generateSegments('from');});
  $('#segment-all').onclick=guarded(async()=>{closeSegmentGenerateMenu();await generateSegments('all');});
  $('#segment-single').onclick=guarded(()=>changeSegmentLayout({operation:'partition',frames:shot().end-shot().start}));
  $$('[data-segment-partition]').forEach(b=>b.onclick=guarded(()=>changeSegmentLayout({operation:'partition',frames:Number(b.dataset.segmentPartition)})));
  $('#segment-split').onclick=guarded(()=>changeSegmentLayout({operation:'split',frame:maskEditState?.frame??0}));
  $('#segment-boundary').onclick=guarded(()=>segmentBoundary());
  $('#segment-merge').onclick=guarded(()=>changeSegmentLayout({operation:'merge',index:segmentEditor.plan.segments.indexOf(segmentSelected())+1}));
  $('#segment-previous').onclick=segmentPreviousLayouts;
  const audio=$('#render-use-source-audio');audio.removeAttribute('aria-describedby');audio.closest('label').title='Guide performance with source audio. Turn off for music or singing; exports keep the original soundtrack.';
  $('#render-use-mask').closest('label').title='Limit changes to the assigned masks. Turn off to regenerate the entire frame.';
  const resolution=$('#render-mp');resolution.closest('.field').querySelector('label').textContent='Shot resolution (MP)';resolution.title='Shared by all segments so overlapping history and assembled takes have matching dimensions.';
  $('#render-size-output').title='Fits the shot to the model’s 32-pixel grid.';
  settings.querySelectorAll('p.note').forEach(note=>note.remove());
  bindShotViewer(c,s);
  $('#queue-shot').disabled=true;uiButton($('#queue-shot'),'Loading segments…','play');
  $('#segment-generate-more').disabled=true;
  void loadSegmentPlan(true);
}

// Keep keyboard focus on the heading while revealing useful content in this
// scroll container. Tall panels start at the top; short panels need only a nudge.
function bindInspectorAccordion(section,scroller){
  const button=section.querySelector('.inspector-heading button'),panel=section.lastElementChild;
  button.onclick=()=>{
    const open=button.getAttribute('aria-expanded')!=='true';
    button.setAttribute('aria-expanded',String(open));panel.hidden=!open;
    if(!open)return;
    requestAnimationFrame(()=>{
      if(!section.isConnected||panel.hidden)return;
      const bounds=scroller.getBoundingClientRect(),content=section.getBoundingClientRect();
      if(content.bottom>bounds.bottom||content.top<bounds.top){
        const delta=content.height>bounds.height?content.top-bounds.top:content.bottom-bounds.bottom;
        scroller.scrollBy({top:delta,behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth'});
      }
    });
  };
}
