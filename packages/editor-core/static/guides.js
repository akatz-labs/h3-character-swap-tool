// Guide drafts stay separate from immutable generated versions and their active selection.
let guideSession=null;
// Small per-workspace LRU. Revision matching invalidates edits, uploads and job
// completions; TTL also refreshes project references changed in another view.
const guideModalCache=new Map();
let guideCacheIdentity=null;
function guideCacheKey(cid,sid){
  const identity=adapter.identity();
  if(identity!==guideCacheIdentity){guideModalCache.clear();guideCacheIdentity=identity;}
  return JSON.stringify([identity,S.batch,cid,sid]);
}
function cachedGuideData(key,revision){
  const entry=guideModalCache.get(key);
  if(!entry)return null;
  guideModalCache.delete(key);
  if(entry.revision!==revision||Date.now()-entry.time>300000)return null;
  guideModalCache.set(key,entry);
  return entry.data;
}
function rememberGuideData(key,revision,data){
  guideModalCache.delete(key);
  guideModalCache.set(key,{revision,data,time:Date.now()});
  while(guideModalCache.size>8)guideModalCache.delete(guideModalCache.keys().next().value);
}

const GUIDE_PROPAGATION='Apply the changes shown in the edited guide frames throughout the source video. Preserve the source motion, timing, camera movement and unedited content.';
function guideBase(cid=clip().id,sid=shot().id){return `clips/${cid}/shots/${sid}/guides`;}
function guideRecord(session=guideSession){return S.data.clips.find(c=>c.id===session?.cid)?.shots.find(s=>s.id===session.sid)?.guides?.find(g=>g.id===session.id);}
function guideIsCurrent(g,c=clip(),s=shot()){return g.source===(c.render_source||c.source)&&g.frame>=s.start&&g.frame<s.end&&JSON.stringify(g.dimensions)===JSON.stringify(c.dimensions);}
function guidesForSegment(){const s=shot(),seg=segmentSelected();return (s?.guides||[]).filter(g=>!g.archived&&g.enabled&&g.frame>=s.start+seg.start&&g.frame<s.start+seg.end&&g.versions.some(v=>v.id===g.approved));}
function usesTimelineGuides(seg){return ['guides','swap_pika'].includes(seg?.render_mode);}
function segmentModelLabel(seg){if(seg?.lora_id==='viggle-remake-swap')return 'Powered by Viggle Remake · MiniMax H3 · 6 steps';return seg?.render_mode==='swap_pika'?'MiniMax H3 · FL2VA · Pika 6 · Swap V1.5 ×1.0':seg?.render_mode==='guides'?'MiniMax H3 · Ripple V1 · 6 steps':'MiniMax H3 · '+loRALabel(seg?.lora_id??shot().lora_id)+' · Turbo 8';}
function guideRenderReason(){
  const seg=segmentSelected(),chosen=guidesForSegment();
  if(seg.end-seg.start<5||seg.end-seg.start>362)return 'Split this segment to use 5–362 frames per guided render.';
  if(chosen.some(g=>!guideIsCurrent(g)))return 'A guide belongs to an older source. Disable it and add a new guide.';
  if(seg.render_mode==='swap_pika')return maskRenderReason(seg);
  return chosen.length?'':'Approve at least one guide inside this segment.';
}
function renderGuideStrip(){
  const target=$('#shot-guides'),surface=$('#segment-timeline-surface');if(!target||!surface||!shot())return;
  const shotValue=shot(),frames=shotValue.end-shotValue.start,width=surface.clientWidth;
  if(!width)return;
  const items=(shotValue.guides||[]).filter(g=>!g.archived).sort((a,b)=>a.created-b.created);
  const size=parseFloat(getComputedStyle(target).getPropertyValue('--guide-size'));
  const positions=new Map(),ordered=[...items].sort((a,b)=>a.frame-b.frame);let previous=-6;
  for(const g of ordered){const x=(g.frame-shotValue.start+.5)/frames*width;const left=Math.max(previous+6,Math.max(0,Math.min(width-size,x-size/2)));positions.set(g.id,left);previous=left;}
  let next=width-size+6;for(const g of ordered.reverse()){const left=Math.min(positions.get(g.id),next-6);positions.set(g.id,Math.max(0,left));next=left;}
  const html=items.map((g,index)=>{
    const approved=g.versions.find(v=>v.id===g.approved),version=approved,frame=g.frame-shotValue.start;
    const current=guideIsCurrent(g),active=current&&g.enabled&&!!approved;
    const state=!current?'Source changed':!g.enabled?'Disabled':active?'Approved':version?'Needs approval':'Source only';
    const x=Math.max(0,Math.min(width,(frame+.5)/frames*width)),left=positions.get(g.id);
    return `<div class="guide-position ${active?'guide-approved':version&&current&&g.enabled?'guide-edited':'guide-inactive'} ${g.enabled?'':'guide-disabled'}" style="--guide-x:${x}px;--guide-left:${left}px;--guide-order:${index+4}"><span class="guide-line" aria-hidden="true"></span><button class="guide-marker" data-guide="${esc(g.id)}" title="Frame ${frame} · ${segmentTime(frame)} · ${state}" aria-label="Edit guide at frame ${frame}, ${state}"><img src="${imageAsset(version?.asset||g.source_asset,256)}" alt="" loading="lazy"></button><button type="button" class="guide-toggle" data-guide-toggle="${esc(g.id)}" aria-pressed="${g.enabled}" aria-label="${g.enabled?'Disable':'Enable'} guide at frame ${frame}" title="${g.enabled?'Disable':'Enable'} guide at frame ${frame}"><span aria-hidden="true">${!g.enabled?'−':active?'✓':version?'!':'○'}</span></button><span class="guide-frame-label" aria-hidden="true">F${frame}</span></div>`;
  }).join('');
  const key=html;
  if(target.dataset.content!==key){target.innerHTML=html;target.dataset.content=key;
    target.querySelectorAll('[data-guide]').forEach(button=>{
      const open=guarded(async()=>{maskPause();await openGuide(button.dataset.guide);});
      // Opening an editor keeps the thumbnail's normal appearance while loading.
      button.onclick=()=>open();
    });
    target.querySelectorAll('[data-guide-toggle]').forEach(button=>button.onclick=guarded(async event=>{
      event.stopPropagation();button.disabled=true;
      try{await setGuideEnabled(clip().id,shotValue.id,button.dataset.guideToggle,button.getAttribute('aria-pressed')!=='true');target.querySelector('[data-guide-toggle="'+CSS.escape(button.dataset.guideToggle)+'"]')?.focus({preventScroll:true});}
      finally{if(button.isConnected)button.disabled=false;}
    }));
  }
  const count=$('#guide-count');if(count)count.textContent=items.length?`${items.length} guide${items.length===1?'':'s'}`:'No guides';
}
// Capture the normalized source, never the preview canvas or its mask overlays.
async function captureGuideSource(c,s,frame){
  const video=document.createElement('video'),canvas=document.createElement('canvas');
  const signal=AbortSignal.timeout(30000);
  video.crossOrigin='anonymous';video.muted=true;video.playsInline=true;video.preload='auto';
  const wait=event=>new Promise((resolve,reject)=>{
    const cleanup=()=>{video.removeEventListener(event,done);video.removeEventListener('error',failed);signal.removeEventListener('abort',aborted);};
    const done=()=>{cleanup();resolve();},failed=()=>{cleanup();reject(Error('Could not load the source frame'));},aborted=()=>{cleanup();reject(Error('Source frame capture timed out'));};
    video.addEventListener(event,done,{once:true});video.addEventListener('error',failed,{once:true});signal.addEventListener('abort',aborted,{once:true});
    if(signal.aborted)aborted();
  });
  try{
    const ready=wait('loadeddata');video.src=asset(c.render_source||c.source);await ready;
    // Hosted working sources are normalized to 24 fps. Seek into the frame,
    // avoiding floating-point ambiguity at the preceding frame boundary.
    const sought=wait('seeked');video.currentTime=(s.start+frame+.5)/24;await sought;
    [canvas.width,canvas.height]=c.dimensions;
    const context=canvas.getContext('2d');context.fillStyle='#000';context.fillRect(0,0,canvas.width,canvas.height);
    const scale=Math.min(canvas.width/video.videoWidth,canvas.height/video.videoHeight);
    const w=c.canvas_aligned?canvas.width:video.videoWidth*scale,h=c.canvas_aligned?canvas.height:video.videoHeight*scale;
    context.drawImage(video,(canvas.width-w)/2,(canvas.height-h)/2,w,h);
    const blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/png'));
    if(!blob)throw Error('Could not capture the source frame');
    return blob;
  }finally{video.removeAttribute('src');video.load();canvas.width=canvas.height=0;}
}
let guideCapturePending=false;
async function addGuideAtPlayhead(){
  if(guideCapturePending)return;
  guideCapturePending=true;
  const c=clip(),s=shot(),cid=c.id,sid=s.id,frame=maskEditState?.frame??0,project=S.batch;
  const button=$('#guide-add'),capture=$('#shot-capture'),label=button?.innerHTML;
  if(button){button.disabled=true;button.setAttribute('aria-busy','true');button.textContent='Adding guide…';}
  if(capture)capture.disabled=true;
  try{
    await flushShotAutosave();
    const source=connected?await captureGuideSource(c,s,frame):null;
    if(S.batch!==project||clip()?.id!==cid||shot()?.id!==sid)throw Error('Shot changed during capture');
    const result=await guideWrite(cid,sid,(base,revision)=>{
      if(!source)return api(base,'POST',{revision,frame});
      const form=new FormData();form.set('revision',String(revision));form.set('frame',String(frame));form.set('source',source,'source-frame.png');
      return api(base,'POST',form);
    });
    mergeSegmentClip(result.clip);
    if(clip()?.id===cid&&shot()?.id===sid){renderGuideStrip();await openGuide(result.guide_id);}
  }finally{
    guideCapturePending=false;
    if(button?.isConnected){button.disabled=false;button.removeAttribute('aria-busy');button.innerHTML=label;}
    if(capture?.isConnected)capture.disabled=false;
  }
}

async function guideWrite(cid,sid,action){
  const task=writeQueue.then(async()=>{const c=S.data.clips.find(c=>c.id===cid);if(!c)throw Error("Shot no longer available");return action(guideBase(cid,sid),c.revision);});
  writeQueue=task.catch(()=>{});return task;
}
// A shot revision also advances for unrelated settings and completed jobs.
// Retry only a rejected metadata save, after checking this guide's exact inputs.
// Never replay a quote, generation request, upload or ambiguous network failure.
function guideSettings(g){
  return {...(adapter.mode==='local'?{edit_model:g.edit_model||'gpt-image'}:{}),prompt:g.prompt,quality:g.quality,references:[...g.references],helpers:structuredClone(g.helpers||[]),edit_source_asset:g.edit_source_asset||g.source_asset};
}
async function saveGuideDraft(session,base,revision,approve=false){
  const {cid,sid,id,draft}=session,changes=structuredClone(draft.values);
  const original=guideRecord(session);
  if(!original)throw Error('The guide was removed. Your draft is preserved.');
  const source=g=>({source:g.source,source_asset:g.source_asset,frame:g.frame,dimensions:g.dimensions});
  const expectedSource=source(original);
  const check=()=>{
    const c=S.data.clips.find(c=>c.id===cid),s=c?.shots.find(s=>s.id===sid),g=s?.guides?.find(g=>g.id===id);
    if(!g||g.archived||!guideIsCurrent(g,c,s)||!sameSettings(source(g),expectedSource))throw Error('The guide source changed. Your draft is preserved; close and reopen this guide.');
    if(!sameSettings(guideSettings(g),draft.base)||(approve&&g.approved!==session.approved))throw Error('This guide changed elsewhere. Your draft is preserved; close and reopen it before saving.');
  };
  const save=revision=>api(base+'/'+id+(approve?'/save':''),approve?'POST':'PATCH',{revision,changes,...(approve?{version_id:session.preview}:{})});
  check();
  try{return await save(revision);}
  catch(error){
    if(error.status!==409)throw error;
    const fresh=await api(`clips/${cid}`);mergeSegmentClip(fresh);
    check();
    return save(fresh.revision);
  }
}
async function setGuideEnabled(cid,sid,id,enabled){
  await flushShotAutosave();
  const result=await guideWrite(cid,sid,(base,revision)=>api(base+'/'+id,'PATCH',{revision,changes:{enabled}}));
  mergeSegmentClip(result);renderGuideStrip();await loadSegmentPlan(true);
}
function guideDraftChanged(session){
  if(session!==guideSession)return;
  const retained=retainGuideDraft(localStorage,session.draftKey,session.draft);
  $('#guide-draft-status').textContent=retained?'Draft saved':'Browser storage unavailable — save before closing.';
  const changed=!sameSettings(session.draft.values,session.draft.base)||session.preview!==session.approved;
  const pending=session.preview&&!guideRecord(session).versions.some(v=>v.id===session.preview);
  $('#guide-save').disabled=session.busy||pending;
  $('#guide-save').title=pending?'Wait for the selected version to finish':changed?'Apply the selected version to this timeline guide':'Keep the current guide and close';
  const guide=guideRecord(session),version=guide.versions.find(v=>v.id===session.preview);
  $('#guide-save').textContent=version&&version.id!==guide.approved?'Use this version':'Save settings';
  $('#guide-selection-status').textContent=version?`Version ${guide.versions.indexOf(version)+1} ${version.id===guide.approved&&guide.enabled?'in use':'selected'}`:'';
  updateGuideGenerationPrice(session);
  renderGuideSource();
  const values=session.draft.values,parts=values.prompt?[values.prompt]:[];
  parts.push(...values.helpers.map(h=>session.presets.find(p=>p.id===h.id).label+':\n'+h.text));
  let preview=values.helpers.length?parts.join('\n\n'):values.prompt;
  if(values.edit_model==='h3-image')preview=['<Picture 1> is the source frame to edit.',...values.references.map((_,i)=>`<Picture ${i+2}> is reference image ${i+2}.`)].join('\n')+'\n\n'+preview.replace(/\bImage (\d+)\b/g,'<Picture $1>').replaceAll('Images 2 onward','Pictures 2 onward');
  $('#guide-full-prompt').textContent=preview;
}
function updateGuideGenerationPrice(session) {
  const running=S.data.jobs.some(p=>p.kind==='guide_generate'&&p.clip_id===session.cid&&p.shot_id===session.sid&&p.data.options?.guide?.id===session.id&&['submitting','queued','waiting','running'].includes(p.state));
  generationPriceButton($('#guide-generate'),{operation:'generate-guide',parameters:{quality:session.draft.values.quality||'medium'}},session.busy||running||!guideIsCurrent(guideRecord(session))||!session.providerReady);
}
async function openGuide(id){
  const cid=clip().id,sid=shot().id,start=shot().start;
  const cached=shot().guides?.find(g=>g.id===id);
  dialog(cached?`Guide · frame ${cached.frame-start}`:'Guide',`<div class="guide-editor guide-loading" aria-busy="true"><div class="guide-review"><div class="guide-comparison" aria-hidden="true">${['Source','Edited version'].map(label=>`<figure><figcaption>${label}</figcaption><div class="guide-result guide-skeleton"></div></figure>`).join('')}</div><section class="guide-history"><h3>Versions</h3><div class="guide-versions">${cached?.versions.length?cached.versions.slice(-3).map(v=>`<article class="guide-version"><button disabled><span class="guide-loading-thumb guide-skeleton"></span></button>${v.prompt||v.helpers?.length?'<button class="guide-restore quiet" disabled>Use settings</button>':''}</article>`).join(''):'<p class="note">No versions yet</p>'}</div></section></div><div class="guide-inspector"><div class="guide-parameters"><label>Edit instructions</label><div class="guide-skeleton guide-loading-fields" aria-hidden="true"></div><p id="guide-loading-status" role="status">Loading guide…</p>${['Full prompt','Reference images','Edit model','Quality'].map(label=>`<label>${label}</label><div class="guide-skeleton guide-loading-controls" aria-hidden="true"></div>`).join('')}</div><div class="guide-generation"><button class="primary" disabled>Generate guide</button></div></div></div><footer class="guide-footer"><div class="guide-footer-actions"><button disabled>Remove guide</button><button disabled>Enabled</button></div><div class="guide-footer-confirm"><button id="guide-loading-close">Close</button><button class="primary" disabled>Use this version</button></div></footer>`);
  $('#guide-loading-close').onclick=()=>$('#dialog').close();
  $('#dialog').classList.add('guide-modal');
  const opening=anchorDialogVersion;
  const isCurrent=()=>$('#dialog')?.open&&anchorDialogVersion===opening&&clip()?.id===cid&&shot()?.id===sid;
  let data;
  try {
    await flushShotAutosave();await saveMaskEdits();await writeQueue;
    if(!isCurrent())return;
    const key=guideCacheKey(cid,sid),revision=clip().revision;
    data=cachedGuideData(key,revision);
    if(!data){
      try { data=await api(guideBase(cid,sid)); }
      catch(error) {
        // A same-workspace metadata invalidation can race an autosave. Retry
        // the read once after queued writes, never a generation or mutation.
        if(!isExpectedCancellation(error)||!isCurrent())throw error;
        await writeQueue;
        if(!isCurrent())return;
        data=await api(guideBase(cid,sid));
      }
      // Never associate a response with a revision that advanced in flight.
      if(isCurrent()&&clip().revision===revision)rememberGuideData(key,revision,data);
    }
    if(!isCurrent())return;
    if(!data.guides.some(g=>g.id===id))throw Error('Guide no longer exists');
  } catch(error) {
    if(!isCurrent())return;
    $('.guide-loading').setAttribute('aria-busy','false');
    $('#guide-loading-status').textContent=error.message;
    const retry=document.createElement('button');retry.textContent='Retry';retry.onclick=guarded(()=>openGuide(id));
    $('#guide-loading-status').after(retry);
    return;
  }
  const g=data.guides.find(g=>g.id===id);
  const values=guideSettings(g);
  const draftKey=`guide-draft:${adapter.identity()}:${S.batch}:${cid}:${sid}:${id}`,draft=restoreGuideDraft(localStorage,draftKey,values);
  draft.values.helpers=draft.values.helpers.filter(h=>data.helper_presets.some(p=>p.id===h.id));
  draft.values.references=draft.values.references.filter(id=>data.references.some(r=>r.asset===id));
  if(![g.source_asset,...(g.source_uploads||[]),...g.versions.map(v=>v.asset)].includes(draft.values.edit_source_asset))draft.values.edit_source_asset=values.edit_source_asset;
  const session={cid,sid,id,draft,draftKey,references:data.references,presets:data.helper_presets,preview:g.approved,approved:g.approved,autoSavedVersion:g.auto_saved_version,versionsKey:null,busy:false,submission:null};
  const active=S.data.jobs.find(j=>j.kind==='guide_generate'&&j.data.options?.guide?.id===id&&['queued','waiting','running'].includes(j.state));
  session.preview=active?.id||g.approved||g.versions.at(-1)?.id||null;
  dialog(`Guide · frame ${g.frame-shot().start}`,`<div class="guide-editor">
    <div class="guide-review"><div class="guide-comparison">
      <figure id="guide-source-drop-target"><figcaption><span>Source</span><div class="guide-image-actions"><button type="button" id="guide-reset-source" class="guide-source-action quiet" title="Reset source to the original shot frame" aria-label="Reset source to original frame">${uiIcon('refresh')}<span>Reset</span></button><a id="guide-source-download" class="h3-icon-button" download title="Download source image" aria-label="Download source image">${uiIcon('download')}</a><button type="button" class="h3-icon-button quiet" data-guide-upload="source" title="Upload source image" aria-label="Upload source image">${uiIcon('upload')}</button></div></figcaption><div id="guide-source" class="guide-result"></div></figure>
      <figure id="guide-drop-target"><figcaption><span id="guide-result-caption">Edited version</span><div class="guide-image-actions"><button type="button" id="guide-use-source" class="guide-source-action quiet" title="Use this edited version as the source for a follow-up edit" aria-label="Use edited version as source" disabled>${uiIcon('left')}<span>Use as source</span></button><a id="guide-result-download" class="h3-icon-button" download title="Download edited image" aria-label="Download edited image" aria-disabled="true" tabindex="-1">${uiIcon('download')}</a><button type="button" class="h3-icon-button quiet" data-guide-upload="version" title="Upload guide image" aria-label="Upload guide image">${uiIcon('upload')}</button></div></figcaption><div id="guide-result" class="guide-result"></div></figure>
    </div><input id="guide-source-upload" type="file" accept="image/*" hidden><input id="guide-version-upload" type="file" accept="image/*" hidden>
    <section class="guide-history"><h3>Versions <span id="guide-version-count"></span></h3><div id="guide-versions" class="guide-versions" aria-label="Guide versions"></div></section><div id="guide-jobs" role="status" aria-live="polite"></div></div>
    <div class="guide-inspector"><div class="guide-parameters"><label for="guide-prompt">Edit instructions</label><textarea id="guide-prompt" rows="4" maxlength="12000" placeholder="Describe what you want to change in this frame…">${esc(draft.values.prompt)}</textarea>
      <div class="guide-helper-chips" role="group" aria-label="Prompt helpers">${session.presets.map(p=>`<button type="button" data-guide-helper="${esc(p.id)}" aria-pressed="${draft.values.helpers.some(h=>h.id===p.id)}" title="Enable ${esc(p.label.toLowerCase())} instructions">${esc(p.label)}</button>`).join('')}</div>
      <details class="guide-full-prompt guide-disclosure"><summary id="guide-full-prompt-label">Full prompt</summary><pre id="guide-full-prompt" tabindex="0" aria-labelledby="guide-full-prompt-label"></pre></details>
      <details class="guide-references guide-disclosure" open><summary>Reference images <span id="guide-ref-count"></span></summary><div id="guide-reference-list" class="guide-reference-list"></div><button type="button" class="quiet" data-guide-upload="reference">${uiIcon('upload')}Upload reference</button><input id="guide-reference-upload" type="file" accept="image/*" hidden></details>
      <div class="guide-model"><label for="guide-model">Edit model</label><select id="guide-model"><option value="gpt-image">GPT Image 2.6 Flare</option>${draft.values.edit_model==='h3-image'?'<option value="h3-image" selected>H3 Image Edit</option>':''}</select><label for="guide-quality">Quality</label><select id="guide-quality">${['low','medium','high'].map(q=>`<option value="${q}" ${q===draft.values.quality?'selected':''}>${q[0].toUpperCase()+q.slice(1)}</option>`).join('')}</select></div>
      </div><div class="guide-generation"><button id="guide-generate" class="primary" disabled><span id="guide-generate-label">Generate guide</span></button><span id="guide-provider-status" class="guide-connection-status" role="status"></span></div>
    </div></div><footer class="guide-footer"><div class="guide-footer-actions"><button id="guide-remove" class="danger">${uiIcon('trash')}Remove guide</button><button type="button" id="guide-toggle-enabled" role="switch" aria-checked="${g.enabled}"><span class="guide-switch" aria-hidden="true"></span>Enabled</button></div><span id="guide-error" role="alert"></span><span id="guide-selection-status" role="status"></span><div class="guide-footer-confirm"><button id="guide-cancel">Close</button><button id="guide-save" class="primary" disabled>Use this version</button></div></footer>`);
  session.dropLifetime=new AbortController();
  guideSession=session;$('#dialog-close').insertAdjacentHTML('beforebegin','<span id="guide-draft-status" role="status" title="Guide settings are saved in this browser"></span>');$('#dialog').classList.add('guide-modal');$('#dialog-close').innerHTML=uiIcon('close');$('#dialog-close').setAttribute('aria-label','Close guide');
  $('#guide-prompt').oninput=event=>{draft.values.prompt=event.target.value;guideDraftChanged(session);};
  $('#guide-model').onchange=event=>{draft.values.edit_model=event.target.value;showGuideProvider(session.providerStatus);guideDraftChanged(session);};
  $('#guide-quality').onchange=event=>{draft.values.quality=event.target.value;guideDraftChanged(session);};
  $('#guide-reset-source').onclick=()=>setGuideSource(guideRecord(session).source_asset);
  $('#guide-use-source').onclick=()=>setGuideSource(guideRecord(session).versions.find(v=>v.id===session.preview).asset);
  $('#guide-cancel').onclick=()=>$('#dialog').close();
  $$('[data-guide-helper]').forEach(button=>button.onclick=()=>{
    const id=button.dataset.guideHelper,values=draft.values;
    values.helpers=values.helpers.some(h=>h.id===id)?values.helpers.filter(h=>h.id!==id):[...values.helpers,{id,text:session.presets.find(p=>p.id===id).text}];
    renderGuideHelpers();guideDraftChanged(session);
  });
  renderGuideHelpers();
  const run=action=>guarded(async()=>{
    if(session.busy)return;session.busy=true;guideDraftChanged(session);$('#guide-error').textContent='';
    try{await action();}catch(error){if(session===guideSession)$('#guide-error').textContent=error.message;throw error;}
    finally{session.busy=false;if(session===guideSession){guideDraftChanged(session);renderGuideVersions();}}
  });
  $('#guide-save').onclick=run(async()=>{
    if(sameSettings(draft.values,draft.base)&&session.preview===session.approved){$('#dialog').close();return;}
    const result=await guideWrite(cid,sid,(base,revision)=>saveGuideDraft(session,base,revision,true));mergeSegmentClip(result);localStorage.removeItem(draftKey);$('#dialog').close();renderGuideStrip();await loadSegmentPlan(true);
  });
  $('#guide-generate').onclick=run(async()=>{
    const price=generationPriceInput($("#guide-generate"));
    const previous=session.preview;
    session.submission={id:'guide-submitting',state:'submitting'};session.preview=session.submission.id;renderGuideVersions();
    try{
      const job=await guideWrite(cid,sid,async(base,revision)=>{
        const changes=structuredClone(draft.values);
        {
          // Save inputs now; successful completion activates the generated version.
          const updated=await saveGuideDraft(session,base,revision);
          mergeSegmentClip(updated);revision=updated.revision;draft.base=structuredClone(changes);
        }
        return api(base+'/'+id+'/generate','POST',{revision,changes,...price,...(adapter.mode==='local'?{worker:adapter.execution().worker,model_offloading:adapter.execution().modelOffloading}:{})});
      });
      session.preview=job.id;session.submission=null;mergeJob(job);
    }catch(error){session.preview=previous;throw error;}
    finally{session.submission=null;if(session===guideSession)renderGuideVersions();}
  });
  $('#guide-remove').onclick=run(async()=>{const result=await guideWrite(cid,sid,(base,revision)=>api(base+'/'+id,'DELETE',{revision}));mergeSegmentClip(result);localStorage.removeItem(draftKey);$('#dialog').close();renderGuideStrip();await loadSegmentPlan(true);});
  $('#guide-toggle-enabled').onclick=run(()=>setGuideEnabled(cid,sid,id,!guideRecord(session).enabled));
  async function upload(kind,file){
    if(!file)return;
    if(kind==='reference'&&draft.values.references.length>=8)throw Error('Use up to 8 references');
    const normalized=await prepareImageUpload(file);
    const previous=new Set(session.references.map(r=>r.asset));
    const result=await guideWrite(cid,sid,async(base,revision)=>{const form=new FormData();form.append('revision',revision);form.append('kind',kind);form.append('select_reference','false');form.append('file',normalized);const response=await fetch('api/'+base+'/'+id+'/upload',{method:'POST',body:form});const value=await response.json();if(!response.ok)throw Error(value.detail||'Upload failed');return value;});
    mergeSegmentClip(result);
    session.references=(await api(guideBase(cid,sid))).references;
    if(kind==='version')session.preview=guideRecord(session).versions.at(-1)?.id;
    else if(kind==='source')draft.values.edit_source_asset=guideRecord(session).source_uploads.at(-1);
    else draft.values.references.push(...session.references.filter(r=>!previous.has(r.asset)).map(r=>r.asset));
    if(session===guideSession){renderGuideReferences();renderGuideVersions();guideDraftChanged(session);}
  }
  $('#dialog-body').onclick=event=>{
    const image=event.target.closest('[data-guide-image]');
    if(image){void guarded(()=>openGuideImage(image.dataset.guideImage,image.dataset.guideImageLabel))();return;}
    const button=event.target.closest('[data-guide-upload]');if(button&&!session.busy)$('#guide-'+button.dataset.guideUpload+'-upload').click();
  };
  for(const kind of ['source','version','reference'])$('#guide-'+kind+'-upload').onchange=event=>{const file=event.target.files[0];event.target.value='';void run(()=>upload(kind,file))();};
  $('.guide-review').ondragstart=event=>{
    const image=event.target.closest('[data-guide-drag-asset]');if(!image)return;
    event.dataTransfer.setData('application/x-h3-guide-asset',image.dataset.guideDragAsset);event.dataTransfer.effectAllowed='copy';
  };
  for(const [selector,kind] of [['#guide-source-drop-target','source'],['#guide-drop-target','version'],['.guide-references','reference']]){
    const drop=$(selector);
    bindFileDrop(drop,{
      kind:'image',signal:AbortSignal.any([importLifetime.signal,session.dropLifetime.signal]),
      active:active=>drop.classList.toggle('drag-over',active&&!session.busy),
      files:files=>{
        if(session.busy)return;
        if(kind!=='reference'&&files.length>1){toast('Drop one image into this frame.');return;}
        if(kind==='reference'&&draft.values.references.length+files.length>8){toast('Use up to 8 references');return;}
        void run(async()=>{for(const file of files)await upload(kind,file);})();
      },
      invalid:()=>toast('Drop an image file here.'),
    });
    // Internal version drags can become a source without uploading the image again.
    if(kind==='source'){
      drop.ondragover=event=>{if(session.busy||!event.dataTransfer.types.includes('application/x-h3-guide-asset'))return;event.preventDefault();event.dataTransfer.dropEffect='copy';drop.classList.add('drag-over');};
      drop.ondragleave=event=>{if(!drop.contains(event.relatedTarget))drop.classList.remove('drag-over');};
      drop.ondrop=event=>{
        const id=event.dataTransfer.getData('application/x-h3-guide-asset');if(!id)return;
        event.preventDefault();event.stopPropagation();drop.classList.remove('drag-over');if(!session.busy)void guarded(()=>setGuideSource(id))();
      };
    }
  }
  renderGuideReferences();renderGuideVersions();
  try{const status=await api('guide-provider');if(session===guideSession)showGuideProvider(status);}catch{if(session===guideSession)showGuideProvider({});}
}
function renderGuideHelpers(){
  const session=guideSession,helpers=session.draft.values.helpers;
  $$('[data-guide-helper]').forEach(button=>button.setAttribute('aria-pressed',String(helpers.some(h=>h.id===button.dataset.guideHelper))));

}
function showGuideProvider(status){
  guideSession.providerStatus=status;
  const chosen=guideSession.draft.values.edit_model||'gpt-image';
  const models=adapter.mode==='local'&&status.models?status.models:[{id:'gpt-image',label:'GPT Image 2.6 Flare',available:['private-openai','local-openai'].includes(status.provider)&&status.configured===true&&status.available===true,message:status.message}];
  $('#guide-model').innerHTML=models.filter(m=>m.available||m.id==='gpt-image'||m.id===chosen).map(m=>`<option value="${esc(m.id)}" ${m.id===chosen?'selected':''} ${!m.available&&m.id!=='gpt-image'?'disabled':''}>${esc(m.label)}</option>`).join('');
  const model=models.find(m=>m.id===chosen);
  guideSession.providerReady=model?.available===true;
  $('#guide-provider-status').textContent=guideSession.providerReady?'':model?.message||'Generation not connected';
  [...$('#guide-quality').options].forEach((option,i)=>{const q=['Low','Medium','High'][i];option.textContent=chosen==='h3-image'?`${q} · ${[1,2,4][i]} MP`:q;});
  $('#guide-generate').title=guideSession.providerReady?'Generate an edited guide':'Generation unavailable; upload a guide image';
  $('#guide-generate').setAttribute('aria-describedby','guide-provider-status');renderGuideVersions();
}
function renderGuideReferences(){
  const session=guideSession,ids=session.draft.values.references;
  $('#guide-ref-count').textContent=`(${ids.length}/8)`;
  const all=session.references,list=$('#guide-reference-list'),top=list.scrollTop,focused=document.activeElement?.dataset.guideReference;
  list.innerHTML=all.map(r=>`<label class="guide-reference ${ids.includes(r.asset)?'selected':''}"><input type="checkbox" data-guide-reference="${esc(r.asset)}" ${ids.includes(r.asset)?'checked':''}><img src="${imageAsset(r.asset,256)}" alt="" loading="lazy" decoding="async"><span>${esc(r.name)}<small>${ids.includes(r.asset)?'Image '+(ids.indexOf(r.asset)+2)+' · ':''}${esc(r.kind)}</small></span></label>`).join('')||'<p class="note">No references yet. Upload an image to add one.</p>';
  list.scrollTop=top;if(focused)list.querySelector('[data-guide-reference="'+CSS.escape(focused)+'"]')?.focus({preventScroll:true});
  $$('[data-guide-reference]').forEach(input=>input.onchange=()=>{const id=input.dataset.guideReference;session.draft.values.references=input.checked?[...ids,id]:ids.filter(v=>v!==id);if(session.draft.values.references.length>8){session.draft.values.references=ids;toast('Use up to 8 references');}renderGuideReferences();guideDraftChanged(session);});
}
function guideImage(id,label){
  return `<button type="button" class="guide-image-inspect" data-guide-image="${esc(id)}" data-guide-image-label="${esc(label)}" aria-label="Enlarge ${esc(label.toLowerCase())}" title="Enlarge ${esc(label.toLowerCase())}"><img src="${imageAsset(id,960)}" alt="${esc(label)}" decoding="async" draggable="true" data-guide-drag-asset="${esc(id)}"></button>`;
}
function setGuideSource(id){
  const session=guideSession,g=guideRecord(session);
  if(![g.source_asset,...(g.source_uploads||[]),...g.versions.map(v=>v.asset)].includes(id))throw Error('Choose a source image from this guide.');
  session.draft.values.edit_source_asset=id;guideDraftChanged(session);
}
function renderGuideSource(){
  const session=guideSession,id=session.draft.values.edit_source_asset,target=$('#guide-source');
  if(target.dataset.asset!==id){target.innerHTML=guideImage(id,'Source frame');target.dataset.asset=id;}
  $('#guide-source-download').href=asset(id,true);
  $('#guide-reset-source').disabled=session.busy||id===guideRecord(session).source_asset;
  const version=guideRecord(session).versions.find(v=>v.id===session.preview);
  $('#guide-use-source').disabled=session.busy||!version||version.asset===id;
  $$('[data-guide-upload]').forEach(button=>button.disabled=session.busy);
}
async function openGuideImage(id,label){
  const session=guideSession,source=session.draft.values.edit_source_asset,edited=guideRecord(session).versions.find(v=>v.id===session.preview)?.asset;
  const makeWipe=typeof createImageWipe==='function'?createImageWipe:(await import(/* @vite-ignore */ new URL('static/vendor/report-media.js',globalThis.location.href).href)).createImageWipe;
  if(session!==guideSession||!$('#dialog')?.open)return;
  const trigger=document.activeElement,parent=$('#dialog'),viewer=document.createElement('dialog');
  viewer.id='guide-image-viewer';viewer.className='guide-image-viewer';viewer.setAttribute('aria-labelledby','guide-image-viewer-title');
  viewer.innerHTML=`<header><h2 id="guide-image-viewer-title">Guide · frame ${guideRecord(session).frame-shot().start}</h2><label class="guide-image-view-mode">View<select aria-label="Image view"><option value="source">Source</option><option value="edited" ${edited?'':'disabled'}>Edited</option><option value="compare" ${edited?'':'disabled'}>Compare</option></select></label><button type="button" class="h3-icon-button quiet" aria-label="Close image viewer" title="Close image viewer">${uiIcon('close')}</button></header><div class="guide-image-view-content"></div>`;
  const select=viewer.querySelector('select'),content=viewer.querySelector('.guide-image-view-content'),[width,height]=guideRecord(session).dimensions;
  let wipe=null;
  const fit=()=>{const stage=content.querySelector('.ak-report-wipe-stage');if(stage)stage.style.width=Math.max(0,Math.min(content.clientWidth,content.clientHeight*width/height))+'px';};
  const observer=new ResizeObserver(fit);observer.observe(content);
  const render=()=>{
    wipe?.destroy();wipe=null;content.replaceChildren();
    if(select.value==='compare'){
      // Both previews share the guide canvas; contain preserves differing upload ratios.
      wipe=makeWipe(content,{left:{src:imageAsset(source,1920),width,height,label:'Source'},right:{src:imageAsset(edited,1920),width,height,label:'Edited'}});
      fit();wipe.load();content.querySelector('.ak-report-wipe-handle').title='Drag to compare. Left arrow shows Edited; right arrow shows Source.';
    }else content.innerHTML=`<img src="${imageAsset(select.value==='source'?source:edited,1920)}" alt="${select.value==='source'?'Source frame':'Edited guide version'}" decoding="async">`;
  };
  select.value=label==='Source frame'?'source':'edited';select.onchange=render;
  const closeWithParent=()=>viewer.close();
  parent.addEventListener('close',closeWithParent);
  viewer.addEventListener('close',event=>{event.stopPropagation();observer.disconnect();wipe?.destroy();parent.removeEventListener('close',closeWithParent);viewer.remove();if(parent.open)(trigger?.isConnected?trigger:parent.querySelector('[data-guide-image="'+CSS.escape(id)+'"]'))?.focus();},{once:true});
  viewer.addEventListener('cancel',event=>{event.preventDefault();event.stopPropagation();viewer.close();});
  viewer.addEventListener('keydown',event=>{
    if(event.target.closest('select')){if(select.matches(':open')||event.key!=='Escape')return;}
    if(event.key==='Escape'){event.preventDefault();event.stopPropagation();viewer.close();}
    else if(edited&&['ArrowLeft','ArrowRight'].includes(event.key)){
      event.preventDefault();event.stopPropagation();if(select.value!=='compare'){select.value='compare';render();}
      const range=content.querySelector('input[type=range]');range.value=event.key==='ArrowLeft'?'0':'100';range.dispatchEvent(new Event('input',{bubbles:true}));
    }
  },true);
  viewer.addEventListener('click',event=>{if(event.target===viewer)viewer.close();});
  viewer.querySelector('button').onclick=()=>viewer.close();
  document.body.append(viewer);viewer.showModal();render();
}
function renderGuideVersions(){
  const session=guideSession;if(!session||!$('#guide-result'))return;const g=guideRecord(session);if(!g)return;
  if(g.auto_saved_version&&g.auto_saved_version!==session.autoSavedVersion&&g.approved===g.auto_saved_version){
    if(!session.preview||session.preview===session.approved)session.preview=g.approved;
    session.autoSavedVersion=g.auto_saved_version;session.approved=g.approved;
    for(const key of Object.keys(session.draft.base)){
      const saved=key==='helpers'?(g.helpers||[]):key==='edit_source_asset'?(g.edit_source_asset||g.source_asset):g[key];
      if(JSON.stringify(session.draft.values[key])===JSON.stringify(session.draft.base[key]))session.draft.values[key]=structuredClone(saved);
      session.draft.base[key]=structuredClone(saved);
    }
    $('#guide-prompt').value=session.draft.values.prompt;$('#guide-quality').value=session.draft.values.quality;
    renderGuideReferences();renderGuideHelpers();session.versionsKey=null;
  }
  const jobs=S.data.jobs.filter(j=>j.state!=='quoted'&&j.kind==='guide_generate'&&j.clip_id===session.cid&&j.shot_id===session.sid&&j.data.options?.guide?.id===session.id).sort((a,b)=>b.created-a.created);
  const attempts=[...(session.submission?[session.submission]:[]),...jobs.filter(j=>!g.versions.some(v=>v.id===j.id))];
  const pending=j=>['submitting','queued','waiting','running','cancelling','success'].includes(j.state);
  const label=j=>({submitting:'Starting…',queued:'Queued',waiting:'Queued',running:'Generating…',cancelling:'Cancelling…',success:'Finishing…',failed:'Generation failed',interrupted:'Generation interrupted',cancelled:'Generation cancelled'}[j.state]||'Generation failed');
  const collect=j=>['failed','interrupted'].includes(j.state)&&(j.data?.comfy_prompt_id||j.data?.guide_service_job)?`<button data-guide-collect="${esc(j.id)}">Collect existing result</button>`:'';
  const key=JSON.stringify([g.versions,g.approved,g.enabled,session.preview,attempts.map(j=>[j.id,j.state,j.data?.phase,j.data?.error])]);
  if(session.versionsKey!==key){
    session.versionsKey=key;
    const version=g.versions.find(v=>v.id===session.preview),attempt=attempts.find(j=>j.id===session.preview);
    $('#guide-result').setAttribute('aria-busy',String(!!attempt&&pending(attempt)));
    $('#guide-result').innerHTML=version?guideImage(version.asset,'Edited guide version'):attempt?`<div class="guide-pending-preview" role="status">${pending(attempt)?'<span class="guide-spinner" aria-hidden="true"></span>':uiIcon('image')}<strong>${label(attempt)}</strong>${attempt.data?.error?`<p>${esc(attempt.data.error)}</p>`:''}${collect(attempt)}</div>`:'<div class="guide-result-empty"><strong>Your edit appears here</strong><button type="button" data-guide-upload="version">'+uiIcon('upload')+'Upload guide image</button><span>or drop an image here</span></div>';
    $('#guide-result-caption').textContent=version?`Version ${g.versions.indexOf(version)+1}`:attempt?label(attempt):'Edited version';
    const download=$('#guide-result-download');
    if(version)download.href=asset(version.asset,true);else download.removeAttribute('href');
    download.setAttribute('aria-disabled',String(!version));download.tabIndex=version?0:-1;
    const inProgress=attempts.filter(pending).length;
    $('#guide-version-count').textContent=g.versions.length||attempts.length?`(${g.versions.length}${inProgress?` · ${inProgress} pending`:''})`:'';
    const waiting=attempts.map(j=>`<article class="guide-version guide-version-attempt ${j.id===session.preview?'selected':''}" data-guide-attempt="${esc(j.id)}" aria-busy="${pending(j)}"><button type="button" data-guide-version="${esc(j.id)}" aria-pressed="${j.id===session.preview}" title="${label(j)}"><span class="guide-version-placeholder">${pending(j)?'<span class="guide-spinner" aria-hidden="true"></span>':uiIcon('image')}</span><span>${label(j)}</span></button></article>`).join('');
    const completed=g.versions.slice().reverse().map(v=>`<article class="guide-version ${v.id===session.preview?'selected':''}"><button type="button" data-guide-version="${esc(v.id)}" aria-pressed="${v.id===session.preview}" title="${esc(v.prompt||'Uploaded edit')}"><img src="${imageAsset(v.asset,256)}" alt="Version ${g.versions.indexOf(v)+1}" draggable="true" data-guide-drag-asset="${esc(v.asset)}"><span>Version ${g.versions.indexOf(v)+1}</span>${v.id===g.approved&&g.enabled?'<span class="guide-in-use">In use</span>':''}</button><a href="${asset(v.asset,true)}" title="Download version" aria-label="Download version ${g.versions.indexOf(v)+1}">${uiIcon('download')}</a>${v.prompt||v.helpers?.length?`<button class="guide-restore quiet" data-guide-settings="${esc(v.id)}" title="Restore this version's source image, prompt, references and quality">Use settings</button>`:''}</article>`).join('');
    $('#guide-versions').innerHTML=waiting+completed||'<p class="note">No versions yet</p>';
    $$('[data-guide-version]').forEach(button=>button.onclick=()=>{session.preview=button.dataset.guideVersion;renderGuideVersions();guideDraftChanged(session);});
    $$('[data-guide-settings]').forEach(button=>button.onclick=guarded(async()=>{
      const saved=g.versions.find(v=>v.id===button.dataset.guideSettings),available=new Set(session.references.map(r=>r.asset));
      if(saved.references.some(id=>!available.has(id)))throw Error('A saved reference is no longer available.');
      Object.assign(session.draft.values,{...(adapter.mode==='local'?{edit_model:saved.edit_model||'gpt-image'}:{}),prompt:saved.prompt,references:[...saved.references],quality:saved.quality||'medium',helpers:structuredClone(saved.helpers||[]),edit_source_asset:saved.source_asset||g.source_asset});
      $('#guide-prompt').value=session.draft.values.prompt;$('#guide-quality').value=session.draft.values.quality;
      showGuideProvider(session.providerStatus||{});renderGuideReferences();renderGuideHelpers();guideDraftChanged(session);
    }));
    $('#guide-jobs').innerHTML=jobs.filter(j=>['failed','interrupted'].includes(j.state)&&j.id!==session.preview&&!g.versions.some(v=>v.id===j.id)).slice(0,4).map(j=>`<div class="guide-job"><strong>${esc(renderStatus(j))}</strong><p class="note">${esc(j.data.error||j.data.phase||'')}</p>${collect(j)}</div>`).join('');
    $$('[data-guide-collect]').forEach(button=>button.onclick=guarded(async()=>{session.preview=button.dataset.guideCollect;mergeJob(await api('guide-jobs/'+button.dataset.guideCollect+'/collect','POST'));renderGuideVersions();}));
    guideDraftChanged(session);
  }
  const running=attempts.some(pending);$('#guide-generate').disabled=session.busy||running||!guideIsCurrent(g)||!session.providerReady;const labelText=running?'Generating…':'Generate guide';if($('#guide-generate-label').textContent!==labelText)$('#guide-generate-label').textContent=labelText;
  updateGuideGenerationPrice(session);
  const enabled=$('#guide-toggle-enabled');enabled.disabled=session.busy;enabled.setAttribute('aria-checked',String(g.enabled));enabled.title=g.enabled?'Exclude this guide from future renders':'Include this guide in future renders';
}
function syncGuideUI(){renderGuideStrip();if($('#dialog')?.open&&$('#dialog').classList.contains('guide-modal'))renderGuideVersions();}
function bindGuideSegmentMode(){
  $('.render-layout').classList.add('guided-edit-mode','pika-edit-mode');
  $('#segment-anchor').closest('.field').hidden=true;
  $('#render-lora').hidden=false;
  $('#render-use-mask').closest('label').hidden=true;
  $('.generation-model').textContent=segmentModelLabel(segmentSelected());
  renderGuideStrip();
}
