let maskEditState = null;
const maskCopy = (v) => JSON.parse(JSON.stringify(v));
const maskKey = (c, s) => `h3-mask-draft:${c.id}:${s.id}`;
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
  return `<div class="mask-editor"><div class="mask-viewport" id="mask-viewport"><video id="mask-video" muted playsinline preload="auto"></video><canvas id="mask-source" aria-label="Source frame"></canvas><canvas id="mask-preview" aria-label="Person mask overlay"></canvas><svg id="mask-points" viewBox="0 0 ${c.dimensions[0]} ${c.dimensions[1]}" preserveAspectRatio="xMidYMid meet" tabindex="0" role="application" aria-label="Mask correction points. Choose Include or Exclude, then click the person or background."></svg><span id="mask-loading" class="mask-loading">Loading frame…</span></div><div class="panel-body stack"><div class="mask-tools" role="group" aria-label="Point tools">${[
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
    )}<button id="mask-undo" class="h3-icon-button" title="Undo point or padding edit" aria-label="Undo point or padding edit">${uiIcon('undo')}</button><button id="mask-redo" class="h3-icon-button" title="Redo point or padding edit" aria-label="Redo point or padding edit">${uiIcon('redo')}</button></div><div class="mask-transport" style="${playerMetrics(s.end-s.start,s.start)}"><div class="h3-playback-main"><button id="mask-play" class="primary h3-icon-button" aria-label="Play" title="Play" disabled>${uiIcon('play')}</button><div class="h3-frame-stepper"><button id="mask-prev" class="quiet h3-icon-button" aria-label="Previous frame" title="Previous frame">${uiIcon('back')}</button><label id="mask-frame-label" class="h3-sr-only" for="mask-frame-number">Source frame</label><input id="mask-frame-number" aria-label="Source frame" type="number" min="${s.start}" max="${s.end - 1}" value="${s.start}" class="mask-frame-number"><span class="h3-frame-total">/ ${s.end-1}</span><button id="mask-next" class="quiet h3-icon-button" aria-label="Next frame" title="Next frame">${uiIcon('next')}</button></div><output id="mask-time" class="h3-play-time">0.00s</output></div><div class="h3-playback-options"><select id="mask-speed" aria-label="Mask playback speed"><option value="0.5">0.5×</option><option value="1" selected>1×</option><option value="2">2×</option></select><label class="check"><input id="mask-loop" type="checkbox" checked>Loop</label><label class="check"><input id="mask-audio" type="checkbox">Audio</label></div><button id="mask-preview-retry" hidden>Retry preview</button></div><input class="range" id="mask-frame" type="range" min="0" max="${s.end - s.start - 1}" value="0" aria-label="Shot frame"><div id="mask-keyframes" class="mask-keyframes" aria-label="Frames with correction points"></div><div class="row spread"><label for="mask-padding">Expansion <output class="h3-sr-only" id="mask-padding-value">${s.expansion}px</output></label><input id="mask-padding-number" class="mask-frame-number" aria-label="Mask padding pixels" type="number" min="0" max="256" value="${s.expansion}"><label class="check"><input id="mask-show" type="checkbox" checked>Show mask</label></div><input id="mask-padding" class="range" type="range" min="0" max="256" step="1" value="${s.expansion}" aria-label="Mask padding"><p class="note mask-help">Padding saves automatically; no retracking needed. Include/exclude points need Update frame or Track shot. Right-click adds an exclusion. Padding is measured at ${c.dimensions.join(" × ")}.</p><div class="row"><button id="mask-clear-frame" class="quiet">Clear frame points</button></div><div class="row"><button id="mask-update">${uiIcon("refresh")}Update frame</button><button id="mask-track" class="primary">${uiIcon("track")}Track shot</button></div><div class="mask-feedback"><div id="mask-save-status" class="note" role="status"></div><button id="mask-save-retry" hidden>Retry saving padding</button><div id="mask-edit-status" role="status" class="note"></div></div><button id="mask-reload" class="quiet" hidden>Discard local edits & load saved</button><details><summary>Tracking & mask history</summary><div class="stack mask-advanced"><div class="row"><input id="mask-text" aria-label="Target description" placeholder="person" value="${esc(s.mask_text || "person")}" style="width:100%"><select id="subject" aria-label="Person selection">${["largest", "leftmost", "rightmost", "index"].map((x) => `<option ${s.selection === x ? "selected" : ""}>${x}</option>`).join("")}</select><input id="subject-index" type="number" min="0" max="100" value="${s.subject_index}" style="width:68px" aria-label="Person index"><button id="track-mask">Auto-track person</button></div><label class="label" for="mask-version">Saved masks</label><select id="mask-version"></select></div></details></div></div>`;
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
  $('.mask-advanced').append($('#mask-reload'));
  const viewport=$('#mask-viewport');
  viewport.insertAdjacentHTML('afterend','<div id="mask-resize" class="mask-resize" role="separator" tabindex="0" aria-label="Video canvas height" aria-orientation="horizontal" aria-controls="mask-viewport" title="Drag or use arrow keys to resize. Double-click to reset."><span class="mask-resize-grip" aria-hidden="true"></span><span aria-hidden="true">Resize video</span></div>');
  const handle=$('#mask-resize'),key='h3-mask-canvas-height';let drag=null;
  const limit=()=>Math.max(360,Math.min(1200,window.innerHeight-140));
  function resize(height,persist=false){
    const value=Math.round(Math.max(220,Math.min(limit(),height)));
    viewport.style.height=value+'px';handle.setAttribute('aria-valuemin','220');handle.setAttribute('aria-valuemax',String(limit()));handle.setAttribute('aria-valuenow',String(value));handle.setAttribute('aria-valuetext',value+' pixels');
    if(persist)localStorage.setItem(key,String(value));
    maskDrawPoints();
  }
  const saved=Number(localStorage.getItem(key));resize(saved>0?saved:340);
  handle.onpointerdown=event=>{
    if(event.button!==0||drag)return;
    event.preventDefault();handle.focus({preventScroll:true});drag={id:event.pointerId,y:event.clientY,height:viewport.getBoundingClientRect().height};handle.setPointerCapture(event.pointerId);
  };
  handle.onpointermove=event=>{if(drag?.id===event.pointerId)resize(drag.height+event.clientY-drag.y);};
  handle.onpointerup=event=>{if(drag?.id!==event.pointerId)return;resize(viewport.getBoundingClientRect().height,true);drag=null;handle.releasePointerCapture(event.pointerId);};
  handle.onpointercancel=event=>{if(drag?.id===event.pointerId){resize(drag.height);drag=null;}};
  handle.onlostpointercapture=()=>{drag=null;};
  handle.onkeydown=event=>{
    const steps={ArrowUp:-24,ArrowDown:24,ArrowLeft:-24,ArrowRight:24};
    if(!(event.key in steps)&&!['Home','End'].includes(event.key))return;
    event.preventDefault();resize(event.key==='Home'?220:event.key==='End'?limit():viewport.getBoundingClientRect().height+steps[event.key]*(event.shiftKey?3:1),true);
  };
  handle.ondblclick=()=>{resize(340);localStorage.removeItem(key);};
}
function maskKeepDraft() {
  const e = maskEditState;
  e.dirty =
    JSON.stringify(e.points) !== JSON.stringify(e.basePoints) ||
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
}
function maskEdit(fn) {
  const before = maskSnapshot();
  fn(maskEditState);
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
        `<g data-point-id="${esc(p.id)}"><circle cx="${p.x * w}" cy="${p.y * h}" r="${r}" fill="${p.label ? "#20b477" : "#ef5555"}" stroke="${e.selected === p.id ? "#ffec83" : "white"}" stroke-width="${(e.selected === p.id ? 3 : 1.5) / Math.max(scale, 0.01)}"></circle><text x="${p.x * w}" y="${p.y * h}" dy=".32em" text-anchor="middle" fill="white" font-size="${10 / Math.max(scale, 0.01)}" font-weight="bold">${p.label ? "+" : "−"}</text></g>`,
    )
    .join("");
  $$("[data-mask-tool]").forEach((b) => {
    b.classList.toggle("active", b.dataset.maskTool === e.tool);
    b.setAttribute("aria-pressed", String(b.dataset.maskTool === e.tool));
  });
  const frames = [...new Set(e.points.map((p) => p.frame))].sort(
    (a, b) => a - b,
  );
  const keyframes=$("#mask-keyframes"), key=JSON.stringify([e.frame,frames.map(f=>[f,e.points.filter(p=>p.frame===f).length])]);
  if(keyframes.dataset.key!==key) {
  keyframes.dataset.key=key;
  keyframes.innerHTML = frames
    .map(
      (f) =>
        `<button class="quiet ${f === e.frame ? "active" : ""}" data-mask-frame="${f}">Frame ${e.start + f} · ${e.points.filter((p) => p.frame === f).length}</button>`,
    )
    .join("");
  $$("[data-mask-frame]").forEach(
    (b) => (b.onclick = () => maskSeek(Number(b.dataset.maskFrame))),
  );
  }
  $("#mask-undo").disabled = !e.undo.length;
  $("#mask-redo").disabled = !e.redo.length;
  $("#mask-clear-frame").disabled = !e.points.some((p) => p.frame === e.frame);
}
function maskSeek(frame) {
  const e=maskEditState;
  maskPause(); e.requested=Math.max(0,Math.min(e.end-e.start-1,Math.round(frame)));
  e.selected=null;
  if (!e.ready) return;
  e.loading=true; e.seeking=true;
  e.player.seek(e.requested,e.end-e.start-1).then(()=>{if(e.player && Math.round(e.player.time*24)===e.requested)e.seeking=false;return maskPresent(e);}).catch(error=>maskPlaybackError(e,error));
}
function maskLoadFrame() {
  const e=maskEditState;
  if (!e || !$('#mask-source')) return;
  if (!e.player || e.loadedMask!==shot().mask) loadMaskPlayback();
  else maskPresent(e);
}
function maskPointDraft(e=maskEditState) {
  return !!e && JSON.stringify(e.points)!==JSON.stringify(e.basePoints);
}
function maskSchedulePadding() {
  const e=maskEditState;
  clearTimeout(e.paddingTimer);
  e.paddingError=null;
  e.paddingTimer=setTimeout(()=>saveMaskPadding(e).catch(()=>{}),350);
  updateMaskEditorStatus();
}
async function saveMaskPadding(e=maskEditState) {
  if (!e) return;
  clearTimeout(e.paddingTimer);
  if (e.paddingSave) {await e.paddingSave; if(e.padding!==e.basePadding)return saveMaskPadding(e); return;}
  if(e.padding===e.basePadding)return;
  const value=e.padding, before=e.basePadding;
  e.paddingError=null;
  e.paddingSave=patch({expansion:value},'shot',{expansion:before},{clipId:e.key.split(':')[0],shotId:e.key.split(':')[1]}).then(()=>{
    e.basePadding=value;
  }).catch(error=>{e.paddingError=error.message;throw error;}).finally(()=>{
    e.paddingSave=null;
    if(e===maskEditState && e.key===`${S.clip}:${S.shot}` && $('#mask-save-status'))maskKeepDraft();
  });
  updateMaskEditorStatus();
  await e.paddingSave;
  if(e.padding!==e.basePadding)return saveMaskPadding(e);
}
async function saveMaskEdits() {
  const e=maskEditState;
  if (!e || e.key!==`${S.clip}:${S.shot}`)return;
  await saveMaskPadding(e);
  if (!maskPointDraft(e))return;
  const points=maskCopy(e.points);
  try {
    await patch({mask_points:points},'shot',{mask_points:e.basePoints},{clipId:e.key.split(':')[0],shotId:e.key.split(':')[1]});
    e.basePoints=points; e.conflict=false;
  }catch(error){e.conflict=true;throw error;}
  finally{if(e===maskEditState)maskKeepDraft();}
}
function updateMaskEditorStatus() {
  const e = maskEditState;
  if (!e || !$("#mask-edit-status") || e.key !== `${S.clip}:${S.shot}`) return;
  const s = shot(),
    pending = maskPending(e.points, s);
  const job = S.data.jobs.find(
    (j) =>
      j.clip_id === S.clip &&
      j.shot_id === S.shot &&
      ["mask", "mask_edit"].includes(j.kind) &&
      ["queued", "waiting", "running"].includes(j.state),
  );
  const last = S.data.jobs.find(
    (j) =>
      j.clip_id === S.clip &&
      j.shot_id === S.shot &&
      ["mask", "mask_edit"].includes(j.kind),
  );
  const busy = !!job || e.submitting;
  const active=S.data.jobs.filter(j=>j.clip_id===S.clip&&j.shot_id===S.shot&&['mask','mask_edit'].includes(j.kind)&&['queued','waiting','running','cancelling'].includes(j.state));
  const at=(points,f)=>JSON.stringify((points||[]).filter(p=>p.frame===f).map(p=>[p.x,p.y,p.label]).sort());
  const frameBusy=active.some(j=>j.kind==='mask'||j.data.options?.mode!=='frame'||(j.data.options.frame===e.frame&&at(j.data.shot?.mask_points,e.frame)===at(e.points,e.frame)));
  $("#mask-update").disabled =
    !!e.submitting || frameBusy || !pending.includes(e.frame);
  $("#mask-track").disabled = !!e.submitting || active.length > 0 || !(e.points.length || s.mask);
  $("#track-mask").disabled = busy;
  const text = e.conflict
    ? "Saved settings changed elsewhere. Your local edits are kept."
    : e.submitting
      ? "Submitting mask correction…"
      : job
        ? `${job.state === "running" ? "Updating mask" : job.state === "waiting" ? "Waiting for worker" : "Mask job queued"} · ${job.data.phase || ""}`
        : maskPointDraft(e)
          ? "Correction points changed. Update frame or Track shot saves and applies them."
          : pending.length
            ? `Points changed on ${pending.length} frame${pending.length === 1 ? "" : "s"}. Update frame or track shot.`
            : last && ["failed", "interrupted"].includes(last.state)
              ? `${last.state === "failed" ? "Mask update failed" : "Mask update needs inspection"}: ${last.data.error || last.data.phase}`
              : s.mask_empty_frames?.length
                ? `Missing mask on ${s.mask_empty_frames.length} frames. Add corrections or split out target absence.`
                : s.mask
                  ? "Mask ready."
                  : "Add include points or auto-track the person.";
  $("#mask-edit-status").textContent = text;
  $("#mask-edit-status").hidden = text === "Mask ready.";
  const coverage = $("#mask-coverage");
  if (coverage) {
    const total = s.end-s.start, covered = total-(s.mask_empty_frames?.length||0);
    coverage.textContent = s.mask ? `${covered} / ${total} frames` : "No mask yet";
    coverage.dataset.ready = String(!!s.mask && covered === total);
  }
  $('#mask-save-status').textContent=e.paddingError?`Padding not saved: ${e.paddingError}`:e.paddingSave?'Saving padding…':e.padding!==e.basePadding?'Padding will save automatically…':'Padding saved automatically';
  $('#mask-save-status').hidden = !e.paddingError && !e.paddingSave && e.padding === e.basePadding;
  $('#mask-save-retry').hidden=!e.paddingError;

  $("#mask-reload").hidden = !e.dirty;
  const versions = s.mask_versions?.length
    ? s.mask_versions
    : s.mask
      ? [{ asset: s.mask, label: "Original mask" }]
      : [];
  const versionKey = JSON.stringify([
    versions.map((v) => [v.asset, v.label]),
    s.mask,
  ]);
  if ($("#mask-version").dataset.key !== versionKey) {
    $("#mask-version").dataset.key = versionKey;
    $("#mask-version").innerHTML = versions
      .map(
        (v) =>
          `<option value="${v.asset}" ${v.asset === s.mask ? "selected" : ""}>${esc(v.label)}${v.created ? " · " + esc(new Date(v.created * 1000).toLocaleTimeString()) : ""}</option>`,
      )
      .join("");
  }
  if (e.loadedMask !== s.mask && !e.loading) maskLoadFrame();
  if ($("#mask-review-state"))
    $("#mask-review-state").textContent =
      s.mask && !maskPointDraft(e) && !pending.length && !s.mask_empty_frames?.length ? "Ready" : "Needs correction";
  updateRenderHistory();
}
async function runMaskCorrection(mode) {
  const e = maskEditState,
    cid = S.clip,
    sid = S.shot,
    frame = e.frame;
  e.submitting = true;
  updateMaskEditorStatus();
  try {
    await saveMaskEdits();
    await api("jobs", "POST", {
      kind: "mask_edit",
      clip_id: cid,
      shot_id: sid,
      worker: S.worker,
      options: { mode, frame },
    });
    updateMaskEditorStatus();
    toast(
      mode === "frame" ? "Frame correction queued" : "Shot tracking queued",
    );
  } finally {
    e.submitting = false;
    updateMaskEditorStatus();
  }
}
function bindMaskEditor(c, s) {
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
      storageKey: maskKey(c, s),
      source,
      start: s.start,
      end: s.end,
      frame: previous?.key === key ? previous.frame : 0,
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
          JSON.stringify(draft.basePoints) !==
            JSON.stringify(s.mask_points || []) ||
          draft.basePadding !== s.expansion,
      });
  }
  const e = maskEditState;
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
  $("#mask-frame").oninput = (ev) => maskSeek(Number(ev.target.value));
  $("#mask-frame-number").onchange = (ev) =>
    maskSeek(Number(ev.target.value) - s.start);
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
      toast("Padding must be between 0 and 256 pixels");
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
    bindMaskEditor(clip(), shot());
  });
  $("#mask-version").onchange = guarded(async (ev) => {
    if (
      e.dirty &&
      !confirm("Discard local mask edits and load this saved mask?")
    ) {
      ev.target.value = shot().mask;
      return;
    }
    await patch({ mask_version: ev.target.value });
    localStorage.removeItem(e.storageKey);
    e.dirty = false;
    disposeMaskPlayback();
    maskEditState = null;
    S.dirty = Boolean(S.renderDraftDirty);
    bindMaskEditor(clip(), shot());
  });
  $("#track-mask").onclick = guarded(async () => {
    await saveMaskEdits();
    await patch({
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
  svg.onpointerdown = (ev) => {
    if (e.loading || e.loadError || e.player?.busy || ev.button > 2) return;
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
    maskEdit((v) => {
      if (v.points.length >= 256)
        throw Error("Use at most 256 points per shot");
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
