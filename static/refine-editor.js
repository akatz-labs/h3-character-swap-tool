// Optional finishing. All foreground edits remain a local draft until saved.
let refineEditor = null;
const refinePositions = new Map();
function disposeRefineEditor() {
  if (!refineEditor) return;
  const e=refineEditor; clearTimeout(e.seekTimer);clearTimeout(e.prewarmTimer);e.closed=true; e.abort?.abort(); e.frameAbort?.abort(); e.playAbort?.abort(); e.player?.dispose(); e.finishReview?.dispose();
  if(e.objectURL) URL.revokeObjectURL(e.objectURL);
  refinePositions.set(e.key,{frame:e.frame,side:e.side,view:e.view}); refineEditor=null;
}
function updateRefineStatus() { refineEditor?.status?.(); }
function drawRefine(c,s) {
  const key=c.id+':'+s.id, position=refinePositions.get(key)||{};
  const e=refineEditor={key,frame:position.frame||0,side:position.side||'character',view:position.view||'checker',closed:false,revision:c.revision};
  const alive=()=>refineEditor===e&&!e.closed;
  const path=`clips/${c.id}/shots/${s.id}/refine`, max=s.end-s.start-1;
  $('#workspace').innerHTML='<div class="refine-editor"><p class="note">Loading refinement…</p></div>';
  if(s.mode==='preserve') { $('#workspace').innerHTML='<div class="empty">This shot keeps the original footage on both sides of the pair.</div>';return; }
  const fail=err=>{if(alive()){const host=$('#refine-error');if(host){host.textContent=err.message;host.hidden=false}else $('#workspace').textContent=err.message;}};
  const safe=fn=>async(...args)=>{const finish=buttonBusy(args[0]?.currentTarget);try{await fn(...args)}catch(err){fail(err)}finally{finish()}};
  const foregroundFields=['mask_points','face','gate_px','core_px','mask_text','selection','subject_index','alpha_seed','auto_face'];
  const viewNames={rgb:'Original RGB',checker:'Checkerboard',alpha:'Alpha matte',sam:'SAM support',black:'Black',white:'White',plate:'On backplate'};
  function previewKey(r=e.saved) {
    const fields=e.view==='rgb'?[]:e.view==='sam'?['mask','gate_px']:['alpha','mask','gate_px','core_px','face'];
    return JSON.stringify([e.side,e.view,r.binding,Object.fromEntries(fields.map(k=>[k,r[e.side][k]])),e.view==='plate'?r.plate_id:null]);
  }
  function prewarm() {
    clearTimeout(e.prewarmTimer);
    if(viewReason(e.view))return;
    if(S.data.jobs.some(j=>j.clip_id===c.id&&j.shot_id===s.id&&j.kind==='refine_mask'&&j.data.options?.side===e.side&&['queued','waiting','running','cancelling'].includes(j.state))){e.prewarmTimer=setTimeout(()=>{if(alive())prewarm()},1000);return;}
    const key=previewKey();if(e.prewarmRecipe===key)return;e.prewarmRecipe=key;
    api(path+'/playback','POST',{side:e.side,view:e.view}).catch(()=>{if(e.prewarmRecipe===key)e.prewarmRecipe=null});
  }
  function viewReason(view) {
    const f=e.draft[e.side];
    if(e.side==='character'&&!e.saved.binding.render_asset)return 'Select a render first';
    if(!['rgb','sam'].includes(view)&&!f.alpha)return 'Requires AlphaGen extraction';
    if(view!=='rgb'&&!f.mask)return 'Requires a SAM mask';
    if(view==='plate'&&!e.saved.plate_id)return 'Choose a backplate first';
    return '';
  }
  function foregroundState() {
    const f=e.draft[e.side], lastExtraction=S.data.jobs.find(j=>j.clip_id===c.id&&j.shot_id===s.id&&j.kind==='refine_extract'&&j.data.options?.side===e.side);
    const reason=!f.alpha&&lastExtraction?.state==='failed'?(lastExtraction.data.capacity_failure?'AlphaGen failed: GPU memory limit reached. Retry from the failed extraction below.':'AlphaGen extraction failed. See the failed run below.') : !f.alpha?'Run Extract foreground to create the alpha matte.':'';
    $('#refine-matte-state').textContent=f.empty_frames?.length?`${f.empty_frames.length} missing frames · first at ${f.empty_frames[0]}`:f.alpha?'Alpha matte ready':f.mask?'SAM mask ready · alpha matte missing':'Foreground extraction needed';
    $('#refine-view-help').textContent=reason||'Checkerboard and alpha views show the saved foreground matte.';
    for(const option of $('#refine-view').options){const why=viewReason(option.value);option.disabled=!!why;option.textContent=viewNames[option.value]+(why?' · '+why:'');}
    if(viewReason(e.view))e.view='rgb';
    $('#refine-view').value=e.view;
    $('#refine-play').disabled=!!viewReason(e.view);
  }
  const editable=r=>({...Object.fromEntries(['removal_seed','removal_steps','removal_strength'].map(k=>[k,r[k]])),...Object.fromEntries(['source','character'].map(side=>[side,Object.fromEntries(foregroundFields.map(k=>[k,r[side][k]]))]))});
  function dirty() {
    S.dirty=JSON.stringify(editable(e.draft))!==JSON.stringify(editable(e.saved));
    $('#refine-save').disabled=!S.dirty||e.saving;
    $('#refine-padding-apply').disabled=!S.dirty||e.saving;
    $('#refine-padding-default').disabled=!!e.saving;
    $('#refine-save-state').textContent=S.dirty?'Unsaved refinement settings':'Settings saved';
    $('#refine-error').hidden=true;
    $('#refine-frame-note').textContent=S.dirty?'Preview shows saved settings. Save to update the matte.':`Frame ${e.frame} · source frame ${s.start+e.frame} · ${c.dimensions.join(' × ')}`;
    e.finishReview?.update();
  }
  async function save() {
    if(!S.dirty)return;
    e.saving=true;dirty();
    try {
      const changes={};const now=editable(e.draft),was=editable(e.saved);
      for(const k of ['removal_seed','removal_steps','removal_strength'])if(now[k]!==was[k])changes[k]=now[k];
      for(const side of ['source','character']) {const edits={};for(const k of foregroundFields)if(JSON.stringify(now[side][k])!==JSON.stringify(was[side][k]))edits[k]=now[side][k];if(Object.keys(edits).length)changes[side]=edits;}
      const submitted=structuredClone(e.draft),base=structuredClone(e.saved);
      const updated=await api(path,'PATCH',{revision:e.revision,changes,base});
      if(!alive())return;
      const previousPreview=previewKey();
      e.revision=updated.revision;const saved=structuredClone(updated.shots.find(x=>x.id===s.id).refine);
      for(const side of ['source','character'])for(const k of Object.keys(saved[side]))if(JSON.stringify(e.draft[side][k])===JSON.stringify(submitted[side][k]))e.draft[side][k]=structuredClone(saved[side][k]);
      for(const k of Object.keys(saved))if(!['source','character'].includes(k)&&JSON.stringify(e.draft[k])===JSON.stringify(submitted[k]))e.draft[k]=structuredClone(saved[k]);
      e.saved=saved;
      S.data.clips[S.data.clips.findIndex(x=>x.id===c.id)]=updated;S.dirty=false;
      if(previousPreview!==previewKey()){
        pausePlayback(false);e.playAbort?.abort();e.player?.dispose();e.player=null;e.previewRecipe=null;
        prewarm();await frame();
      }
    } finally {if(alive()){e.saving=false;dirty();}}
  }
  async function change(changes) {
    await save(); if(!alive())return;
    await api(path,'PATCH',{revision:e.revision,changes});S.dirty=false;await refresh();
  }
  async function queueStage(kind,options={}) {
    if(e.submitting)return;
    e.submitting={kind,side:options.side,worker:S.worker};e.status();
    try {
      await save();if(!alive())return;
      await api('jobs','POST',{kind,clip_id:c.id,shot_id:s.id,worker:S.worker,options});
    } finally {e.submitting=null;e.status();}

  }
  function polygon(f=e.frame) {
    const keys=e.draft[e.side].face.keyframes;
    if(!keys.length)return [];
    const a=keys.findLast(k=>k.frame<=f)||keys[0],b=keys.find(k=>k.frame>=f)||keys.at(-1),t=a.frame===b.frame?0:(f-a.frame)/(b.frame-a.frame);
    return a.polygon.map((p,i)=>p.map((v,j)=>v*(1-t)+b.polygon[i][j]*t));
  }
  function setPolygon(poly) {
    const face=e.draft[e.side].face;
    face.keyframes=face.keyframes.filter(k=>k.frame!==e.frame).concat([{frame:e.frame,polygon:poly}]).sort((a,b)=>a.frame-b.frame);face.enabled=true;
    $('#refine-face-enabled').checked=true;dirty();overlay();keysUI();
  }
  function keysUI() {
    const face=e.draft[e.side].face;
    const keys=[...new Set([...face.keyframes.map(k=>k.frame),...(e.draft[e.side].face_track?.uncertain_frames||[])])].sort((a,b)=>a-b).map(frame=>({frame}));
    $('#refine-face-keys').innerHTML=keys.map(k=>`<button data-face-frame="${k.frame}" class="${k.frame===e.frame?'primary':'quiet'}" title="${e.draft[e.side].face_track?.uncertain_frames?.includes(k.frame)?'Uncertain tracking · inspect this frame':'Face region'}">${e.draft[e.side].face_track?.uncertain_frames?.includes(k.frame)?'⚠ ':''}${k.frame}</button>`).join('')||'<span class="note">No face region yet</span>';
    document.querySelectorAll('[data-face-frame]').forEach(b=>b.onclick=()=>seek(+b.dataset.faceFrame));
    $('#refine-face-remove').disabled=!face.keyframes.some(k=>k.frame===e.frame);
    $('#refine-face-key').disabled=!face.keyframes.length;
  }
  function overlay() {
    if(!alive())return;
    const f=e.draft[e.side], poly=polygon();
    $('#refine-overlay').innerHTML=(poly.length?`<polygon points="${poly.map(p=>p.map(n=>n*1000).join(',')).join(' ')}" class="face-region"/>${poly.map((p,i)=>`<circle data-vertex="${i}" cx="${p[0]*1000}" cy="${p[1]*1000}" r="12" class="face-vertex"/>`).join('')}`:'')+f.mask_points.filter(p=>p.frame===e.frame).map(p=>`<circle cx="${p.x*1000}" cy="${p.y*1000}" r="10" class="mask-point ${p.label?'include':'exclude'}"/><text x="${p.x*1000}" y="${p.y*1000+5}" class="mask-point-label">${p.label?'+':'−'}</text>`).join('');
  }
  async function frame() {
    if(!alive())return;
    e.exact=true;
    const token=e.frameToken=(e.frameToken||0)+1;
    e.frameAbort?.abort();e.frameAbort=new AbortController();
    e.framePending=true; $('#refine-overlay').style.pointerEvents='none';
    $('#refine-frame-note').textContent='Loading exact frame…';
    try {
      const response=await fetch(`api/${path}/frame?side=${e.side}&frame=${e.frame}&view=${e.view}`,{signal:e.frameAbort.signal});
      if(!response.ok) {const err=await response.json();throw Error(err.detail);}
      const blob=await response.blob();if(!alive()||token!==e.frameToken)return;
      const url=URL.createObjectURL(blob), decoded=new Image();decoded.src=url;
      try {await decoded.decode()} catch(error) {URL.revokeObjectURL(url);throw Error('Frame could not decode')}
      const img=$('#refine-image');
      if(!alive()||token!==e.frameToken){URL.revokeObjectURL(url);return;}
      img.hidden=false;$('#refine-canvas').hidden=true;img.src=url;if(e.objectURL)URL.revokeObjectURL(e.objectURL);e.objectURL=url;
      e.displayedFrame=e.frame;e.framePending=false;$('#refine-overlay').style.pointerEvents='';
      $('#refine-frame-note').textContent=S.dirty?'Save edits to update the matte.':`Frame ${e.frame} · source frame ${s.start+e.frame} · ${c.dimensions.join(' × ')}`;
    } catch(err) {if(alive()&&token===e.frameToken&&err.name!=='AbortError'){e.framePending=false;e.displayedFrame=null;$('#refine-image').hidden=true;$('#refine-canvas').hidden=true;$('#refine-frame-note').textContent=err.message; }}
    if(alive())overlay();
  }
  function pausePlayback(exact=true) {
    clearTimeout(e.seekTimer);e.seekSerial=(e.seekSerial||0)+1;
    e.playIntent=(e.playIntent||0)+1;
    e.exact=exact;e.wanted=false;e.player?.stop();$('#refine-play').textContent='Play';
    if(exact)frame();
  }
  async function playback() {
    const why=viewReason(e.view);if(why)throw Error(why);
    const recipe=previewKey();
    if(e.previewRecipe===recipe&&e.player)return;
    // Many slider events may arrive before the first preview is ready. They
    // share one preparation/load; Atlas coalesces their eventual frame seeks.
    if(e.loadingRecipe===recipe&&e.playbackPromise&&!e.playAbort?.signal.aborted)return e.playbackPromise;
    e.playAbort?.abort();e.player?.dispose();e.player=null;e.previewRecipe=null;
    const controller=e.playAbort=new AbortController(), current=()=>alive()&&!controller.signal.aborted;
    e.loadingRecipe=recipe;
    const pending=(async()=>{
    $('#refine-frame-note').textContent='Preparing playback…';
    let result,retry=true;const deadline=Date.now()+180000;
    do {
      result=await api(path+'/playback','POST',{side:e.side,view:e.view,retry},controller.signal);retry=false;
      if(Date.now()>deadline)throw Error('Playback is still preparing. Try again shortly.');
      if(!current())return;
      if(result.state==='failed')throw Error(result.error);
      if(result.state!=='ready'){
        $('#refine-frame-note').textContent=(result.phase||'Preparing playback')+(result.completed?` · ${result.completed} / ${result.total} frames`:'')+'…';
        await new Promise(resolve=>setTimeout(resolve,250));
      }
    }while(result.state!=='ready');
    const video=$('#refine-video');
    e.previewLoading=true;
    e.player=new H3Player.Atlas(video,$('#refine-canvas'),null,$('#refine-timeline'),()=>24);
    const player=e.player;
    player.onFrame(()=>{
      if(!current()||player.time===null||e.exact||e.previewLoading)return;
      player.render(1,0,0);player.top.hidden=true;e.frame=Math.round(player.time*24);
      e.displayedFrame=e.frame;e.framePending=false;
      $('#refine-frame').value=e.frame;if(!player.scrubbing)$('#refine-timeline').value=e.frame;
      $('#refine-image').hidden=true;$('#refine-canvas').hidden=false;
      $('#refine-frame-note').textContent=`Frame ${e.frame} · source frame ${s.start+e.frame} · playback preview`;
      overlay();
    });
    video.muted=!$('#refine-audio').checked;video.loop=$('#refine-loop').checked;video.playbackRate=+$('#refine-speed').value;
    const loaded=player.event('loadeddata',player.epoch);video.src=asset(result.asset);video.load();await loaded;
    if(!current())return;await player.seek(e.frame,max);if(!current())return;e.previewLoading=false;e.previewRecipe=recipe;player.emit();
    })();
    e.playbackPromise=pending;
    try{return await pending}finally{if(e.playbackPromise===pending){e.playbackPromise=null;e.loadingRecipe=null}}
  }
  async function play() {
    if(e.wanted){pausePlayback();return;}
    const intent=e.playIntent=(e.playIntent||0)+1;
    e.finishReview?.pause();
    e.frameAbort?.abort();e.frameToken=(e.frameToken||0)+1;e.exact=false;e.wanted=true;$('#refine-play').textContent='Pause';
    try {await playback();if(alive()&&e.wanted&&intent===e.playIntent)await e.player.start();}
    catch(error){playbackFailed(error);}
  }
  function playbackFailed(error) {
    if(alive()&&error.name!=='AbortError'){
      pausePlayback(false);e.framePending=false;
      $('#refine-frame-note').textContent='Playback unavailable: '+error.message;
    }
  }
  function seek(n) {
    const next=Math.max(0,Math.min(max,Math.round(Number(n)||0)));
    if(e.displayedFrame===next&&!e.framePending)return;
    pausePlayback(false);e.frameAbort?.abort();e.frameToken=(e.frameToken||0)+1;e.exact=false;e.frame=next;
    $('#refine-frame').value=e.frame;$('#refine-timeline').value=e.frame;overlay();keysUI();e.status();
    clearTimeout(e.seekTimer);
    if(e.native){frame();return;}
    e.framePending=true;const serial=e.seekSerial=(e.seekSerial||0)+1;
    e.seekTimer=setTimeout(async()=>{try{await playback();if(alive()&&serial===e.seekSerial&&e.player){e.exact=false;await e.player.seek(next,max)}}catch(error){if(serial===e.seekSerial)playbackFailed(error)}},40);
  }
  function foregroundUI() {
    pausePlayback(false);e.playAbort?.abort();e.player?.dispose();e.player=null;e.previewRecipe=null;
    const f=e.draft[e.side];
    $('#refine-source-tab').classList.toggle('primary',e.side==='source');$('#refine-character-tab').classList.toggle('primary',e.side==='character');
    foregroundState();
    $('#refine-alpha-seed').value=f.alpha_seed??1234;$('#refine-auto-face').checked=f.auto_face!==false;
    $('#refine-target').value=f.mask_text;$('#refine-subject').value=f.selection;$('#refine-index').value=f.subject_index;
    $('#refine-gate').value=f.gate_px;$('#refine-core').value=f.core_px;
    $('#refine-padding-default').hidden=e.side!=='character';
    $('#refine-padding-default-note').textContent=`New character foreground default: ${e.defaults.character_padding_px} px`;
    $('#refine-padding-default-note').hidden=e.side!=='character';
    $('#refine-face-enabled').checked=f.face.enabled;$('#refine-feather').value=f.face.feather_px;$('#refine-guard').value=f.face.guard_px;
    $('#refine-source-mask').hidden=e.side!=='source';
    const track=f.face_track;
    $('#refine-face-status').textContent=track?.warning|| (track?`${track.method} · ${track.uncertain_frames?.length||0} frames need review`:'Face tracking runs with extraction, or use Track face through shot.');
    $('#refine-history').innerHTML=(s.foreground_versions||[]).filter(v=>v.side===e.side).map(v=>`<option value="${esc(v.job)}">${v.alpha?'Extraction':'Correction'} · ${esc(v.job.slice(-8))}${v.empty_frames?.length?' · missing frames':''}</option>`).join('');
    $('#refine-restore').disabled=!$('#refine-history').value;
    $('#refine-extract').textContent=f.alpha?'Extract again':'Extract foreground';
    keysUI();dirty();frame();e.status();
    prewarm();
  }
  function compare(left,right) {
    const id=right,render=s.renders.find(r=>r.id===id),finish=(s.finishes||[]).find(f=>id.endsWith(':'+f.id)),plate=(s.backplates||[]).find(p=>id==='plate:'+p.id);
    const opened={id,left,asset:render?.asset||finish?.character||plate?.asset};
    dialog('Compare this shot',comparisonHtml(c,s,opened));bindRenderComparison(c,s,opened);
  }
  e.status=()=>{
    if(!alive()||!$('#refine-jobs'))return;
    const liveClip=S.data.clips.find(x=>x.id===c.id),liveShot=liveClip?.shots.find(x=>x.id===s.id);
    if(!e.saving&&liveClip&&liveClip.revision!==e.revision&&liveShot?.refine&&JSON.stringify(liveShot.refine.binding)===JSON.stringify(e.saved.binding)){
      const previous=JSON.stringify(e.saved[e.side]),previousPreview=previewKey();
      const remote=structuredClone(liveShot.refine);
      for(const side of ['source','character']){remote[side].alpha_seed??=1234;remote[side].auto_face??=true;}
      for(const side of ['source','character'])for(const k of Object.keys(remote[side])){
        if(JSON.stringify(e.draft[side][k])===JSON.stringify(e.saved[side][k]))e.draft[side][k]=structuredClone(remote[side][k]);
      }
      for(const k of Object.keys(remote))if(!['source','character','binding'].includes(k)&&JSON.stringify(e.draft[k])===JSON.stringify(e.saved[k]))e.draft[k]=structuredClone(remote[k]);
      e.saved=remote;e.revision=liveClip.revision;dirty();
      if(previous!==JSON.stringify(remote[e.side])){
        foregroundState();
        const tracked=e.draft[e.side].face_track;
        $('#refine-face-status').textContent=tracked?.warning||(tracked?`${tracked.method} · ${tracked.uncertain_frames?.length||0} frames need review`:'Face tracking runs with extraction.');
        $('#refine-face-enabled').checked=e.draft[e.side].face.enabled;
        keysUI();
        if(previousPreview!==previewKey()){
          pausePlayback(false);e.playAbort?.abort();e.player?.dispose();e.player=null;e.previewRecipe=null;
          prewarm();frame();
        }
      }
    }
    const kinds=['refine_plate','refine_extract','refine_mask','refine_face','refine_compose','backplate_anchor','backplate'];
    const jobs=S.data.jobs.filter(j=>j.clip_id===c.id&&j.shot_id===s.id&&kinds.includes(j.kind)).slice(0,30);
    const stateLabel={queued:'Queued',waiting:'Waiting for worker',running:'Processing',success:'Ready',failed:'Failed',cancelled:'Cancelled',cancelling:'Stopping',interrupted:'Needs inspection'};
    const latest=S.data.clips.find(x=>x.id===c.id)?.shots.find(x=>x.id===s.id);
    const versions=latest?.foreground_versions||[];
    foregroundState();
    const card=j=>{
      const side=j.data?.options?.side,seed=(j.data?.shot?.refine?.[side]?.alpha_seed??(j.kind==='refine_extract'?1234:undefined));
      const version=versions.find(v=>v.job===j.id),ready=j.state==='success';
      const frameLabel=j.kind==='refine_mask'&&j.data.options?.mode==='frame'?` · Frame ${j.data.options.frame}`:'';
      const kindLabel={refine_extract:'AlphaGen extraction',refine_mask:'SAM mask correction',refine_face:'Face tracking'}[j.kind]||j.kind.replace('refine_','').replaceAll('_',' ');
      const readyLabel={refine_extract:'Alpha matte ready',refine_mask:'SAM mask ready',refine_face:'Face track ready'}[j.kind]||'Ready';
      return `<article class="refine-run ${esc(j.state)}"><div class="row spread"><strong>${esc(kindLabel)}${frameLabel}${side?' · '+esc(side):''}</strong><span class="badge">${ready?readyLabel:stateLabel[j.state]||esc(j.state)}</span></div>${version?`<img class="refine-run-thumb" loading="lazy" src="api/${path}/runs/${esc(j.id)}/thumbnail" alt="Foreground result preview">`:""}<p class="note">${esc(j.data.worker||'')} · ${esc(new Date(j.created*1000).toLocaleTimeString())}${seed!==undefined?' · Seed '+seed:''}</p><p>${esc(j.data.phase||'')}${j.data.runtime_status?.elapsed?' · '+Math.round(j.data.runtime_status.elapsed)+'s':''}</p>${j.data.result?.attachment==='history_only_newer_frame_edits'?'<p class="note">Newer edits kept; this result is in history.</p>':''}${j.data.error?`<p class="error-text">${j.data.capacity_failure?'GPU memory limit reached':esc(j.data.error.split('\n')[0])}</p><details><summary>Error details</summary><p class="error-text">${esc(j.data.error)}</p></details>`:''}<div class="row">${(['queued','waiting'].includes(j.state)||(j.state==='running'&&gpuJob(j)))?`<button data-refine-cancel="${esc(j.id)}">${j.state==='running'?'Stop run':'Cancel'}</button>`:''}${version?`<button data-refine-use="${esc(j.id)}" data-side="${esc(side)}">Use result</button><button data-refine-inspect="${esc(j.id)}" data-side="${esc(side)}">Compare</button>`:''}${['success','failed','cancelled'].includes(j.state)&&side?`<button data-refine-rerun="${esc(j.id)}">Rerun settings</button>`:''}${j.data.capacity_failure?`<button data-refine-offload="${esc(j.id)}">${j.data.capacity_failure.streaming?'Retry with layer streaming':j.data.capacity_failure.offloading?'Retry with model streaming':'Retry with model offloading'}</button>`:''}</div></article>`;
    };
    const otherJobs=jobs.filter(j=>!['refine_extract','refine_mask','refine_face','refine_compose'].includes(j.kind)).slice(0,5);
    reconcileItems($('#refine-jobs'),otherJobs.length?otherJobs.map(j=>({key:j.id,html:card(j)})):[{key:'empty',html:'<p class="note">No other refinement jobs.</p>'}]);
    const sideJobs=jobs.filter(j=>['refine_extract','refine_mask','refine_face'].includes(j.kind)&&j.data.options?.side===e.side);
    reconcileItems($('#refine-runs'),[...(e.submitting?[{key:'submitting',html:'<article class="refine-run"><strong>Submitting…</strong><p>Saving settings and adding this run to the worker queue.</p></article>'}]:[]),...sideJobs.map(j=>({key:j.id,html:card(j)}))]);
    document.querySelectorAll('[data-refine-cancel]').forEach(b=>b.onclick=safe(async()=>{const j=jobs.find(j=>j.id===b.dataset.refineCancel);if(j.state==='running'&&!confirm('Stop this running job? Its worker runtime will be stopped; completed artifacts are retained.'))return;await api(`jobs/${j.id}/cancel`,'POST',{confirm_running:j.state==='running'});await refresh(false);e.status()}));
    document.querySelectorAll('[data-refine-offload]').forEach(b=>b.onclick=safe(async()=>{const j=jobs.find(j=>j.id===b.dataset.refineOffload);await queueStage(j.kind,{...j.data.options,offload_after:j.id})}));
    document.querySelectorAll('[data-refine-use]').forEach(b=>b.onclick=safe(async()=>{await save();await api(path+'/foreground-version','POST',{revision:e.revision,side:b.dataset.side,job:b.dataset.refineUse});await refresh()}));
    document.querySelectorAll('[data-refine-inspect]').forEach(b=>b.onclick=safe(async()=>{
      pausePlayback(false);const id=b.dataset.refineInspect, title='Foreground run · '+id.slice(-8);
      dialog(title,'<p class="note">Preparing saved run comparison…</p>');
      let result,retry=true;const deadline=Date.now()+180000;
      do {
        result=await api(path+'/runs/'+id+'/playback','POST',{retry});retry=false;
        if(!$('#dialog').open||$('#dialog-title').textContent!==title)return;
        if(result.state==='failed')throw Error(result.error);
        if(result.state!=='ready')await new Promise(resolve=>setTimeout(resolve,500));
        if(Date.now()>deadline)throw Error('Comparison is still preparing. Try again shortly.');
      }while(result.state!=='ready');
      $('#dialog-body').innerHTML=`<p class="note">Original RGB / saved foreground · source audio</p><video controls playsinline class="media-wide" src="${asset(result.asset)}"></video><p class="note">Close to return to your current edits. Use result selects this run.</p>`;
    }));
    document.querySelectorAll('[data-refine-rerun]').forEach(b=>b.onclick=safe(async()=>{const j=jobs.find(j=>j.id===b.dataset.refineRerun),side=j.data.options.side,previous=j.data.shot.refine?.[side];if(previous){for(const k of foregroundFields)if(previous[k]!==undefined)e.draft[side][k]=structuredClone(previous[k]);dirty();}await queueStage(j.kind,{...j.data.options,offload_after:undefined})}));
    const active=new Set(jobs.filter(j=>['queued','waiting','running','cancelling'].includes(j.state)&&(!['refine_extract','refine_mask'].includes(j.kind)||j.data.options?.side===e.side)).map(j=>j.kind));
    const maskJobs=jobs.filter(j=>j.kind==='refine_mask'&&j.data.options?.side===e.side&&['queued','waiting','running','cancelling'].includes(j.state));
    const pointsAt=(points,f)=>JSON.stringify((points||[]).filter(p=>p.frame===f).map(p=>[p.x,p.y,p.label]).sort());
    const sameFrameQueued=maskJobs.some(j=>j.data.options.mode==='frame'&&j.data.options.frame===e.frame&&pointsAt(j.data.shot?.refine?.[e.side]?.mask_points,e.frame)===pointsAt(e.draft[e.side].mask_points,e.frame));
    document.querySelectorAll('[data-refine-kind]').forEach(b=>b.disabled=!!e.submitting||(b.id==='refine-correct-frame'?(sameFrameQueued||maskJobs.some(j=>j.data.options.mode!=='frame')):active.has(b.dataset.refineKind))||(b.dataset.refineKind==='refine_compose'&&(!e.draft.plate_id||!e.saved.binding.render_asset||!['source','character'].every(side=>{const f=e.draft[side];return f.alpha&&f.mask&&!f.empty_frames?.length&&JSON.stringify(f.mask_points)===JSON.stringify(f.mask_applied_points)}))));
    const frameJob=maskJobs.find(j=>j.data.options.mode==='frame'&&j.data.options.frame===e.frame);
    $('#refine-correct-frame').textContent=sameFrameQueued?(frameJob?.state==='running'?'Updating frame…':'Frame queued'):'Update frame';
    $('#refine-mask-queue-note').textContent=maskJobs.length?`${maskJobs.length} frame correction${maskJobs.length===1?'':'s'} pending. Keep editing and queue another frame. Completed frames appear automatically.`:S.data.unified_workers?'Frame updates reuse SAM. Models unload after 5 idle minutes; the worker stops after 15.':'Frame updates reuse SAM; the worker releases 15 seconds after the last correction.';
    e.finishReview?.update();
  };
  safe(async()=>{
    e.abort=new AbortController();const state=await api(path,'GET',undefined,e.abort.signal);if(!alive())return;
    e.revision=state.revision;e.draft=structuredClone(state.draft);e.saved=structuredClone(state.draft);e.defaults=state.defaults;
    const plates=state.backplates.filter(p=>JSON.stringify(p.binding)===JSON.stringify(Object.fromEntries(Object.entries(e.draft.binding).filter(([k])=>!k.startsWith('render_')))));
    const selected=plates.find(p=>p.id===e.draft.plate_id), chosen=(state.finishes||[]).find(f=>f.id===state.selected_finish);
    const stale=chosen&&JSON.stringify(chosen.binding)!==JSON.stringify(e.draft.binding);
    $('#workspace').innerHTML=`<section class="refine-editor">
      <div class="shots-heading"><div><h2>Refine on a shared backplate <span class="note">Optional</span></h2><p class="note">Keep the source performance and rendered character; place both on the same clean background.</p></div><span id="refine-selected-badge" class="badge">${stale?'Selected pair needs review':chosen?'Refined pair selected':'Raw render selected'}</span></div>
      ${!s.selected_render?'<p class="refine-warning">Select a character render in Render before extracting its foreground.</p>':''}
      <section class="panel refine-plate"><div class="panel-title"><h3>1 · Clean backplate</h3><span class="note">Exact shot timeline</span></div><div class="panel-body refine-plate-layout">
        <div>${selected?`<button id="refine-plate-preview" class="refine-plate-thumb"><img src="${asset(selected.asset)}/thumbnail" alt="Selected clean backplate"><span>Compare with source ▶</span></button>`:'<div class="refine-placeholder">Choose or generate an empty background.</div>'}</div>
        <div class="stack"><label>Saved backplate <select id="refine-plate-select"><option value="">Choose a backplate</option>${plates.map(p=>`<option value="${esc(p.id)}" ${p.id===e.draft.plate_id?'selected':''}>${esc(p.origin?.label||p.origin?.name||p.origin?.kind||'Backplate')} · ${p.frames??max+1} frames</option>`).join('')}</select></label>

        <div class="row"><select id="refine-candidate" aria-label="Existing matching backplate"><option value="">Existing matching removals</option>${state.candidates.map(p=>`<option value="${esc(p.id)}">${esc(p.label)}${p.prior_review?' · '+esc(typeof p.prior_review==='string'?p.prior_review:p.prior_review.decision||'Reviewed'):''}</option>`).join('')}</select><button id="refine-import" data-refine-kind="refine_plate">Import</button></div>
        <div class="row"><button id="refine-upload">Upload backplate</button><select id="refine-upload-timeline" aria-label="Uploaded backplate timeline"><option value="shot">This shot only</option><option value="clip">Full source clip</option></select></div><input id="refine-upload-file" type="file" accept="video/*,.mkv" hidden>
        <details><summary>Generate a new backplate</summary><p class="note">Start with a clean opening frame and the source person mask from Render.</p><div class="row">${e.draft.anchor?`<a href="${asset(e.draft.anchor)}" target="_blank"><img class="refine-anchor" src="${imageAsset(e.draft.anchor,640)}" alt="Clean opening anchor" decoding="async" data-still-preview></a>`:''}<button id="refine-anchor-generate" data-refine-kind="backplate_anchor">Generate clean frame</button><button id="refine-anchor-upload">Upload clean frame</button></div><div class="refine-settings"><label>Seed<input id="refine-removal-seed" type="number" min="0" value="${e.draft.removal_seed}"></label><label>Steps<input id="refine-removal-steps" type="number" min="1" max="50" value="${e.draft.removal_steps}"></label><label>Removal strength<input id="refine-removal-strength" type="number" min="0" max="3" step="0.1" value="${e.draft.removal_strength}"></label></div><button id="refine-generate-plate" data-refine-kind="backplate">Generate background video</button></details></div></div></section>
      <section class="panel refine-foreground"><div class="panel-title"><h3>2 · Foregrounds</h3><span id="refine-matte-state" class="note"></span></div><div class="refine-foreground-layout">
        <div class="refine-viewer"><div class="row spread"><div class="row"><button id="refine-source-tab">Source actor</button><button id="refine-character-tab">Rendered character</button></div><label>View <select id="refine-view"><option value="rgb">Original RGB</option><option value="checker">Checkerboard</option><option value="alpha">Alpha matte</option><option value="sam">SAM support</option><option value="black">Black</option><option value="white">White</option><option value="plate">On backplate</option></select></label></div>
        <p id="refine-view-help" class="note" role="status"></p><div id="refine-viewport" tabindex="0" aria-label="Foreground frame. Arrow keys step frames."><div id="refine-stage" style="aspect-ratio:${c.dimensions[0]}/${c.dimensions[1]}"><img id="refine-image" alt="Exact foreground frame"><canvas id="refine-canvas" hidden></canvas><video id="refine-video" playsinline hidden></video><svg id="refine-overlay" viewBox="0 0 1000 1000" preserveAspectRatio="none"></svg></div></div>
        <div class="row refine-transport"><button id="refine-back" aria-label="Previous foreground frame">←</button><button id="refine-next" aria-label="Next foreground frame">→</button><label>Frame <input id="refine-frame" type="number" min="0" max="${max}" value="${e.frame}"></label><span class="note">/ ${max}</span><button id="refine-native" class="quiet">100% pixels</button><button id="refine-play" class="quiet">Play</button><label><input id="refine-loop" type="checkbox" checked>Loop</label><label>Speed<select id="refine-speed"><option value="0.5">0.5×</option><option value="1" selected>1×</option><option value="2">2×</option></select></label><label><input id="refine-audio" type="checkbox">Audio</label></div><input id="refine-timeline" class="range" type="range" min="0" max="${max}" value="${e.frame}" step="1"><p id="refine-frame-note" class="note"></p>
        </div><div class="refine-tools stack">
        <div class="row spread"><strong>Foreground extraction</strong>${workerSelect()}</div><div class="row"><label>Extraction seed<input id="refine-alpha-seed" type="number" min="0" max="9007199254740991"></label><button id="refine-random-seed">Randomize</button></div><button id="refine-extract" data-refine-kind="refine_extract" class="primary">Extract foreground</button><button id="refine-source-mask" class="quiet">Use source generation mask</button><p class="note">AlphaGen uses model offloading by default and supplies the soft matte. SAM keeps the chosen subject and its opaque interior.</p>
        <details><summary>Foreground history</summary><select id="refine-history" aria-label="Foreground version"></select><button id="refine-restore">Restore version</button></details><details><summary>Choose subject</summary><label>Description<input id="refine-target"></label><label>Person<select id="refine-subject"><option value="largest">Largest</option><option value="leftmost">Leftmost</option><option value="rightmost">Rightmost</option><option value="index">By index</option></select></label><label>Index<input id="refine-index" type="number" min="0" max="100"></label></details>
        <div class="refine-padding"><label>SAM padding (px)<input id="refine-gate" type="number" min="0" max="64" step="1"></label><div class="row"><button id="refine-padding-apply">Apply padding</button><button id="refine-padding-default" class="quiet">Use as character default</button></div><p class="note">Extra room outside SAM for AlphaGen’s soft edges. Does not expand the opaque interior. Pixels use the generation resolution. Apply to preview without rerunning SAM or AlphaGen.</p><p id="refine-padding-default-note" class="note"></p></div><label>Edit tool <select id="refine-tool"><option value="inspect">Inspect</option><option value="include">Add foreground</option><option value="exclude">Remove background</option><option value="face">Face region</option></select></label>
        <div class="row"><button id="refine-correct-frame" data-refine-kind="refine_mask">Update frame</button><button id="refine-correct-shot" data-refine-kind="refine_mask">Track shot</button><button id="refine-clear-points" class="quiet">Clear points here</button></div>
        <p id="refine-mask-queue-note" class="note" role="status"></p><div class="refine-face"><strong>Protect mouth and eyes</strong><label><input id="refine-auto-face" type="checkbox" checked>Automatically track face after extraction</label><p class="note">Review the tracked region. To correct it, draw a face box once and track it through the shot.</p><button id="refine-detect-face" data-refine-kind="refine_face">Detect face automatically</button><button id="refine-track-face" data-refine-kind="refine_face">Track face through shot</button><p id="refine-face-status" class="note"></p><label class="check"><input id="refine-face-enabled" type="checkbox">Protect face interior</label><div id="refine-face-keys" class="row"></div><div class="row"><button id="refine-face-key">Keyframe here</button><button id="refine-face-remove" class="quiet">Remove keyframe</button></div></div>
        <details><summary>Advanced edge settings</summary><div class="refine-settings"><label>Opaque core (px)<input id="refine-core" type="number" min="0" max="64"></label><label>Face feather (px)<input id="refine-feather" type="number" min="0" max="64"></label><label>Silhouette guard (px)<input id="refine-guard" type="number" min="0" max="64"></label></div></details>
        <button id="refine-save">Save foreground edits</button><span id="refine-save-state" class="note"></span>
        </div></div><div class="panel-body"><h3>Foreground runs</h3><div id="refine-runs" class="refine-runs" aria-live="polite"></div></div></section>
      ${finishReviewHtml()}
      <p id="refine-error" role="alert" class="error-text" hidden></p><section class="panel"><div class="panel-title">Refinement jobs</div><div id="refine-jobs" class="panel-body"></div></section></section>`;
    $('#refine-save').onclick=safe(save);
    $('#refine-padding-apply').onclick=safe(save);
    $('#refine-padding-default').onclick=safe(async()=>{
      await save();if(!alive())return;
      e.defaults=await api('refine-defaults','PATCH',{revision:e.defaults.revision,character_padding_px:e.draft.character.gate_px});
      $('#refine-padding-default-note').textContent=`New character foreground default: ${e.defaults.character_padding_px} px`;
      toast('Default saved for new character foregrounds');
    });
    $('#refine-plate-select').onchange=safe(ev=>ev.target.value?change({plate_id:ev.target.value}):null);
    if(selected)$('#refine-plate-preview').onclick=()=>compare('source','plate:'+selected.id);
    $('#refine-import').onclick=safe(()=>{const candidate=$('#refine-candidate').value;if(!candidate)throw Error('Choose an existing backplate');return queueStage('refine_plate',{candidate})});
    $('#refine-upload').onclick=()=>$('#refine-upload-file').click();
    $('#refine-upload-file').onchange=safe(async ev=>{if(!ev.target.files[0])return;await save();const form=new FormData();form.append('file',ev.target.files[0]);form.append('revision',e.revision);form.append('timeline',$('#refine-upload-timeline').value);await api(path+'/backplate','POST',form);await refresh()});
    $('#refine-anchor-upload').onclick=safe(async()=>{await save();uploadImage('backplate_anchor')});
    $('#refine-anchor-generate').onclick=safe(()=>queueStage('backplate_anchor'));
    for(const k of ['seed','steps','strength'])$('#refine-removal-'+k).oninput=ev=>{e.draft['removal_'+k]=Number(ev.target.value);dirty()};
    $('#refine-generate-plate').onclick=safe(()=>queueStage('backplate'));
    $('#worker').onchange=ev=>S.worker=ev.target.value;
    $('#refine-source-tab').onclick=()=>{if(e.side!=='source'){e.side='source';foregroundUI()}};$('#refine-character-tab').onclick=()=>{if(e.side!=='character'){e.side='character';foregroundUI()}};
    $('#refine-view').onchange=ev=>{pausePlayback(false);e.playAbort?.abort();e.view=ev.target.value;e.player?.dispose();e.player=null;e.previewRecipe=null;prewarm();frame()};
    $('#refine-frame').onchange=ev=>seek(ev.target.value);$('#refine-timeline').oninput=ev=>seek(ev.target.value);
    $('#refine-back').onclick=()=>seek(e.frame-1);$('#refine-next').onclick=()=>seek(e.frame+1);
    $('#refine-tool').onchange=()=>{if(e.wanted||(e.player&&!e.exact))pausePlayback()};
    e.pause=()=>{pausePlayback(false);e.finishReview?.pause()};
    $('#refine-play').onclick=play;
    $('#refine-video').onended=()=>pausePlayback();
    $('#refine-loop').onchange=ev=>$('#refine-video').loop=ev.target.checked;
    $('#refine-audio').onchange=ev=>$('#refine-video').muted=!ev.target.checked;
    $('#refine-speed').onchange=ev=>$('#refine-video').playbackRate=+ev.target.value;
    $('#refine-alpha-seed').oninput=ev=>{e.draft[e.side].alpha_seed=Number(ev.target.value);dirty()};
    $('#refine-random-seed').onclick=()=>{const seed=crypto.getRandomValues(new Uint32Array(1))[0];e.draft[e.side].alpha_seed=seed;$('#refine-alpha-seed').value=seed;dirty()};
    $('#refine-auto-face').onchange=ev=>{e.draft[e.side].auto_face=ev.target.checked;dirty()};
    $('#refine-detect-face').onclick=safe(()=>queueStage('refine_face',{side:e.side,mode:'auto'}));
    $('#refine-track-face').onclick=safe(()=>queueStage('refine_face',{side:e.side,frame:e.frame}));
    $('#refine-native').onclick=()=>{pausePlayback();const vp=$('#refine-viewport'),stage=$('#refine-stage');e.native=!e.native;vp.classList.toggle('native',e.native);stage.style.width=e.native?c.dimensions[0]+'px':'';stage.style.height=e.native?c.dimensions[1]+'px':'';$('#refine-native').textContent=e.native?'Fit preview':'100% pixels'};
    $('#refine-viewport').onkeydown=ev=>{if(['ArrowLeft','ArrowRight'].includes(ev.key)){ev.preventDefault();seek(e.frame+(ev.key==='ArrowLeft'?-1:1)*(ev.shiftKey?10:1))}};
    $('#refine-restore').onclick=safe(async()=>{await save();await api(path+'/foreground-version','POST',{revision:e.revision,side:e.side,job:$('#refine-history').value});await refresh()});
    $('#refine-source-mask').onclick=safe(async()=>{await save();await api(path+'/source-mask','POST',{revision:e.revision});await refresh()});
    $('#refine-extract').onclick=safe(()=>queueStage('refine_extract',{side:e.side}));
    for(const mode of ['frame','shot'])$('#refine-correct-'+mode).onclick=safe(()=>queueStage('refine_mask',{side:e.side,mode,frame:e.frame}));
    $('#refine-clear-points').onclick=()=>{e.draft[e.side].mask_points=e.draft[e.side].mask_points.filter(p=>p.frame!==e.frame);dirty();overlay();e.status()};
    for(const [id,k] of [['target','mask_text'],['subject','selection'],['index','subject_index'],['gate','gate_px'],['core','core_px']])$('#refine-'+id).oninput=ev=>{e.draft[e.side][k]=['target','subject'].includes(id)?ev.target.value:Number(ev.target.value);dirty()};
    for(const [id,k] of [['feather','feather_px'],['guard','guard_px']])$('#refine-'+id).oninput=ev=>{e.draft[e.side].face[k]=Number(ev.target.value);dirty()};
    $('#refine-face-enabled').onchange=ev=>{if(ev.target.checked&&!e.draft[e.side].face.keyframes.length){ev.target.checked=false;$('#refine-tool').value='face';toast('Drag a face region on the image first');return;}e.draft[e.side].face.enabled=ev.target.checked;dirty();overlay()};
    $('#refine-face-key').onclick=()=>{if(!e.framePending)setPolygon(polygon())};
    $('#refine-face-remove').onclick=()=>{const face=e.draft[e.side].face;face.keyframes=face.keyframes.filter(k=>k.frame!==e.frame);if(!face.keyframes.length){face.enabled=false;$('#refine-face-enabled').checked=false}dirty();overlay();keysUI()};
    const svg=$('#refine-overlay'),xy=ev=>{const b=svg.getBoundingClientRect();return [Math.max(0,Math.min(1,(ev.clientX-b.left)/b.width)),Math.max(0,Math.min(1,(ev.clientY-b.top)/b.height))]};
    svg.onpointerdown=ev=>{if(e.wanted){pausePlayback();return;}if(e.framePending||e.displayedFrame!==e.frame)return;const tool=$('#refine-tool').value;if(tool==='inspect')return;ev.preventDefault();svg.setPointerCapture(ev.pointerId);const p=xy(ev);
      if(tool==='include'||tool==='exclude'){e.draft[e.side].mask_points.push({id:crypto.randomUUID(),frame:e.frame,x:p[0],y:p[1],label:tool==='include'?1:0});dirty();overlay();e.status();return;}
      const vertex=ev.target.dataset.vertex;e.drag={start:p,vertex:vertex===undefined?null:+vertex,poly:polygon()};
    };
    svg.onpointermove=ev=>{if(!e.drag||!svg.hasPointerCapture(ev.pointerId))return;const p=xy(ev),d=e.drag;if(d.vertex!==null){const poly=structuredClone(d.poly);poly[d.vertex]=p;setPolygon(poly);}else {const [x,y]=d.start;if(Math.abs(p[0]-x)>.005&&Math.abs(p[1]-y)>.005)setPolygon([[Math.min(x,p[0]),Math.min(y,p[1])],[Math.max(x,p[0]),Math.min(y,p[1])],[Math.max(x,p[0]),Math.max(y,p[1])],[Math.min(x,p[0]),Math.max(y,p[1])]])}};
    svg.onpointerup=svg.onpointercancel=ev=>{e.drag=null;if(svg.hasPointerCapture(ev.pointerId))svg.releasePointerCapture(ev.pointerId)};
    e.finishReview=bindFinishReview(c,s,e,{save,queueStage,pauseForeground:()=>pausePlayback(false)});
    foregroundUI();e.status();
  })().catch(err=>{if(alive())$('#workspace').textContent=err.message});
}
