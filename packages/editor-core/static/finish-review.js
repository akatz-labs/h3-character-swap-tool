// Shot finishing: preview is local; selecting a version is an explicit write.
const finishPositions = new Map();
function finishReviewHtml() {
  return `<section id="finish-review" class="panel finish-review"><div class="panel-title"><div><h3>3 · Finish this shot</h3><p class="note">Compare the finished pair, then choose the version used in the full clip.</p></div><button id="refine-compose">Create new version</button></div><div class="panel-body">
    <div id="finish-job" class="finish-notice" role="status" hidden></div><p id="finish-create-reason" class="note"></p>
    <div class="row spread finish-toolbar"><label>Preview <select id="finish-version" aria-label="Preview finished version"></select></label><div class="row"><button data-finish-mode="pair" aria-pressed="true">Final pair</button><button data-finish-mode="before" aria-pressed="false">Before / after</button><label id="finish-target-label" hidden>Compare <select id="finish-target"><option value="character">Character</option><option value="source">Source actor</option></select></label></div></div>
    <div id="finish-player-host"></div><p id="finish-settings-note" class="finish-notice" hidden></p>
    <div class="finish-decision row spread"><div><span class="note">Used in final clip</span><strong id="finish-used" role="status"></strong></div><button id="finish-use" class="primary">Use this version for this shot</button></div>
    <p id="finish-action-error" class="error-text" role="alert" hidden></p>
    <details class="finish-details"><summary>Version details &amp; downloads</summary><div id="finish-downloads" class="stack"></div></details>
    <div class="finish-assembly"><div class="row spread"><div><strong id="finish-clip-label">Full clip</strong><p id="finish-assembly-status" class="note" role="status"></p></div><div class="row"><button id="finish-open-review" hidden>View full clip →</button><button id="refine-assemble">Build full clip →</button></div></div><div id="finish-shot-selections" class="row"></div></div>
  </div></section>`;
}
function bindFinishReview(c, s, editor, actions) {
  const host=$('#finish-review'), q=selector=>host.querySelector(selector), key=c.id+':'+s.id;
  const position=finishPositions.get(key)||{};
  const e={preview:position.preview,mode:position.mode||'pair',target:position.target||'character',comparison:null,closed:false,seen:position.seen??null};
  const alive=()=>!e.closed&&host.isConnected;
  const current=()=>{const clip=S.data.clips.find(x=>x.id===c.id)||c;return [clip,clip.shots.find(x=>x.id===s.id)||s]};
  const stable=v=>JSON.stringify(v,(_,x)=>x&&typeof x==='object'&&!Array.isArray(x)?Object.fromEntries(Object.keys(x).sort().map(k=>[k,x[k]])):x);
  const same=(a,b)=>stable(a)===stable(b);
  const binding=(clip,shot)=>({source:clip.render_source||clip.source,start:shot.start,end:shot.end,dimensions:clip.dimensions,render_id:shot.selected_render||null,render_asset:shot.renders.find(r=>r.id===shot.selected_render)?.asset||null});
  const compatible=f=>same(f.binding,binding(...current()));
  const versionName=(shot,id)=>id?'Shared background · Version '+((shot.finishes||[]).findIndex(f=>f.id===id)+1):'Raw original';
  function changes(f) {
    if(!f?.recipe)return [];
    const r=editor.draft,old=f.recipe,out=[];
    if(r.plate_id!==old.plate_id)out.push('Background');
    for(const side of ['source','character']){
      const fields=['alpha','mask','gate_px','core_px','face','mask_points','mask_applied_points'];
      if(fields.some(k=>!same(r[side][k],old[side]?.[k])))out.push(side==='source'?'Source foreground':'Character foreground');
    }
    return out;
  }
  function prerequisites() {
    const r=editor.draft;
    if(!r.binding.render_asset)return 'Select a character render in Render first.';
    if(!r.plate_id)return 'Choose a clean backplate above.';
    for(const side of ['source','character']){
      const f=r[side],label=side==='source'?'source':'character';
      if(!f.alpha||!f.mask)return `Extract the ${label} foreground above.`;
      if(f.empty_frames?.length)return `Correct ${f.empty_frames.length} missing ${label} mask frames.`;
      if(!same(f.mask_points,f.mask_applied_points))return `Apply the ${label} SAM point corrections first.`;
    }
    return '';
  }
  function showError(err) {if(alive()){q('#finish-action-error').hidden=false;q('#finish-action-error').textContent=err.message;}}
  const guarded=fn=>async()=>{q('#finish-action-error').hidden=true;try{await fn()}catch(err){showError(err)}};
  function player(clip,shot) {
    const f=(shot.finishes||[]).find(v=>v.id===e.preview);
    if(!shot.selected_render){e.comparison?.dispose();e.comparison=null;q('#finish-player-host').innerHTML='<div class="empty">Select a character render to preview this shot.</div>';return;}
    let left,right,labels;
    if(e.mode==='pair'){
      left=f?'finish-source:'+f.id:'source';right=f?'finish-character:'+f.id:shot.selected_render;labels=['Source actor','Swapped character'];
    }else{
      left=e.target==='source'?'source':(f?.binding.render_id||shot.selected_render);
      right=f?'finish-'+e.target+':'+f.id:left;labels=['Before · original',f?'After · '+versionName(shot,f.id):'Original · unchanged'];
    }
    const spec=stable([editor.saved.binding,(shot.finishes||[]).map(f=>f.id),shot.selected_render]);
    const pair=stable([left,right,e.mode]);
    if(e.playerSpec!==spec||!e.comparison){
      const frame=e.comparison?.frame??position.frame??0;e.comparison?.dispose();
      const opened={id:right,left,asset:f?.[e.target]||shot.renders.find(r=>r.id===shot.selected_render)?.asset};
      q('#finish-player-host').innerHTML=comparisonHtml(clip,shot,opened,'finish-');
      e.comparison=bindRenderComparison(clip,shot,opened,{root:q('#finish-player-host'),prefix:'finish-',inline:true,frame,mode:e.mode==='pair'?'side':'wipe',labels,onPlay:actions.pauseForeground});
      e.playerSpec=spec;e.playerPair=pair;
    }else if(pair!==e.playerPair){e.comparison.setPair(left,right,e.mode==='pair'?'side':'wipe',labels);e.playerPair=pair;}
  }
  function downloads(shot,f) {
    const k=f?.id||'raw:'+shot.selected_render;
    if(e.downloadKey===k)return;e.downloadKey=k;
    const render=shot.renders.find(r=>r.id===shot.selected_render);
    q('#finish-downloads').innerHTML=f?`<p class="note">${esc(versionName(shot,f.id))} · ${esc(new Date(f.created*1000).toLocaleString())}</p><div class="row"><a href="${asset(f.source,true)}">Source lossless master</a><a href="${asset(f.character,true)}">Character lossless master</a></div><details><summary>Technical details</summary><div class="row"><a href="${asset(f.verification,true)}">Pixel verification</a>${f.provenance?`<a href="${asset(f.provenance,true)}">Provenance &amp; settings</a>`:''}</div></details>`:render?`<p class="note">Original source and selected raw character render.</p><a href="${asset(render.asset,true)}">Character render</a>`:'<p class="note">No render selected.</p>';
  }
  e.update=()=>{
    if(!alive())return;
    const [clip,shot]=current(),finishes=shot.finishes||[],valid=finishes.filter(compatible),newest=valid.at(-1)?.id||'raw';
    if(e.seen!==null&&newest!==e.seen)e.preview=newest;
    if(!e.preview||(e.preview!=='raw'&&!valid.some(f=>f.id===e.preview)))e.preview=newest;
    e.seen=newest;
    const options=stable([finishes.map(f=>[f.id,compatible(f)]),shot.selected_finish]);
    if(e.options!==options){q('#finish-version').innerHTML=[...finishes].reverse().map(f=>`<option value="${esc(f.id)}" ${compatible(f)?'':'disabled'}>${esc(versionName(shot,f.id))}${f.id===shot.selected_finish?' · in use':''}${compatible(f)?'':' · earlier inputs'}</option>`).join('')+`<option value="raw">Raw original${shot.selected_finish?'':' · in use'}</option>`;e.options=options;}
    q('#finish-version').value=e.preview;
    host.querySelectorAll('[data-finish-mode]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.finishMode===e.mode)));
    q('#finish-target-label').hidden=e.mode!=='before';q('#finish-target').value=e.target;
    player(clip,shot);
    const f=finishes.find(f=>f.id===e.preview),changed=changes(f);
    q('#finish-settings-note').hidden=!changed.length;
    q('#finish-settings-note').textContent=`${changed.join(' and ')} changed since this version${S.dirty?' · unsaved edits':''}. Create an updated version to include ${changed.length>1?'them':'it'}.`;
    const chosen=finishes.find(f=>f.id===shot.selected_finish),stale=shot.selected_finish&&(!chosen||!compatible(chosen));
    q('#finish-used').textContent=stale?'Selected version belongs to earlier inputs':versionName(shot,shot.selected_finish);
    if($('#refine-selected-badge'))$('#refine-selected-badge').textContent=stale?'Selection needs updating':shot.selected_finish?'Shared background selected':'Raw original selected';
    const isUsed=(shot.selected_finish||'raw')===e.preview;
    q('#finish-use').disabled=isUsed||!!e.selecting||!shot.selected_render;
    q('#finish-use').textContent=e.selecting?'Saving selection…':isUsed?'Using this version':e.preview==='raw'?'Use raw original for this shot':`Use Version ${finishes.findIndex(f=>f.id===e.preview)+1} for this shot`;
    downloads(shot,f);
    const jobs=S.data.jobs.filter(j=>j.clip_id===clip.id&&j.shot_id===shot.id&&j.kind==='refine_compose'),active=jobs.find(j=>['queued','waiting','running','cancelling'].includes(j.state)),latest=jobs[0],submitting=editor.submitting?.kind==='refine_compose';
    const reason=prerequisites();q('#finish-create-reason').textContent=reason;
    q('#refine-compose').disabled=!!active||!!editor.submitting||!!reason||!!e.creating;
    q('#refine-compose').textContent=submitting?'Submitting…':active?'Creating version…':latest?.state==='failed'?'Retry new version':changed.length?'Create updated version':finishes.length?'Create new version':'Create shared-background version';
    let message='';
    if(submitting)message='Saving settings and submitting a new version…';
    else if(active)message=`${active.state==='running'?'Creating version':active.state==='queued'?'Queued':'Waiting'} · ${active.data.phase||''}${active.data.progress?` · ${active.data.progress.completed}/${active.data.progress.total} frames`:''}. Your selected version stays in use.`;
    else if(latest?.state==='failed'||latest?.state==='interrupted')message=`New version ${latest.state==='failed'?'failed':'needs inspection'}: ${latest.data.error||latest.data.phase}. Your selected version is unchanged.`;
    else if(latest?.state==='success')message='New version ready. Preview it, then choose whether to use it.';
    q('#finish-job').hidden=!message;q('#finish-job').textContent=message;
    const builds=S.data.jobs.filter(j=>j.clip_id===clip.id&&j.kind==='assemble'),build=builds.find(j=>['queued','waiting','running','cancelling'].includes(j.state)),last=builds[0];
    const missing=clip.shots.filter(sh=>sh.mode==='generate'&&(!sh.selected_render||(sh.selected_finish&&!sh.finishes?.some(f=>f.id===sh.selected_finish&&same(f.binding,binding(clip,sh))))));
    q('#finish-clip-label').textContent=`Full clip · ${clip.shots.length} shots`;
    q('#refine-assemble').disabled=!!build||!!e.building||!!missing.length;
    q('#refine-assemble').textContent=e.building?'Submitting…':build?'Building full clip…':clip.result?'Rebuild full clip →':'Build full clip →';
    q('#finish-assembly-status').textContent=e.building?'Submitting selected shot versions…':build?`${build.state==='running'?'Building':'Queued'} · ${build.data.phase||'Waiting'}${build.data.progress?` · ${build.data.progress.completed}/${build.data.progress.total} segments`:''}`:missing.length?`${missing.length} shots need a current result selected.`:last?.state==='failed'?`Build failed: ${last.data.error||'See job details'}`:clip.result?'Full clip ready. Open it to review or download.':'Ready to build from the selected shot versions.';
    q('#finish-open-review').hidden=!clip.result;
    const selectionKey=stable(clip.shots.map(sh=>[sh.id,sh.mode,sh.selected_finish,sh.selected_render]));
    if(e.selectionKey!==selectionKey){q('#finish-shot-selections').innerHTML=clip.shots.map((sh,i)=>`<span class="badge">Shot ${i+1} · ${sh.mode==='preserve'?'Original source':!sh.selected_render?'No result':sh.selected_finish?versionName(sh,sh.selected_finish):'Raw original'}</span>`).join('');e.selectionKey=selectionKey;}
  };
  q('#finish-version').onchange=ev=>{e.preview=ev.target.value;e.update()};
  host.querySelectorAll('[data-finish-mode]').forEach(b=>b.onclick=()=>{e.mode=b.dataset.finishMode;e.update()});
  q('#finish-target').onchange=ev=>{e.target=ev.target.value;e.update()};
  q('#finish-use').onclick=guarded(async()=>{
    const finish=e.preview==='raw'?null:e.preview;e.selecting=true;e.update();
    try{const updated=await api(`clips/${c.id}/shots/${s.id}/refine/select`,'POST',{revision:editor.revision,finish});S.data.clips[S.data.clips.findIndex(x=>x.id===c.id)]=updated;editor.status();}
    finally{e.selecting=false;e.update()}
  });
  q('#refine-compose').onclick=guarded(async()=>{e.creating=true;e.update();try{await actions.queueStage('refine_compose')}finally{e.creating=false;e.update()}});
  q('#refine-assemble').onclick=guarded(async()=>{e.building=true;e.update();try{await actions.save();await api('jobs','POST',{kind:'assemble',clip_id:c.id,worker:S.worker});await refresh(false);editor.status()}finally{e.building=false;e.update()}});
  q('#finish-open-review').onclick=guarded(async()=>{await actions.save();S.tab='review';draw()});
  e.pause=()=>e.comparison?.pause();
  e.dispose=()=>{finishPositions.set(key,{preview:e.preview,mode:e.mode,target:e.target,seen:e.seen,frame:e.comparison?.frame||0});e.closed=true;e.comparison?.dispose()};
  e.update();return e;
}
