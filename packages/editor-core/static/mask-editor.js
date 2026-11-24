let maskEditState = null;
const maskCopy = (v) => JSON.parse(JSON.stringify(v));
const maskKey = (c, s) => `h3-mask-draft:${c.id}:${s.id}${maskTrackId(s)?":"+maskTrackId(s):":empty:"+(segmentSelected()?.id||"shot")}`;
function maskHasDraft() {
  return (
    S.tab === "render" &&
    maskEditState?.key === `${S.clip}:${S.shot}` &&
    maskEditState.dirty
  );
}
function maskPointGroups(points) {
  const groups = {};
  for (const p of points || [])
    (groups[p.frame] ||= []).push([p.x, p.y, p.label]);
  for (const values of Object.values(groups))
    values.sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b)));
  return groups;
}
function maskPending(points, s) {
  const current = maskPointGroups(points),
    applied = maskPointGroups(s.mask_applied_points);
  return [...new Set([...Object.keys(current), ...Object.keys(applied)])]
    .map(Number)
    .filter((f) => JSON.stringify(current[f]) !== JSON.stringify(applied[f]));
}
function maskEditorHtml(c, s) {
  s=maskShot(s);
  return `<div class="mask-editor" id="shot-mask-panel" aria-label="Shot video and mask editor"><div class="mask-viewport" id="mask-viewport"><video id="mask-video" muted playsinline preload="auto"></video><canvas id="mask-source" aria-label="Source frame"></canvas><canvas id="mask-preview" aria-label="Person mask overlay"></canvas><svg id="mask-points" viewBox="0 0 ${c.dimensions[0]} ${c.dimensions[1]}" preserveAspectRatio="xMidYMid meet" tabindex="0" role="application" aria-label="Mask correction points. Choose Include or Exclude, then click the person or background."></svg><span id="mask-anchor-label" class="mask-anchor-label" role="status" hidden></span><span id="mask-loading" class="mask-loading" role="status" aria-live="polite" data-state="loading">Loading preview…</span></div><div class="panel-body stack"><div class="mask-tools" role="group" aria-label="Point tools">${[
    ["include", "Include"],
    ["exclude", "Exclude"],
    ["move", "Move"],
    ["delete", "Delete"],
  ]
    .map(
      ([id, label]) =>
        `<button data-mask-tool="${id}" aria-label="${label}" aria-pressed="false" title="${label} correction points">${uiIcon({include:"plus",exclude:"minus",move:"move",delete:"trash"}[id])}<span>${label}</span></button>`,
    )
    .join(
      "",
    )}<button id="mask-undo" class="h3-icon-button" title="Undo point or padding edit" aria-label="Undo point or padding edit">${uiIcon('undo')}</button><button id="mask-redo" class="h3-icon-button" title="Redo point or padding edit" aria-label="Redo point or padding edit">${uiIcon('redo')}</button></div><div class="mask-transport" style="${playerMetrics(s.end-s.start,s.start)}"><div class="h3-playback-main"><button id="mask-play" class="primary h3-icon-button" aria-label="Play" title="Play" disabled>${uiIcon('play')}</button><div class="h3-frame-stepper"><button id="mask-prev" class="quiet h3-icon-button" aria-label="Previous frame" title="Previous frame">${uiIcon('back')}</button><label id="mask-frame-label" class="h3-sr-only" for="mask-frame-number">Source frame</label><input id="mask-frame-number" aria-label="Source frame" type="number" min="${s.start}" max="${s.end - 1}" value="${s.start}" class="mask-frame-number"><span class="h3-frame-total">/ ${s.end-1}</span><button id="mask-next" class="quiet h3-icon-button" aria-label="Next frame" title="Next frame">${uiIcon('next')}</button></div><output id="mask-time" class="h3-play-time">0.00s</output></div><div class="h3-playback-options"><select id="mask-speed" aria-label="Mask playback speed"><option value="0.5">0.5×</option><option value="1" selected>1×</option><option value="2">2×</option></select><label class="check"><input id="mask-loop" type="checkbox" checked>Loop</label><label class="check"><input id="mask-audio" type="checkbox">Audio</label></div><button id="mask-preview-retry" hidden>Retry preview</button></div><input class="range" id="mask-frame" type="range" min="0" max="${s.end - s.start - 1}" value="0" aria-label="Shot frame"><div class="row spread"><label for="mask-padding">Expansion <output class="h3-sr-only" id="mask-padding-value">${s.expansion}px</output></label><input id="mask-padding-number" class="mask-frame-number" aria-label="Expansion in pixels" type="number" min="0" max="256" value="${s.expansion}"><label class="check"><input id="mask-show" type="checkbox" checked>Show mask</label></div><input id="mask-padding" class="range" type="range" min="0" max="256" step="1" value="${s.expansion}" aria-label="Mask expansion"><div class="row"><button id="mask-clear-frame" class="quiet">Clear frame points</button></div><div class="row"><button id="mask-update">${uiIcon("refresh")}<span id="mask-frame-action">Preview frame</span></button><button id="mask-track" class="primary">${uiIcon("track")}Track</button></div><div class="mask-feedback" hidden><div id="mask-save-status" class="note" role="status"></div><button id="mask-save-retry" hidden>Retry saving expansion</button><div id="mask-edit-status" role="status" class="note"></div></div><button id="mask-reload" class="quiet" hidden>Discard local edits & load saved</button><details><summary>Edit mask</summary><div class="stack mask-advanced"><form id="mask-name-form"><label class="label" for="mask-name">Mask name</label><div class="mask-name-row"><input id="mask-name" type="text" maxlength="120" required autocomplete="off"><button id="mask-name-save" type="submit" aria-label="Save mask name" disabled>Save</button></div></form><div class="mask-tracking-settings"><label class="label" for="mask-text">SAM prompt</label><input id="mask-text" placeholder="person" value="${esc(s.mask_text || "person")}"><div class="mask-person-controls"><div class="mask-setting-field"><label class="label" for="subject">Person selection</label><select id="subject">${[["largest","Largest"],["leftmost","Leftmost"],["rightmost","Rightmost"],["index","By index"]].map(([value,label]) => `<option value="${value}" ${s.selection === value ? "selected" : ""}>${label}</option>`).join("")}</select></div><div id="mask-subject-index-field" class="mask-setting-field" ${s.selection === "index" ? "" : "hidden"}><label class="label" for="subject-index">Person index</label><input id="subject-index" type="number" min="0" max="100" value="${s.subject_index}" title="Person index, starting at 0"></div></div><button id="track-mask">Auto-track person</button></div></div></details></div></div>`;
}
function maskSnapshot() {
  return {
    points: maskCopy(maskEditState.points),
    padding: maskEditState.padding,
  };
}
function arrangeMaskWorkspace() {
  const body=$('.mask-editor > .panel-body'),padding=$('.mask-padding-row'),actions=$('#mask-update').parentElement;
  body.classList.add('mask-control-deck');
  const adjustments=document.createElement('div');adjustments.className='mask-adjustments';padding.before(adjustments);adjustments.append(padding,actions);
  $('.mask-advanced').append($('#mask-reload'),$('.mask-feedback'),$('#mask-clear-frame').parentElement);

}
function maskKeepDraft() {
  const e = maskEditState;
  e.dirty =
    !sameSettings(e.points,e.basePoints) ||
    e.padding !== e.basePadding;
  if (e.dirty)
    localStorage.setItem(
      e.storageKey,
      JSON.stringify({
        points: e.points,
        padding: e.padding,
        basePoints: e.basePoints,
        basePadding: e.basePadding,
        source: e.source,
        start: e.start,
        end: e.end,
      }),
    );
  else localStorage.removeItem(e.storageKey);
  S.dirty = Boolean(S.renderDraftDirty || e.dirty);
  maskDrawPoints();
  updateMaskEditorStatus();
  clearTimeout(e.pointsTimer);
  if(e.dirty&&!e.creating&&!e.saveError)e.pointsTimer=setTimeout(()=>saveMaskEdits().catch(error=>{e.saveError=error.message;updateMaskEditorStatus();}),400);
}
function maskEdit(fn) {
  const before = maskSnapshot();
  fn(maskEditState);
  maskEditState.saveError=null;
  if (JSON.stringify(before) !== JSON.stringify(maskSnapshot())) {
    maskEditState.undo.push(before);
    maskEditState.redo = [];
    maskKeepDraft();
  }
}
function maskUndo(direction) {
  const e = maskEditState,
    from = e[direction],
    to = e[direction === "undo" ? "redo" : "undo"];
  if (!from.length) return;
  const oldPadding = e.padding;
  to.push(maskSnapshot());
  Object.assign(e, from.pop());
  e.selected = null;
  maskSyncPadding();
  maskKeepDraft();
  if (oldPadding !== e.padding) {maskLoadFrame();maskSchedulePadding();}
}
function maskSyncPadding() {
  if (!$("#mask-padding")) return;
  $("#mask-padding").value = maskEditState.padding;
  $("#mask-padding-number").value = maskEditState.padding;
  $("#mask-padding-value").textContent = `${maskEditState.padding}px`;
  if ($("#mask-padding-summary"))
    $("#mask-padding-summary").textContent =
      `${maskEditState.padding}px padding`;
}
function maskDrawPoints() {
  const e = maskEditState,
    svg = $("#mask-points");
  if (!svg) return;
  const [w, h] = clip().dimensions;
  const rect = svg.getBoundingClientRect(),
    scale = Math.min(rect.width / w, rect.height / h),
    r = 6 / Math.max(scale, 0.01);
  svg.innerHTML = e.points
    .filter((p) => p.frame === e.frame)
    .map(
      (p) =>
        `<g data-point-id="${esc(p.id)}"><circle cx="${p.x * w}" cy="${p.y * h}" r="${r}" fill="${p.label ? selectedMaskColor() : "#ef5555"}" stroke="${e.selected === p.id ? "#ffec83" : "white"}" stroke-width="${(e.selected === p.id ? 3 : 1.5) / Math.max(scale, 0.01)}"></circle><text x="${p.x * w}" y="${p.y * h}" dy=".32em" text-anchor="middle" fill="white" font-size="${10 / Math.max(scale, 0.01)}" font-weight="bold">${p.label ? "+" : "−"}</text></g>`,
    )
    .join("");
  $$("[data-mask-tool]").forEach((b) => {
    b.classList.toggle("active", b.dataset.maskTool === e.tool);
    b.setAttribute("aria-pressed", String(b.dataset.maskTool === e.tool));
  });
  $("#mask-undo").disabled = !e.undo.length;
  $("#mask-redo").disabled = !e.redo.length;
  $("#mask-clear-frame").disabled = e.viewing==='anchor' || !e.points.some((p) => p.frame === e.frame);
}
function maskSeek(frame) {
  const e=maskEditState;
  if(e.viewing==='anchor')maskSetViewing('source',false);
  maskPause(); e.requested=Math.max(0,Math.min(e.end-e.start-1,Math.round(frame)));
  e.selected=null;
  if (!e.ready) return;
  e.loading=true; e.seeking=true;
  const player=e.player,controller=e.playbackAbort;
  player.seek(e.requested,e.end-e.start-1).then(()=>{if(controller.signal.aborted||e.player!==player)return;if(Math.round(player.time*24)===e.requested)e.seeking=false;return maskPresent(e);}).catch(error=>{if(!controller.signal.aborted)maskPlaybackError(e,error);});
}
function maskLoadFrame() {
  const e=maskEditState;
  if (!e || !$('#mask-source')) return;
  if ((!e.player && (!connected || !e.loading)) || e.loadedMask!==maskShot().mask) loadMaskPlayback();
  else maskPresent(e);
}
function maskPointDraft(e=maskEditState) {
  return !!e && !sameSettings(e.points,e.basePoints);
}
function maskSchedulePadding() {
  const e=maskEditState;
  clearTimeout(e.paddingTimer);
  e.paddingError=null;
  e.paddingTimer=setTimeout(()=>saveMaskPadding(e).catch(()=>{}),350);
  updateMaskEditorStatus();
}
// Autosave timers, segment and mask switches and layout edits all flush mask
// edits. Each editor runs one flush that callers share; two independent ones
// could each send the same draft against one saved base, and the second would
// report a false conflict.
function saveMaskPadding(e=maskEditState) {
  if (!e || (shot()?.segment_masks_version===1&&!e.trackId)) return Promise.resolve();
  clearTimeout(e.paddingTimer);
  // Cleared in .finally, which always runs after this assignment.
  return e.paddingFlush ??= (async()=>{
    while(e.padding!==e.basePadding){
        const value=e.padding, before=e.basePadding;
        e.paddingError=null;
        e.paddingSave=maskPatch({expansion:value},'shot',{expansion:before},{clipId:e.key.split(':')[0],shotId:e.key.split(':')[1],trackId:e.trackId}).then(()=>{
          e.basePadding=value;
        }).catch(error=>{e.paddingError=error.message;throw error;}).finally(()=>{
          e.paddingSave=null;
          if(e===maskEditState && e.key===`${S.clip}:${S.shot}` && $('#mask-save-status'))maskKeepDraft();
        });
      updateMaskEditorStatus();
      await e.paddingSave;
    }
  })().finally(()=>{e.paddingFlush=null;});
}
function saveMaskEdits(e=maskEditState) {
  if(!e)return Promise.resolve();
  clearTimeout(e.pointsTimer);
  return e.pointFlush ??= (async()=>{
    for(;;){
      if(e.creating)await e.creating;
      if(!e.trackId)return;
      await saveMaskPadding(e);
      if(!maskPointDraft(e))return;
      const points=maskCopy(e.points),before=maskCopy(e.basePoints);
      e.pointSave=maskPatch({mask_points:points},'shot',{mask_points:before},{clipId:e.key.split(':')[0],shotId:e.key.split(':')[1],trackId:e.trackId}).then(()=>{
        e.basePoints=points;e.conflict=false;e.saveError=null;
      }).catch(error=>{e.saveError=error.message;throw error;}).finally(()=>{e.pointSave=null;if(e===maskEditState)maskKeepDraft();});
      await e.pointSave;
    }
  })().finally(()=>{e.pointFlush=null;});
}
// Create the server track without remounting the preview or dropping rapid clicks.
function createPointMask(e,segmentId) {
  const initial=maskCopy(e.points),[cid,sid]=e.key.split(':'),oldKey=e.storageKey;
  const task=writeQueue.then(async()=>{
    const c=S.data.clips.find(c=>c.id===cid),before=new Set(c.shots.find(s=>s.id===sid).cast_setup.mask_tracks.map(t=>t.id));
    const result=await api(`clips/${cid}/shots/${sid}/cast/masks`,'POST',{revision:c.revision,segment_id:segmentId,points:initial});
    mergeSegmentClip(result);
    const track=result.shots.find(s=>s.id===sid).cast_setup.mask_tracks.find(t=>!before.has(t.id)&&t.segment_id===segmentId);
    e.trackId=track.id;e.basePoints=maskCopy(track.state.mask_points);e.basePadding=track.state.expansion;
    e.storageKey=`h3-mask-draft:${cid}:${sid}:${track.id}`;
    localStorage.setItem(`cast-track:${cid}:${sid}:${segmentId}`,track.id);
    localStorage.removeItem(oldKey);
  });
  writeQueue=task.catch(()=>{});e.creating=task;
  task.catch(error=>{e.saveError=error.message;reportError(error);}).finally(()=>{
    e.creating=null;
    if(e===maskEditState){maskKeepDraft();bindCastMaskSelector();void loadSegmentPlan(true);}
  });
  return task;
}
function updateMaskEditorStatus() {
  const e = maskEditState;
  if (!e || !$("#mask-edit-status") || e.key !== `${S.clip}:${S.shot}`) return;
  const s = maskShot(),
    pending = maskPending(e.points, s);
  const job = S.data.jobs.find(
    (j) =>
      j.clip_id === S.clip &&
      j.shot_id === S.shot && (j.data.mask_track_id??null)===(e.trackId??null) &&
      ["mask", "mask_edit"].includes(j.kind) &&
      ["queued", "waiting", "running"].includes(j.state),
  );
  const last = S.data.jobs.find(
    (j) =>
      j.clip_id === S.clip &&
      j.shot_id === S.shot && (j.data.mask_track_id??null)===(e.trackId??null) &&
      ["mask", "mask_edit"].includes(j.kind),
  );
  const busy = !!job || e.submitting;
  const active=S.data.jobs.filter(j=>j.clip_id===S.clip&&j.shot_id===S.shot&&(j.data.mask_track_id??null)===(e.trackId??null)&&['mask','mask_edit'].includes(j.kind)&&['submitting','queued','waiting','running','cancelling'].includes(j.state));
  const frameBusy=active.length>0;
  const tracked=hasTrackedMask(s);
  $("#mask-frame-action").textContent=tracked?'Update frame':'Preview frame';
  $("#mask-update").title=tracked?'Correct this frame of the tracked mask':'Preview this frame. Track the segment to use this mask in a render.';
  $("#mask-update").disabled =
    e.viewing==='anchor' || !!e.submitting || frameBusy || !pending.includes(e.frame);
  $("#mask-track").disabled = e.viewing==='anchor' || !!e.submitting || active.length > 0 || !(e.points.length || s.mask);
  $("#track-mask").disabled = e.viewing==='anchor' || busy;
  const noCharacter=!!shot().cast_setup&&!e.trackId;
  if(noCharacter){$('#mask-update').disabled=true;$('#mask-track').disabled=true;$('#track-mask').disabled=true;}
  const hostedMasks=S.data.processing?.masks;
  if(hostedMasks?.available===false){for(const id of ['#mask-update','#mask-track','#track-mask']){const button=$(id);button.disabled=true;button.title=hostedMasks.reason;}}
  const pricedSegment=segmentSelected();
  for(const id of ['#mask-update','#mask-track'])generationPriceButton($(id),{operation:'track-mask',seconds:pricedSegment?(pricedSegment.end-pricedSegment.start)/24:undefined});
  const canCreate=shot().segment_masks_version===1&&!!segmentSelected();
  $$('[data-mask-tool]').forEach(button=>button.disabled=(noCharacter&&!canCreate)||e.viewing==='anchor');
  $('#mask-points').style.pointerEvents=noCharacter&&!canCreate?'none':'';
  for(const selector of ['#mask-padding','#mask-padding-number'])$(selector).disabled=noCharacter;
  $('#mask-clear-frame').disabled=noCharacter || e.viewing==='anchor' || !e.points.some(p=>p.frame===e.frame);

  const text = e.saveError || (e.conflict
    ? "Saved settings changed elsewhere. Your local edits are kept."
    : !busy && last && ["failed", "interrupted"].includes(last.state)
      ? `${last.state === "failed" ? "Mask update failed" : "Mask update needs inspection"}: ${last.data.error || last.data.phase}`
      : segmentSelected()?.cast_inputs?.mask_tracks.includes(e.trackId) && maskMissesSegment(s, segmentSelected())
        ? "This mask doesn’t cover the whole segment. Track segment to extend it, or turn off Use in render."
        : "");
  $("#mask-edit-status").textContent = text;
  $("#mask-edit-status").hidden = !text;
  const coverage = $("#mask-coverage");
  if (coverage) {
    const seg=shot().segment_masks_version===1?segmentSelected():null;
    const begin=seg?.start??0,end=seg?.end??s.end-s.start;
    const range=trackedMaskFrames(s),from=Math.max(begin,range?.[0]??begin),to=Math.min(end,range?.[1]??end);
    const total=end-begin,covered=Math.max(0,to-from)-(s.mask_empty_frames||[]).filter(f=>f>=from&&f<to).length;
    coverage.textContent = s.mask ? `${covered} / ${total} frames` : "No mask yet";
    coverage.dataset.ready = String(!!s.mask && covered === total);
  }
  $('#mask-save-status').textContent=e.paddingError?`Expansion not saved: ${e.paddingError}`:'';
  $('#mask-save-status').hidden=!e.paddingError;
  $('#mask-save-retry').hidden=!e.paddingError;
  $('.mask-feedback').hidden=!text && !e.paddingError;
  $('#mask-reload').hidden=!e.conflict;
  if (e.loadedMask !== s.mask && !e.loading) maskLoadFrame();
  if ($("#mask-review-state"))
    $("#mask-review-state").textContent =
      s.mask && !maskPointDraft(e) && !pending.length ? "Ready" : "Needs correction";
  updateRenderHistory();
}
async function runMaskCorrection(mode) {
  const e = maskEditState,
    cid = S.clip,
    sid = S.shot,
    frame = mode==='shot'&&shot().segment_masks_version===1
      ? Math.max(segmentSelected().start,Math.min(segmentSelected().end-1,e.frame)) : e.frame;
  const price = generationPriceInput($(mode === "frame" ? "#mask-update" : "#mask-track"));
  e.submitting = true;
  updateMaskEditorStatus();
  try {
    await saveMaskEdits();
    await api("jobs", "POST", {
      ...price,
      kind: "mask_edit",
      clip_id: cid,
      shot_id: sid,
      worker: S.worker,
      options: { mode, frame, ...(e.trackId?{track_id:e.trackId}:{}) },
    });
    updateMaskEditorStatus();
    toast(
      mode === "frame" ? "Frame correction queued" : "Segment tracking queued",
    );
  } finally {
    e.submitting = false;
    updateMaskEditorStatus();
  }
}
function bindMaskEditor(c, s, initialFrame) {
  const trackId=maskTrackId(s);s=maskShot(s);
  $('#track-mask').closest('.mask-tracking-settings').hidden=shot().segment_masks_version===1;
  const key = `${c.id}:${s.id}`,
    previous = maskEditState;
  if (previous?.key !== key || !previous.dirty) {
    const draft = (() => {
      try {
        return JSON.parse(localStorage.getItem(maskKey(c, s)));
      } catch (_) {
        return null;
      }
    })();
    const source = c.render_source || c.source;
    maskEditState = {
      key,
      trackId,
      segmentId:segmentSelected()?.id,
      storageKey: maskKey(c, s),
      source,
      start: s.start,
      end: s.end,
      frame: initialFrame ?? (previous?.key === key ? previous.frame : 0),
      tool: previous?.tool || "include",
      points: maskCopy(s.mask_points || []),
      basePoints: maskCopy(s.mask_points || []),
      padding: s.expansion,
      basePadding: s.expansion,
      undo: previous?.key === key ? previous.undo : [],
      redo: previous?.key === key ? previous.redo : [],
      ticket: 0,
      dirty: false,
    };
    if (
      draft &&
      draft.source === source &&
      draft.start === s.start &&
      draft.end === s.end
    )
      Object.assign(maskEditState, draft, {
        dirty: true,
        conflict:
          !sameSettings(draft.basePoints,s.mask_points || []) ||
          draft.basePadding !== s.expansion,
      });
  }
  const e = maskEditState;
  $("#mask-text").value = s.mask_text || "person";
  $("#subject").value = s.selection || "largest";
  $("#subject-index").value = s.subject_index || 0;
  $('#mask-subject-index-field').hidden=$('#subject').value!=='index';
  $('#subject').onchange=()=>{$('#mask-subject-index-field').hidden=$('#subject').value!=='index';};
  S.dirty = Boolean(S.dirty || e.dirty);
  $("#mask-play").onclick=maskPlay;
  $("#mask-speed").onchange=event=>$("#mask-video").playbackRate=Number(event.target.value);
  $("#mask-audio").onchange=event=>$("#mask-video").muted=!event.target.checked;
  $("#mask-loop").onchange=event=>$("#mask-video").loop=event.target.checked;
  $("#mask-video").onended=()=>{if(!$("#mask-video").loop)maskPause();};
  $("#mask-preview-retry").onclick=()=>loadMaskPlayback(true);
  $("#mask-save-retry").onclick=guarded(()=>saveMaskPadding());
  $$("[data-mask-tool]").forEach(
    (b) =>
      (b.onclick = () => {
        maskPause();
        e.tool = b.dataset.maskTool;
        maskDrawPoints();
      }),
  );
  $("#mask-undo").onclick = () => maskUndo("undo");
  $("#mask-redo").onclick = () => maskUndo("redo");
  $("#mask-clear-frame").onclick = () =>
    maskEdit((v) => {
      v.points = v.points.filter((p) => p.frame !== v.frame);
      v.selected = null;
    });
  $("#mask-frame").oninput = (ev) => {if(e.viewing!=='anchor')maskSeek(Number(ev.target.value));};
  $("#mask-frame-number").onchange = (ev) =>
    {if(e.viewing!=='anchor')maskSeek(Number(ev.target.value) - s.start);};
  $("#mask-prev").onclick = () => maskSeek(e.frame - 1);
  $("#mask-next").onclick = () => maskSeek(e.frame + 1);
  let paddingBefore;
  $("#mask-padding").onpointerdown = () => {
    paddingBefore = maskSnapshot();
  };
  $("#mask-padding").oninput = (ev) => {
    paddingBefore ||= maskSnapshot();
    e.padding = Number(ev.target.value);
    maskSyncPadding();
    maskKeepDraft();
    maskLoadFrame();
    maskSchedulePadding();
  };
  $("#mask-padding").onchange = () => {
    if (
      paddingBefore &&
      JSON.stringify(paddingBefore) !== JSON.stringify(maskSnapshot())
    ) {
      e.undo.push(paddingBefore);
      e.redo = [];
    }
    paddingBefore = null;
    maskDrawPoints();
    saveMaskPadding().catch(()=>{});
  };
  $("#mask-padding-number").onchange = (event) => {
    const value = Number(event.target.value);
    if (!Number.isInteger(value) || value < 0 || value > 256) {
      maskSyncPadding();
      toast("Expansion must be between 0 and 256 pixels");
      return;
    }
    maskEdit((v) => {
      v.padding = value;
    });
    maskSyncPadding();
    maskLoadFrame();
    maskSchedulePadding();
  };
  $("#mask-show").onchange = (ev) => {
    $("#mask-preview").hidden = !ev.target.checked;
  };
  $("#mask-update").onclick = guarded(() => runMaskCorrection("frame"));
  $("#mask-track").onclick = guarded(() => runMaskCorrection("shot"));
  $("#mask-reload").onclick = guarded(async () => {
    localStorage.removeItem(e.storageKey);
    e.dirty = false;
    disposeMaskPlayback();
    maskEditState = null;
    S.dirty = Boolean(S.renderDraftDirty);
    await refresh(false);
    bindMaskEditor(clip(), maskShot());
  });
  $("#track-mask").onclick = guarded(async () => {
    await saveMaskEdits();
    await maskPatch({
      selection: $("#subject").value,
      subject_index: Number($("#subject-index").value),
      mask_text: $("#mask-text").value,
    });
    await queue("mask", s.id, c.id);
    updateMaskEditorStatus();
  });
  const svg = $("#mask-points");
  const coordinate = (ev) => {
    const rect = svg.getBoundingClientRect(),
      [w, h] = c.dimensions,
      scale = Math.min(rect.width / w, rect.height / h),
      dx = (rect.width - w * scale) / 2,
      dy = (rect.height - h * scale) / 2;
    return {
      x: (ev.clientX - rect.left - dx) / (w * scale),
      y: (ev.clientY - rect.top - dy) / (h * scale),
      scale,
      w,
      h,
    };
  };
  const closest = (q) =>
    e.points
      .filter((p) => p.frame === e.frame)
      .map((p) => ({
        p,
        d: Math.hypot((p.x - q.x) * q.w * q.scale, (p.y - q.y) * q.h * q.scale),
      }))
      .filter((v) => v.d < 13)
      .sort((a, b) => a.d - b.d)[0]?.p;
  svg.oncontextmenu = (ev) => ev.preventDefault();
  svg.onpointerdown = async (ev) => {
    if (e.viewing==='anchor' || e.loading || e.loadError || e.player?.busy || ev.button > 2) return;
    maskPause();
    const q = coordinate(ev);
    if (q.x < 0 || q.x > 1 || q.y < 0 || q.y > 1) return;
    ev.preventDefault();
    svg.focus();
    const found = closest(q),
      tool = ev.button === 2 ? "exclude" : e.tool;
    if (tool === "delete") {
      if (found)
        maskEdit((v) => {
          v.points = v.points.filter((p) => p.id !== found.id);
          v.selected = null;
        });
      return;
    }
    if (tool === "move") {
      if (found) {
        e.selected = found.id;
        e.drag = { id: found.id, before: maskSnapshot() };
        svg.setPointerCapture(ev.pointerId);
        maskDrawPoints();
      }
      return;
    }
    const seg=segmentSelected();
    if(shot().segment_masks_version===1&&(!seg||e.frame<seg.start||e.frame>=seg.end)){
      toast('Move the playhead into this segment to add mask points.');return;
    }
    if(shot().segment_masks_version===1 && !e.trackId && tool!=='include')return;
    maskEdit((v) => {
      if (v.points.length >= 256)
        throw Error("Use at most 256 points per segment");
      const point = {
        id: crypto.randomUUID(),
        frame: v.frame,
        x: q.x,
        y: q.y,
        label: tool === "include" ? 1 : 0,
      };
      v.points.push(point);
      v.selected = point.id;
    });
    if(shot().segment_masks_version===1&&!e.trackId&&!e.creating)void createPointMask(e,seg.id).catch(()=>{});
  };
  svg.onpointermove = (ev) => {
    if (!e.drag) return;
    const q = coordinate(ev),
      p = e.points.find((p) => p.id === e.drag.id);
    p.x = Math.max(0, Math.min(1, q.x));
    p.y = Math.max(0, Math.min(1, q.y));
    maskDrawPoints();
  };
  const finish = () => {
    if (!e.drag) return;
    const before = e.drag.before;
    e.drag = null;
    if (JSON.stringify(before) !== JSON.stringify(maskSnapshot())) {
      e.undo.push(before);
      e.redo = [];
      maskKeepDraft();
    }
  };
  svg.onpointerup = finish;
  svg.onpointercancel = finish;
  svg.onlostpointercapture = finish;
  svg.onkeydown = (ev) => {
    if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === "z") {
      ev.preventDefault();
      maskUndo(ev.shiftKey ? "redo" : "undo");
    } else if (["Delete", "Backspace"].includes(ev.key) && e.selected) {
      ev.preventDefault();
      maskEdit((v) => {
        v.points = v.points.filter((p) => p.id !== v.selected);
        v.selected = null;
      });
    }
  };
  maskSyncPadding();
  loadMaskPlayback();
  if(e.padding!==e.basePadding && !e.conflict)maskSchedulePadding();
  updateMaskEditorStatus();
}
