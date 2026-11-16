// Shot libraries and segment inputs are separate; media stays in the existing store.
function segmentMaskTracks(s=shot()) {
  const tracks=s?.cast_setup?.mask_tracks||[];
  return s?.segment_masks_version===1?tracks.filter(t=>t.segment_id===segmentSelected()?.id):tracks;
}
function maskTrackKey(s=shot()){return `cast-track:${S.clip}:${s.id}:${segmentSelected()?.id||''}`;}
function maskTrackId(s=shot()) {
  if(!s?.cast_setup)return null;
  const tracks=segmentMaskTracks(s),saved=localStorage.getItem(maskTrackKey(s));
  return tracks.find(t=>t.id===saved)?.id || tracks[0]?.id || null;
}
async function syncSegmentMaskEditor(){
  if(!segmentSelected()||!maskEditState)return;
  if(maskEditState.segmentId===segmentSelected().id&&maskEditState.trackId===maskTrackId()){bindCastMaskSelector();return;}
  const e=maskEditState,frame=e.seeking||!e.ready?e.requested??e.frame:e.frame;
  disposeMaskPlayback();maskEditState=null;
  bindMaskEditor(clip(),shot(),frame);
  bindCastMaskSelector();
}
function maskShot(s=shot(),id=maskTrackId(s)) {
  if(!s?.cast_setup)return s;
  const value={...s};
  for(const key of Object.keys(value))if(key.startsWith('mask_')||['mask','expansion','selection','subject_index'].includes(key))delete value[key];
  return {...value,mask:null,expansion:64,selection:'largest',subject_index:0,mask_points:[],mask_applied_points:[],...s.cast_setup.mask_tracks.find(t=>t.id===id)?.state};
}
function maskPatch(changes,scope='shot',expected=null,target=null) {
  const trackId=target?.trackId??maskEditState?.trackId;
  return patch(changes,trackId?'mask':scope,expected,{clipId:S.clip,shotId:S.shot,...target,trackId});
}
function castPath(){return `clips/${S.clip}/shots/${S.shot}/cast`;}
async function castWrite(path,method='POST',body={}){
  await flushShotAutosave();await saveMaskEdits();
  const base=castPath(),cid=S.clip;
  const task=writeQueue.then(async()=>{
    const c=S.data.clips.find(c=>c.id===cid);
    const result=await api(`${base}/${path}`,method,{revision:c.revision,...body});mergeSegmentClip(result);return result;
  });writeQueue=task.catch(()=>{});
  const result=await task;syncAnchors(clip(),shot());if(maskEditState?.trackId!==maskTrackId()){const frame=maskEditState?.frame??0;disposeMaskPlayback();maskEditState=null;bindMaskEditor(clip(),shot());maskSeek(frame);}bindCastMaskSelector();await loadSegmentPlan(true);return result;
}
function castImage(id,alt){return id?`<img src="${imageAsset(id,640)}" alt="${esc(alt)}" loading="lazy">`:'<span class="note">Upload a reference image</span>';}
async function manageProjectCharacters(create=false){
  if(create)sessionStorage.setItem(`h3-create-character:${S.batch}`,'1');
  globalThis.location.hash=`#/project/${encodeURIComponent(S.batch)}/assets`;
}
async function castPicker(characterId=null){
  const project=S.batch,cid=S.clip,sid=S.shot,segmentId=segmentSelected().id,selected=new Set(segmentSelected().cast_inputs.characters),existing=new Set(shot().cast_setup.characters.filter(c=>selected.has(c.id)).map(c=>c.project_character_id));
  const current=characterId?shot().cast_setup.characters.find(c=>c.id===characterId):null;
  if(characterId&&!current)throw Error('Character is no longer in this shot.');
  const characters=await api(`batches/${project}/characters`);
  if(S.clip!==cid||S.shot!==sid||segmentSelected()?.id!==segmentId)return;
  const chosen=new Set(),title=current?'Change character':'Add characters';
  dialog(title,`<div class="cast-picker"><p class="note">${current?`Replace ${esc(current.name)} in this segment. Its segment masks stay unchanged.`:`Choose from ${esc(S.data.batches.find(b=>b.id===project)?.name||'this project')}.`}</p><div class="cast-picker-toolbar"><input id="cast-picker-search" type="search" aria-label="Search project characters" placeholder="Search project characters"><button id="cast-picker-new">${uiIcon('plus')}New character</button></div><div class="cast-picker-grid" id="cast-picker-grid"></div><p id="cast-picker-error" role="alert" hidden></p><div class="cast-picker-footer"><button id="cast-picker-manage">Manage project characters ${uiIcon('arrow')}</button><div class="row"><span id="cast-picker-count" class="note">0 selected</span><button id="cast-picker-cancel">Cancel</button><button id="cast-picker-add" class="primary" disabled>${title}</button></div></div></div>`);
  const refresh=()=>{
    const query=$('#cast-picker-search').value.toLowerCase();
    const items=characters.filter(c=>(c.name+' '+c.description).toLowerCase().includes(query));
    $('#cast-picker-grid').innerHTML=items.map(c=>{
      const disabled=existing.has(c.id);
      return `<label class="cast-picker-card" data-existing="${disabled}">${c.reference?`<img src="${imageAsset(c.reference,640)}" alt="${esc(c.name)}">`:'<div class="cast-visual"><span class="note">Reference needed</span></div>'}<strong>${esc(c.name)}</strong><p>${esc(c.description)}</p><span><input type="${current?'radio':'checkbox'}" name="cast-picker-choice" value="${esc(c.id)}" ${disabled?'disabled':chosen.has(c.id)?'checked':''}>${disabled?current&&c.id===current.project_character_id?'Current character':'Already in segment':current?'Use this character':'Add to this segment'}</span></label>`;
    }).join('')|| (characters.length?'<p class="note">No matching characters.</p>':'<div class="cast-picker-empty"><h2>No project characters yet</h2><p>Create one in Assets to add it to this segment.</p><button id="cast-picker-create" class="primary">Create character</button></div>');
    $$('#cast-picker-grid input:not(:disabled)').forEach(input=>input.onchange=()=>{if(current)chosen.clear();if(input.checked)chosen.add(input.value);else chosen.delete(input.value);count();});
  };
  const count=()=>{const n=chosen.size;$('#cast-picker-count').textContent=`${n} selected`;$('#cast-picker-add').textContent=current?'Change character':n?`Add ${n} character${n===1?'':'s'}`:'Add characters';$('#cast-picker-add').disabled=!n;};
  $('#cast-picker-search').oninput=refresh;
  $('#cast-picker-new').onclick=guarded(()=>manageProjectCharacters(true));
  $('#cast-picker-cancel').onclick=()=>$('#dialog').close();
  $('#cast-picker-manage').onclick=guarded(()=>manageProjectCharacters());
  $('#cast-picker-add').onclick=guarded(async()=>{const button=$('#cast-picker-add');button.disabled=true;try{
    if(S.clip!==cid||S.shot!==sid||segmentSelected()?.id!==segmentId)throw Error('Open this segment again before changing its characters.');
    if(current)await castWrite(`characters/${characterId}`,'PATCH',{project_character_id:[...chosen][0],segment_id:segmentId});
    else await castWrite('characters','POST',{character_ids:[...chosen],create_mask:false,segment_id:segmentId});
    $('#dialog').close();
  }catch(error){const status=$('#cast-picker-error');status.hidden=false;status.textContent=error.message;}finally{if(button.isConnected)button.disabled=false;}});
  refresh();if($('#cast-picker-create'))$('#cast-picker-create').onclick=guarded(()=>manageProjectCharacters(true));$('#cast-picker-search').hidden=!characters.length;if(characters.length)$('#cast-picker-search').focus();
}
function closeCastCharacterMenu(focus=false){
  const menu=$('#cast-character-menu');if(menu)menu.hidden=true;
  $$('[data-cast-options][aria-expanded="true"]').forEach(button=>{button.setAttribute('aria-expanded','false');if(focus)button.focus();});
}
function removeShotCharacter(id){
  const c=shot().cast_setup.characters.find(c=>c.id===id);
  dialog('Remove character from shot',`<p>Remove ${esc(c.name)} and its mask assignments from this shot?</p><p class="note">The character stays in Project Assets. Existing takes are kept.</p><div class="row"><button id="cast-remove-cancel">Cancel</button><button id="cast-remove-confirm" class="danger">Remove from shot</button></div>`);
  $('#cast-remove-cancel').onclick=()=>$('#dialog').close();
  $('#cast-remove-confirm').onclick=guarded(async()=>{const button=$('#cast-remove-confirm');button.disabled=true;try{await castWrite(`characters/${id}`,'DELETE');$('#dialog').close();}finally{if(button.isConnected)button.disabled=false;}});
}
function openCastCharacterMenu(button){
  const expanded=button.getAttribute('aria-expanded')==='true';closeCastCharacterMenu();if(expanded)return;
  const menu=$('#cast-character-menu'),id=button.dataset.castOptions;
  menu.innerHTML=`<button role="menuitem" data-cast-action="change">${uiIcon('swap')}Change character…</button><button role="menuitem" data-cast-action="duplicate">${uiIcon('layers')}Duplicate character</button><button role="menuitem" data-cast-action="remove">${uiIcon('trash')}Remove from shot…</button>`;
  menu.hidden=false;button.setAttribute('aria-expanded','true');
  const rect=button.getBoundingClientRect();menu.style.left=`${Math.max(8,Math.min(rect.right-menu.offsetWidth,innerWidth-menu.offsetWidth-8))}px`;menu.style.top=`${Math.max(8,Math.min(rect.bottom+6,innerHeight-menu.offsetHeight-8))}px`;
  menu.querySelectorAll('[data-cast-action]').forEach(action=>action.onclick=guarded(async()=>{
    closeCastCharacterMenu(true);
    if(action.dataset.castAction==='change')await castPicker(id);
    else if(action.dataset.castAction==='remove')removeShotCharacter(id);
    else {await castWrite(`characters/${id}/duplicate`);toast('Character duplicated with an independent copy of its mask.');}
  }));
  menu.querySelector('button').focus();
}
document.addEventListener('pointerdown',event=>{if(!event.target.closest('#cast-character-menu,[data-cast-options]'))closeCastCharacterMenu();});
document.addEventListener('keydown',event=>{
  const menu=$('#cast-character-menu');if(!menu||menu.hidden)return;
  if(event.key==='Escape'){event.preventDefault();closeCastCharacterMenu(true);}
  else if(event.key==='Tab')closeCastCharacterMenu();
  else if(['ArrowDown','ArrowUp','Home','End'].includes(event.key)){
    event.preventDefault();const items=[...menu.querySelectorAll('button')],i=items.indexOf(document.activeElement);
    items[event.key==='Home'?0:event.key==='End'?items.length-1:(i+(event.key==='ArrowDown'?1:items.length-1))%items.length].focus();
  }
});
window.addEventListener('resize',()=>closeCastCharacterMenu());
document.addEventListener('scroll',()=>closeCastCharacterMenu(),true);
function syncCastSetup(c,s){ bindCastMaskSelector(); syncAnchorGeneration(); }
function castEdit(id){
  const entry=shot().cast_setup.scene_anchors.find(v=>v.id===id);
  dialog('Scene anchor details',`<div class="dialog-form"><label for="cast-name">Name</label><input id="cast-name" maxlength="120" value="${esc(entry.name)}"><label for="cast-description">Editing prompt</label><textarea id="cast-description" rows="7">${esc(entry.provenance?.prompt||'')}</textarea><button id="cast-save" class="primary">Save</button></div>`);
  $('#cast-save').onclick=guarded(async()=>{await castWrite(`anchors/${id}`,'PATCH',{changes:{name:$('#cast-name').value,prompt:$('#cast-description').value}});$('#dialog').close();});
}
function castUpload(id='',captureId=''){
  const cid=S.clip,sid=S.shot,seg=segmentSelected(),segmentId=seg?.id;
  const entry=shot().cast_setup.scene_anchors.find(v=>v.id===id);
  const capture=shot().anchor_captures?.find(v=>v.id===captureId);
  const sourceFrame=capture?.source_frame??entry?.source_frame??null;
  const index=segmentEditor.plan.segments.findIndex(v=>v.id===segmentId)+1;
  dialog('Upload edited scene anchor',`<div class="dialog-form">${capture?`<img class="capture-dialog-image" src="${imageAsset(capture.asset,640)}" alt="Original captured frame"><p class="note">Original capture · ${segmentTime(capture.source_frame)} · frame ${capture.source_frame}</p>`:''}<label for="cast-source-index">Source frame used for this edit · optional</label><input id="cast-source-index" type="number" min="0" max="${shot().end-shot().start-1}" value="${sourceFrame??''}" ${capture?'disabled':''}><p class="note">Records where the image came from; it does not pin it to an output frame.${id?' Replacing this anchor updates every segment that uses it.':''}</p><label class="check"><input id="cast-upload-assign" type="checkbox" checked>Add to Segment ${index} · ${segmentTime(seg.start)}–${segmentTime(seg.end)}</label><label for="cast-image-file">Edited image</label><input id="cast-image-file" type="file" accept="image/*"><p id="cast-upload-status" role="status"></p><button id="cast-upload-save" class="primary">Upload edited image</button></div>`);
  $('#cast-upload-save').onclick=guarded(async()=>{
    const file=$('#cast-image-file').files[0];if(!file)throw Error('Choose an image first.');
    const field=$('#cast-source-index');if(!field.checkValidity())throw Error('Choose a source frame inside this shot.');
    const frame=field.value===''?null:Number(field.value),assign=$('#cast-upload-assign').checked;
    const button=$('#cast-upload-save');button.disabled=true;$('#cast-upload-status').textContent='Uploading…';
    try{
      await flushShotAutosave();await saveMaskEdits();
      const task=writeQueue.then(async()=>{
        const form=new FormData();form.set('revision',S.data.clips.find(c=>c.id===cid).revision);form.set('kind','anchor');form.set('entry_id',id);form.set('file',file);
        if(frame!==null)form.set('source_frame',frame);if(captureId)form.set('capture_id',captureId);if(assign)form.set('segment_id',segmentId);
        const response=await fetch(`api/clips/${cid}/shots/${sid}/cast/images`,{method:'POST',body:form});const result=await response.json();if(!response.ok)throw Error(result.message||result.detail||'Upload failed');mergeSegmentClip(result);
      });writeQueue=task.catch(()=>{});await task;$('#dialog').close();syncAnchors(clip(),shot());await loadSegmentPlan(true);
    }catch(error){$('#cast-upload-status').textContent=error.message;throw error;}
    finally{if(button.isConnected)button.disabled=false;}
  });
}
async function captureShotFrame(){
  const e=maskEditState;if(!e?.ready||e.viewing==='anchor')throw Error('Choose a source video frame first.');
  maskPause();const frame=e.frame,cid=S.clip,sid=S.shot,segmentId=segmentSelected()?.id,button=$('#shot-capture');
  button.disabled=true;
  try{
    await flushShotAutosave();
    const task=writeQueue.then(()=>api(`clips/${cid}/shots/${sid}/cast/captures`,'POST',{revision:S.data.clips.find(c=>c.id===cid).revision,frame,segment_id:segmentId}));
    writeQueue=task.catch(()=>{});const result=await task;mergeSegmentClip(result.clip);
    const link=document.createElement('a');link.href=asset(result.capture.asset,true);link.download=`${sid}-frame-${frame}.png`;document.body.append(link);link.click();link.remove();
    await loadSegmentPlan(true);toast('Frame captured. Upload the edited image in this segment’s Anchors.');
  }finally{if(button.isConnected)button.disabled=false;}
}
const MASK_COLORS=['#83d9c4','#db8bce','#e9ac68','#79b8ef','#d8ce73','#b3a0ef'];
function maskLabel(track,index){return track.name||`Mask ${index+1}`;}
function selectedMaskColor(){const tracks=segmentMaskTracks(),index=tracks.findIndex(t=>t.id===maskTrackId());return tracks[index]?.color||MASK_COLORS[Math.max(0,index)%MASK_COLORS.length];}
async function selectMaskTrack(id){
  await flushShotAutosave();await saveMaskEdits();const frame=maskEditState?.frame??0;
  disposeMaskPlayback();maskEditState=null;localStorage.setItem(maskTrackKey(),id);
  bindMaskEditor(clip(),shot());await maskSeek(frame);bindCastMaskSelector();
}
function bindCastMaskSelector(){
  const row=$('#cast-mask-selector');if(!row||!shot().cast_setup)return;
  const tracks=segmentMaskTracks(),current=maskTrackId();
  const key=JSON.stringify([tracks.map(t=>[t.id,t.name,t.color]),current]);if(row.dataset.key===key)return;row.dataset.key=key;
  row.innerHTML=`<span class="mask-color-dot" style="background:${selectedMaskColor()}" aria-hidden="true"></span><select id="mask-track-select" aria-label="Active mask"><button type="button"><selectedcontent></selectedcontent></button>${tracks.length?'':'<option value="">Mask 1 · add points</option>'}${tracks.map((t,i)=>`<option value="${esc(t.id)}" ${t.id===current?'selected':''}>${esc(maskLabel(t,i))}</option>`).join('')}<option value="new">＋ Add new mask</option></select><button id="mask-edit" class="h3-icon-button quiet" popovertarget="mask-settings-popover" aria-label="Edit mask" title="Edit mask" ${current?'':'disabled'}>${uiIcon('edit')}</button><button id="mask-remove" class="h3-icon-button quiet" aria-label="Delete mask" title="Delete mask" ${current?'':'disabled'}>${uiIcon('trash')}</button>`;
  $('#mask-track-select').onchange=guarded(async event=>{
    const value=event.target.value;
    if(value==='new'){const c=await castWrite('masks','POST',{segment_id:segmentSelected().id});await selectMaskTrack(c.shots.find(s=>s.id===S.shot).cast_setup.mask_tracks.at(-1).id);}
    else await selectMaskTrack(value);
  });
  const form=$('#mask-name-form'),field=$('#mask-name'),save=$('#mask-name-save');
  const track=tracks.find(t=>t.id===current),savedName=track?maskLabel(track,tracks.indexOf(track)):'';
  if(field.dataset.trackId!==(current||'')||field.value===field.dataset.savedName)field.value=savedName;
  field.dataset.trackId=current||'';field.dataset.savedName=savedName;
  field.disabled=!current;
  field.oninput=()=>{save.disabled=!current||form.dataset.saving==='true'||!field.value.trim()||field.value.trim()===field.dataset.savedName;};
  field.oninput();
  form.onsubmit=guarded(async event=>{
    event.preventDefault();if(save.disabled)return;
    const cid=S.clip,sid=S.shot,trackId=current,name=field.value.trim(),before=field.dataset.savedName;
    form.dataset.saving='true';save.disabled=true;save.textContent='Saving…';
    try {
      const task=writeQueue.then(async()=>{
        const c=S.data.clips.find(c=>c.id===cid),s=c.shots.find(s=>s.id===sid),tracks=s.cast_setup.mask_tracks;
        const track=tracks.find(t=>t.id===trackId);
        if(!track||maskLabel(track,tracks.indexOf(track))!==before)throw Error('Mask name changed elsewhere. Reopen Edit mask before saving.');
        return api(`clips/${cid}/shots/${sid}/cast/masks/${trackId}`,'PATCH',{revision:c.revision,name});
      });
      writeQueue=task.catch(()=>{});
      mergeSegmentClip(await task);bindCastMaskSelector();await loadSegmentPlan(true);
    }finally{form.dataset.saving='false';save.textContent='Save';field.oninput();}
  });
  $('#mask-remove').onclick=()=>{
    dialog('Delete mask',`<p>Remove this mask from this segment? Existing takes are kept.</p><div class="row"><button id="mask-delete-cancel">Cancel</button><button id="mask-delete-confirm" class="danger">Delete mask</button></div>`);
    $('#mask-delete-cancel').onclick=()=>$('#dialog').close();$('#mask-delete-confirm').onclick=guarded(async()=>{await flushShotAutosave();const e=maskEditState;if(e?.pointSave)await e.pointSave.catch(()=>{});if(e){clearTimeout(e.pointsTimer);e.points=maskCopy(e.basePoints);e.padding=e.basePadding;localStorage.removeItem(e.storageKey);e.dirty=false;}await castWrite('masks/'+current,'DELETE');$('#dialog').close();});
  };
}
function segmentCastHtml(seg,state){
  if(!shot().cast_setup)return '';
  const lib=shot().cast_setup,chosen=seg.cast_inputs,assignments=characterMasks(chosen),tracks=segmentMaskTracks();
  return `<section class="segment-inputs"><div class="segment-cast-section"><div class="row spread"><strong>Characters</strong><button id="segment-add-character" class="quiet">${uiIcon('plus')}Add</button></div><div class="segment-character-list" aria-label="Character references" tabindex="0">${chosen.characters.map(id=>lib.characters.find(c=>c.id===id)).map(c=>{return `<div class="segment-character-row"><div class="segment-character-reference">${c.reference?`<img src="${imageAsset(c.reference,256)}" alt="">`:''}<span>${esc(c.name)}</span></div><button class="h3-icon-button quiet" data-character-change="${esc(c.id)}" aria-label="Change ${esc(c.name)}" title="Change character">${uiIcon('swap')}</button><button class="h3-icon-button quiet" data-character-remove="${esc(c.id)}" aria-label="Remove ${esc(c.name)} from segment" title="Remove character">${uiIcon('trash')}</button><label class="character-mask-field"><span>Mask</span><select data-character-mask="${esc(c.id)}" aria-label="Mask for ${esc(c.name)}" title="Selected masks combine into one edit region; they do not target individual characters."><option value="">No mask</option>${(assignments[c.id]||[]).length>1?`<option value="__retained__" selected>Saved region (${assignments[c.id].length} masks)</option>`:''}${tracks.map((t,i)=>`<option value="${esc(t.id)}" ${(assignments[c.id]||[]).length===1&&assignments[c.id][0]===t.id?'selected':''}>${esc(maskLabel(t,i))}${t.state.mask?'':' · Not tracked'}</option>`).join('')}</select></label></div>`;}).join('')||'<p class="note">No characters assigned to this segment.</p>'}</div></div>
    ${state.prompt_needs_update?'<div class="cast-review" role="status">References changed. Review the prompt.<div class="row"><button id="cast-review-prompt" class="quiet">Review prompt</button><button id="cast-confirm-prompt" class="quiet">Keep current prompt</button></div></div>':''}
</section>`;
}
async function setSegmentCastInputs(value,maskChange=null){
  const seg=segmentSelected(),previous=structuredClone(seg.cast_inputs),id=seg.id,path=segmentPath(),cid=S.clip,sid=S.shot;await flushShotAutosave();
  const task=writeQueue.then(async()=>{const c=S.data.clips.find(v=>v.id===cid),current=c.shots.find(v=>v.id===sid).segments.find(v=>v.id===id);if(maskChange){
      // Apply only this character's choice after earlier point/selection writes.
      value=structuredClone(current.cast_inputs);
      if(!value.characters.includes(maskChange.characterId))throw Error('This character is no longer in the segment.');
      const assignments=characterMasks(value);
      assignments[maskChange.characterId]=maskChange.id?[maskChange.id]:[];
      applyCharacterMasks(value,assignments);
    }else if(!sameSettings(current?.cast_inputs,previous))throw Error('Segment inputs changed elsewhere; refresh before editing.');
    mergeSegmentClip(await api(`${path}/${id}`,'PATCH',{revision:c.revision,changes:{cast_inputs:value,use_mask:value.mask_tracks.length>0}}));});writeQueue=task.catch(()=>{});await task;await loadSegmentPlan(true);
}
function bindSegmentCast(){
  $('#segment-add-character').onclick=guarded(()=>castPicker());
  $$('[data-character-change]').forEach(b=>b.onclick=guarded(()=>castPicker(b.dataset.characterChange)));
  $$('[data-character-remove]').forEach(b=>b.onclick=guarded(async()=>{
    const id=b.dataset.characterRemove,value=structuredClone(segmentSelected().cast_inputs);
    const assignments=characterMasks(value);delete assignments[id];
    value.characters=value.characters.filter(key=>key!==id);
    applyCharacterMasks(value,assignments);
    await setSegmentCastInputs(value);
  }));
  $$('[data-character-mask]').forEach(field=>field.onchange=guarded(async()=>{
    if(field.value==='__retained__')return;
    await setSegmentCastInputs(null,{characterId:field.dataset.characterMask,id:field.value});
  }));
  syncAnchorGeneration();
  $$('[data-saved-anchor]').forEach(field=>field.onchange=guarded(async()=>{const value=structuredClone(segmentSelected().cast_inputs);value.scene_anchors=$$('[data-saved-anchor]:checked').map(v=>v.dataset.savedAnchor);await setSegmentCastInputs(value);}));
  $$('[data-anchor-cast-upload]').forEach(b=>b.onclick=()=>castUpload(b.dataset.anchorCastUpload));
  $$('[data-anchor-cast-edit]').forEach(b=>b.onclick=()=>castEdit(b.dataset.anchorCastEdit));
  $$('[data-capture-upload]').forEach(b=>b.onclick=()=>castUpload('',b.dataset.captureUpload));
  if($('#anchor-conditioning'))$('#anchor-conditioning').onchange=guarded(async()=>{const value=$('#anchor-conditioning').value;await flushShotAutosave();await patch({anchor_conditioning:value});await loadSegmentPlan(true);});
  const review=$('#cast-review-prompt');if(review)review.onclick=()=>{const toggle=$('#segment-performance-toggle');if(toggle.getAttribute('aria-expanded')!=='true')toggle.click();$('#segment-prompt').focus();};
  const confirm=$('#cast-confirm-prompt');if(confirm)confirm.onclick=guarded(async()=>{const id=segmentSelected().id,text=$('#segment-prompt').value;await flushShotAutosave();mergeSegmentClip(await api(`${segmentPath()}/${id}`,'PATCH',{revision:clip().revision,changes:{performance_prompt:text}}));await loadSegmentPlan(true);});
}

let anchorSubmission = null, anchorComparison = null, anchorDialogVersion = 0;
function disposeAnchorComparison(){anchorComparison?.destroy();anchorComparison=null;}
function anchorJobs(){return (S.data?.jobs||[]).filter(j=>j.kind==='anchor_generate'&&j.clip_id===S.clip&&j.shot_id===S.shot).sort((a,b)=>b.created-a.created);}
function anchorCard(a,job=null){
  const candidate=job?.data.result?.anchor_candidate,entry=a||shot().cast_setup.scene_anchors.find(v=>v.provenance?.job_id===job.id);
  const used=!!entry&&segmentSelected().cast_inputs.scene_anchors.includes(entry.id),key=job?`job:${job.id}`:`anchor:${a.id}`;
  const shown=a||entry,raw=!shown&&!candidate;
  const name=shown?.name||candidate?.character_name||shot().cast_setup.characters.find(v=>v.id===job.data.options.character_id)?.name||'Generated anchor';
  const frame=shown?.source_frame??candidate?.frame??job?.data.options.frame,image=shown?.asset||candidate?.asset||job.data.result.asset;
  const finishing=raw&&['queued','waiting','running','cancelling'].includes(job.state);
  return `<article class="segment-anchor-card anchor-result-card" data-anchor-card="${esc(key)}"><img src="${imageAsset(image,256)}" alt="${esc(name)}"><div><strong>${esc(name)}</strong><small>${frame==null?'Source frame unknown':`Frame ${frame}`}</small>${raw?`<small role="status">Image generated · ${finishing?'finishing…':job.state==='failed'?'finishing failed':'finishing incomplete'}</small>`:''}<div class="row"><button class="quiet" data-anchor-preview="${esc(key)}">Preview</button>${raw?`<a href="${asset(image,true)}">Download image</a>`:`<button class="anchor-use-toggle ${used?'active':''}" role="switch" aria-checked="${used}" data-anchor-toggle="${esc(key)}"><span class="anchor-toggle-track" aria-hidden="true"></span>Use anchor</button>`}</div></div><button class="h3-icon-button quiet anchor-trash" data-anchor-delete="${esc(key)}" aria-label="Delete ${esc(name)}" title="Delete anchor" ${finishing?'disabled':''}>${uiIcon('trash')}</button></article>`;
}
function syncAnchorGeneration(){
  const host=$('#anchor-generation');if(!host||!segmentEditor?.plan)return;
  const seg=segmentSelected(),s=shot(),track=s.cast_setup?.mask_tracks.find(t=>t.id===maskTrackId());
  const character=s.cast_setup?.characters.find(c=>c.id===track?.character_id),frame=maskEditState?.frame??0;
  const jobs=anchorJobs(),active=jobs.find(j=>['queued','waiting','running','cancelling'].includes(j.state));
  const hasMask=!!track?.state?.mask;
  const ready=hasMask&&!!character?.reference&&!!maskEditState?.ready&&maskEditState?.viewing!=='anchor'&&frame>=seg.start&&frame<seg.end;
  const key=JSON.stringify([ready,character?.id,character?.reference,seg.id,seg.cast_inputs.scene_anchors,s.cast_setup.scene_anchors,s.dismissed_anchor_jobs,jobs.map(j=>[j.id,j.updated,j.state]),anchorSubmission]);
  const context=$('#anchor-generation-context');if(context)context.textContent=`${character?.name||'Select a character'} · Frame ${frame}`;
  if(host.dataset.key===key)return;host.dataset.key=key;
  const scrollTop=host.querySelector('.anchor-candidates')?.scrollTop||0;
  const visibleJobs=jobs.filter(j=>j.data.result?.asset&&!(s.dismissed_anchor_jobs||[]).includes(j.id)),jobIds=new Set(visibleJobs.map(j=>j.id));
  const cards=visibleJobs.map(j=>{
    const entry=s.cast_setup.scene_anchors.find(a=>a.provenance?.job_id===j.id);
    return {created:entry?.provenance?.generator==='Manual import'?entry.provenance.created:j.created,order:0,html:anchorCard(null,j)};
  });
  s.cast_setup.scene_anchors.forEach((a,index)=>{if(!jobIds.has(a.provenance?.job_id))cards.push({created:a.provenance?.created||0,order:index,html:anchorCard(a)});});
  cards.sort((a,b)=>b.created-a.created||b.order-a.order);
  host.innerHTML=`<div class="anchor-generate-row"><span id="anchor-generation-context">${character?esc(character.name):'Select a character'} · Frame ${frame}</span><button id="anchor-generate" class="primary" ${!ready||active||anchorSubmission?'disabled':''}>${uiIcon('image')}Generate anchor</button></div>${!ready&&character?`<p class="note">${hasMask?'Select a source frame in this segment.':'Apply a mask for this character first.'}</p>`:''}${active?`<div class="anchor-job-status" role="status"><span>${esc(active.data.phase||active.state)}</span><button id="anchor-cancel" class="quiet" ${active.state==='cancelling'?'disabled':''}>Cancel</button></div>`:''}<div class="anchor-candidates" aria-label="Anchor images" tabindex="0">${cards.map(v=>v.html).join('')}</div>${jobs[0]?.state==='failed'&&!jobs[0].data.result?.asset?`<p class="error" role="alert">${esc(jobs[0].data.error||'Generation failed. Try again.')}</p>`:''}`;
  host.querySelector('.anchor-candidates').scrollTop=scrollTop;
  $('#anchor-generate').onclick=guarded(()=>composeAnchor(character.id,seg.id,maskEditState.frame));
  const cancel=$('#anchor-cancel');if(cancel)cancel.onclick=guarded(async()=>{cancel.disabled=true;await api(`jobs/${active.id}/cancel`,'POST');await updateFromServer();});
  $$('[data-anchor-preview]').forEach(b=>b.onclick=guarded(()=>previewAnchor(b.dataset.anchorPreview)));
  $$('[data-anchor-toggle]').forEach(b=>b.onclick=guarded(async()=>{b.disabled=true;try{await toggleAnchor(b.dataset.anchorToggle,b.getAttribute('aria-checked')!=='true');}finally{if(b.isConnected)b.disabled=false;}}));
  $$('[data-anchor-delete]').forEach(b=>b.onclick=()=>confirmDeleteAnchor(b.dataset.anchorDelete));
}
async function composeAnchor(characterId,segmentId,frame){
  const cid=S.clip,sid=S.shot,worker=S.worker;
  maskPause();await flushShotAutosave();await saveMaskEdits();
  dialog('Generate anchor','<p role="status">Preparing inputs…</p>');$('#dialog').classList.add('anchor-modal','anchor-compose-modal');
  const target=$('#dialog-body'),version=anchorDialogVersion;
  let draft;
  try{draft=await api(`clips/${cid}/shots/${sid}/cast/anchor-draft`,'POST',{revision:clip().revision,character_id:characterId,segment_id:segmentId,frame,worker});}
  catch(e){if(version===anchorDialogVersion&&$('#dialog').open)target.innerHTML=`<p role="alert">${esc(e.message)}</p>`;return;}
  if(!$('#dialog').open||version!==anchorDialogVersion||S.clip!==cid||S.shot!==sid)return;
  const draftKey=`anchor-prompt:${cid}:${sid}:${characterId}:${draft.reference}:${frame}`,saved=localStorage.getItem(draftKey);
  target.innerHTML=`<div class="anchor-compose"><div class="anchor-inputs"><figure><figcaption>Source frame · ${frame}</figcaption><img src="${imageAsset(draft.source,960)}" alt="Source frame ${frame}"></figure><figure><figcaption>Character · ${esc(draft.character_name)}</figcaption><img src="${imageAsset(draft.reference,960)}" alt="${esc(draft.character_name)} reference"></figure></div><label for="anchor-model">Model</label><select id="anchor-model">${draft.models.map(m=>`<option value="${esc(m.id)}">${esc(m.name)}</option>`).join('')}</select><label for="anchor-prompt">Prompt</label><textarea id="anchor-prompt" rows="6" maxlength="12000">${esc(saved??draft.request.prompt)}</textarea><p id="anchor-compose-error" role="alert"></p><div class="anchor-modal-actions"><button id="anchor-compose-cancel">Cancel</button><button id="anchor-submit" class="primary" disabled>Generate</button></div></div>`;
  const inputs=[...target.querySelectorAll('.anchor-inputs img')];
  const inputsReady=()=>{if($('#anchor-submit'))$('#anchor-submit').disabled=!inputs.every(i=>i.complete&&i.naturalWidth>0);};
  for(const img of inputs){img.addEventListener('load',inputsReady);img.addEventListener('error',()=>{if($('#anchor-compose-error'))$('#anchor-compose-error').textContent='An input preview could not load. Close and reopen to retry.';inputsReady();});}inputsReady();
  $('#anchor-prompt').oninput=e=>localStorage.setItem(draftKey,e.target.value);
  $('#anchor-compose-cancel').onclick=()=>$('#dialog').close();
  $('#anchor-submit').onclick=guarded(async()=>{
    const button=$('#anchor-submit'),prompt=$('#anchor-prompt').value,model=$('#anchor-model').value;
    if(!prompt.trim()){$('#anchor-compose-error').textContent='Enter an anchor prompt.';$('#anchor-prompt').focus();return;}
    button.disabled=true;anchorSubmission=`${cid}:${sid}`;
    try{
      const task=writeQueue.then(()=>api(`clips/${cid}/shots/${sid}/cast/anchor-jobs`,'POST',{...draft.request,prompt,model,worker}));
      writeQueue=task.catch(()=>{});const job=await task;acknowledgedJobs.set(job.id,job);if(version===anchorDialogVersion)$('#dialog').close();await refresh(false);syncWorkspace();
    }catch(e){if(version===anchorDialogVersion&&$('#anchor-compose-error'))$('#anchor-compose-error').textContent=e.message;}finally{anchorSubmission=null;if(button.isConnected)button.disabled=false;syncAnchorGeneration();}
  });
}
function anchorEntry(key){const [kind,id]=key.split(':');return kind==='job'?shot().cast_setup.scene_anchors.find(a=>a.provenance?.job_id===id):shot().cast_setup.scene_anchors.find(a=>a.id===id);}
async function toggleAnchor(key,enabled){
  const [kind,id]=key.split(':'),segmentId=segmentSelected().id,cid=S.clip,sid=S.shot;
  await flushShotAutosave();const task=writeQueue.then(()=>api(`clips/${cid}/shots/${sid}/cast/${kind==='job'?'anchor-jobs':'anchors'}/${id}/use`,'POST',{revision:S.data.clips.find(c=>c.id===cid).revision,segment_id:segmentId,enabled}));
  writeQueue=task.catch(()=>{});mergeSegmentClip(await task);await loadSegmentPlan(true);
}
function confirmDeleteAnchor(key){
  const [kind,id]=key.split(':'),a=anchorEntry(key),name=a?.name||anchorJobs().find(j=>j.id===id)?.data.result?.anchor_candidate?.character_name||'anchor';
  const cid=S.clip,sid=S.shot;
  dialog('Delete anchor?',`<p>Delete ${esc(name)} from this shot and all its segments?</p><p class="note">Saved takes are kept.</p><div class="anchor-modal-actions"><button id="anchor-delete-cancel">Cancel</button><button id="anchor-delete-confirm" class="danger">Delete anchor</button></div><p id="anchor-delete-error" role="alert"></p>`);
  $('#anchor-delete-cancel').onclick=()=>$('#dialog').close();
  $('#anchor-delete-confirm').onclick=guarded(async()=>{
    const button=$('#anchor-delete-confirm');button.disabled=true;
    try{await flushShotAutosave();const task=writeQueue.then(()=>api(`clips/${cid}/shots/${sid}/cast/${kind==='job'?'anchor-jobs':'anchors'}/${id}`,'DELETE',{revision:S.data.clips.find(c=>c.id===cid).revision}));writeQueue=task.catch(()=>{});mergeSegmentClip(await task);$('#dialog').close();await loadSegmentPlan(true);}
    catch(e){$('#anchor-delete-error').textContent=e.message;}finally{if(button.isConnected)button.disabled=false;}
  });
}
async function previewAnchor(key){
  const [kind,id]=key.split(':'),a=anchorEntry(key),job=kind==='job'?anchorJobs().find(j=>j.id===id):anchorJobs().find(j=>j.id===a?.provenance?.job_id),original=job?.data.result?.anchor_candidate,c=original&&(!a||a.asset===original.asset)?original:null;
  if(!a&&!c&&job?.data.result?.asset){
    const finishing=['queued','waiting','running','cancelling'].includes(job.state);
    dialog('Generated anchor image',`<div class="anchor-review anchor-raw-review"><p role="status">${finishing?'Image generated. Automatic finishing is still in progress.':'Image generated, but automatic finishing did not complete. You can download this image, edit it if needed, and upload it as an anchor.'}</p><img class="anchor-single-preview" src="${imageAsset(job.data.result.asset,1280)}" alt="Generated anchor before finishing"><div class="anchor-review-footer"><a href="${asset(job.data.result.asset,true)}">Download image</a></div>${job.data.error?`<details><summary>Finishing error</summary><p>${esc(job.data.error)}</p></details>`:''}</div>`);
    $('#dialog').classList.add('anchor-modal','anchor-review-modal');
    return;
  }
  const size=c?.alignment?.source_size||clip().dimensions,used=!!a&&segmentSelected().cast_inputs.scene_anchors.includes(a.id);
  dialog('Review anchor',`<div class="anchor-review"><div id="anchor-review-media"></div><div class="anchor-review-footer"><div class="row">${a?'<button id="anchor-review-replace" class="quiet">Replace</button><button id="anchor-review-details" class="quiet">Details</button>':''}${c?`<a href="${asset(c.asset,true)}">Download anchor</a><a href="${asset(c.raw,true)}">Raw image</a><a href="${asset(c.provenance,true)}">Generation details</a>`:`<a href="${asset(a.asset,true)}">Download anchor</a>`}</div><button id="anchor-review-use" role="switch" aria-checked="${used}" class="anchor-use-toggle ${used?'active':''}"><span class="anchor-toggle-track" aria-hidden="true"></span>Use anchor</button></div><p id="anchor-review-error" role="alert"></p></div>`);
  $('#dialog').classList.add('anchor-modal','anchor-review-modal');
  if(c){
    const makeWipe=typeof createImageWipe==='function'?createImageWipe:(await import(/* @vite-ignore */ new URL('static/vendor/report-media.js',globalThis.location.href).href)).createImageWipe;
    anchorComparison=makeWipe($('#anchor-review-media'),{left:{src:imageAsset(c.source,1280),width:size[0],height:size[1],label:'Source'},right:{src:imageAsset(c.asset,1280),width:size[0],height:size[1],label:'Anchor'}});
    $('#anchor-review-media .ak-report-wipe-stage').style.width=`min(100%, calc((100dvh - 250px) * ${size[0]/size[1]}))`;anchorComparison.load();
  }else $('#anchor-review-media').innerHTML=`<img class="anchor-single-preview" src="${imageAsset(a.asset,1280)}" alt="${esc(a.name)}">`;
  if(a){$('#anchor-review-replace').onclick=()=>castUpload(a.id);$('#anchor-review-details').onclick=()=>castEdit(a.id);}
  $('#anchor-review-use').onclick=guarded(async()=>{const button=$('#anchor-review-use');button.disabled=true;try{await toggleAnchor(key,button.getAttribute('aria-checked')!=='true');const entry=anchorEntry(key),on=!!entry&&segmentSelected().cast_inputs.scene_anchors.includes(entry.id);button.setAttribute('aria-checked',String(on));button.classList.toggle('active',on);}catch(e){$('#anchor-review-error').textContent=e.message;}finally{button.disabled=false;}});
}
