const $ = (s) => document.querySelector(s),
  $$ = (s) => [...document.querySelectorAll(s)];
const S = {
  data: null,
  batch: localStorage.h3batch || null,
  clip: localStorage.h3clip || "521",
  shot: localStorage.getItem("shot"),
  tab: "anchors",
  dirty: false,
  worker: "auto",
  workers: {},
  modelOffloading: true,
};
const esc = (x) =>
  String(x ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const workerDisplayName = (id) => ({arch: 'Arch', fey: 'Fey', local: 'Local GPU'})[id] || String(id);
const imageAsset = (id, size = 960) => `api/assets/${encodeURIComponent(id)}/preview?size=${size}`;
const asset = (id, dl = false) =>
  `api/assets/${encodeURIComponent(id)}${dl ? "?download=true" : ""}`;
// Some browsers keep :focus-visible after clicking an already keyboard-focused
// slider. Track modality without blurring controls or disabling keyboard access.
document.addEventListener("pointerdown", () => {
  document.documentElement.dataset.inputMode = "pointer";
}, true);
document.addEventListener("keydown", () => {
  document.documentElement.dataset.inputMode = "keyboard";
}, true);
document.addEventListener("dragstart", (event) => {
  const target = event.target;
  if (
    target instanceof Element &&
    target.closest('button, a, img, video, canvas, svg, input[type="range"]') &&
    !target.closest('pre, code, .copyable, [contenteditable="true"]')
  ) event.preventDefault();
});
function stillStatus(img) {
  if(!img.matches?.('img[data-still-preview]'))return;
  const host=img.closest('.visual,figure,[data-anchor]');if(!host)return;
  let status=host.querySelector(':scope > .still-status');
  if(img.complete&&img.naturalWidth){status?.remove();host.classList.remove('still-loading');return;}
  if(!status){status=document.createElement('span');status.className='still-status';status.setAttribute('role','status');status.textContent='Loading preview…';host.append(status);}
  host.classList.add('still-loading');
}
new MutationObserver(records=>{
  for(const record of records)for(const node of record.addedNodes)if(node instanceof Element){
    if(node.matches('img[data-still-preview]'))stillStatus(node);
    node.querySelectorAll('img[data-still-preview]').forEach(stillStatus);
  }
}).observe(document.body,{childList:true,subtree:true});
document.addEventListener('load',event=>{if(event.target instanceof HTMLImageElement)stillStatus(event.target);},true);
document.addEventListener('error',event=>{
  const img=event.target;if(!(img instanceof HTMLImageElement)||!img.matches('[data-still-preview]'))return;
  stillStatus(img);const host=img.closest('.visual,figure,[data-anchor]'),status=host?.querySelector(':scope > .still-status');
  if(status){status.textContent='Preview failed. ';const retry=document.createElement('button');retry.type='button';retry.className='quiet';retry.textContent='Retry';retry.onclick=event=>{event.stopPropagation();status.textContent='Loading preview…';img.src=img.src;};status.append(retry);}
},true);
let toastTimer;
function toast(text) {
  $("#toast").textContent = text;
  $("#toast").style.display = "block";
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => ($("#toast").style.display = "none"), 7000);
}
const submissions = new Map(), acknowledgedJobs = new Map();
function mergeJob(job) {
  if (!S.data || !job) return;
  S.data.jobs = [job, ...S.data.jobs.filter(j => j.id !== job.id)];
  sidebar(); syncWorkspace(); updateRenderHistory(); updateQueueDialog();
}
async function api(path, method = "GET", body, signal) {
  let pending;
  if (path === 'jobs' && method === 'POST' && S.data) {
    const c = S.data.clips.find(c => c.id === body.clip_id), sh = c?.shots.find(s => s.id === body.shot_id);
    pending = {id:'submitting-'+crypto.randomUUID(), kind:body.kind, clip_id:body.clip_id, shot_id:body.shot_id,
      state:'submitting', created:Date.now()/1000, data:{phase:'Submitting…', worker:body.worker, options:body.options,
      shot:sh?structuredClone({seed:sh.seed,prompt:sh.prompt,mask_points:sh.mask_points,refine:sh.refine}):{},
      render_spec:body.kind==='render'&&sh?{render_mp:sh.render_mp??1,dimensions:renderDimensions(c,sh.render_mp??1)}:undefined,
      generation:sh?generationSettings(sh):undefined, lora:sh?{id:sh.lora_id||'v1-1000',label:loRALabel(sh.lora_id)}:undefined}};
    submissions.set(pending.id,pending); mergeJob(pending); toast('Submitting '+body.kind.replaceAll('_',' ')+'…');
  }
  try {
  const r = await fetch("api/" + path, {
    method,
    signal,
    headers:
      body instanceof FormData ? {} : { "Content-Type": "application/json" },
    body:
      body === undefined
        ? undefined
        : body instanceof FormData
          ? body
          : JSON.stringify(body),
  });
  const x = await r.json();
  if (!r.ok){const error=Error(x.detail || r.statusText);error.status=r.status;throw error;}
  if (pending) {
    submissions.delete(pending.id);
    S.data.jobs = S.data.jobs.filter(j=>j.id!==pending.id);
    const acknowledged=x.job || {...pending,id:x.id,state:'queued',data:{...pending.data,phase:'Queued'}};
    acknowledgedJobs.set(acknowledged.id,acknowledged);mergeJob(acknowledged);
    toast(body.kind.replaceAll('_',' ')+' queued');
  }
  if(method==='POST'&&/^jobs\/[^/]+\/cancel$/.test(path)&&x.id){acknowledgedJobs.set(x.id,x);mergeJob(x);}
  return x;
  } catch(error) {
    if(pending){submissions.delete(pending.id);S.data.jobs=S.data.jobs.filter(j=>j.id!==pending.id);syncWorkspace();updateRenderHistory();updateQueueDialog();}
    throw error;
  }
}
function buttonBusy(button, label) {
  if (!(button instanceof HTMLButtonElement)) return () => {};
  const text=button.textContent,disabled=button.disabled,visual=!!button.querySelector('img,svg');
  button.disabled=true;button.setAttribute('aria-busy','true');button.classList.add('action-busy');
  if(!visual)button.textContent=label || (/queue|generate|track|transcribe|detect|retry/i.test(text)?'Submitting…':/save|use|approve|keep|maybe|skip/i.test(text)?'Saving…':/stop|cancel/i.test(text)?'Stopping…':/import/i.test(text)?'Importing…':'Loading…');
  const busyText=button.textContent;
  return ()=>{button.removeAttribute('aria-busy');button.classList.remove('action-busy');
    if(visual){button.disabled=disabled;}else if(button.textContent===busyText){button.textContent=text;button.disabled=disabled;}
  };
}
function guarded(fn) {
  return async (...args) => {
    const button=args[0]?.currentTarget;
    if(button?.getAttribute?.('aria-busy')==='true')return;
    const finish=buttonBusy(button);
    try { return await fn(...args); }
    catch(error){toast(error.message);console.error(error);}
    finally {finish();}
  };
}
function clip() {
  return S.data?.clips.find((c) => c.id === S.clip);
}
function shot() {
  const c = clip();
  return c?.shots.find((s) => s.id === S.shot) || c?.shots.at(-1);
}
function dialog(title, html) {
  maskPause();
  refineEditor?.pause?.();
  reviewPlayer?.pause?.();
  $("#review-draft-video")?.pause();
  disposeRenderComparison();
  $("#dialog-title").textContent = title;
  $("#dialog-body").innerHTML = html;
  $("#dialog").showModal();
}
$("#dialog-close").onclick = () => $("#dialog").close();
$("#dialog").addEventListener("click", (e) => {
  if (e.target == $("#dialog")) $("#dialog").close();
});
$("#dialog").addEventListener("close", () => {
  disposeRenderComparison();
  S.openRender = null;
  $("#dialog video")?.pause();
  $("#dialog-body").innerHTML = "";
});
async function refresh(render = true) {
  const next = await api("state");
  for(const [id,job] of acknowledgedJobs){
    const server=next.jobs.find(j=>j.id===id);
    if(server&&(server.updated??server.created)>=(job.updated??job.created))acknowledgedJobs.delete(id);
    else next.jobs=[job,...next.jobs.filter(j=>j.id!==id)];
  }
  if(S.data)next.jobs=next.jobs.map(j=>{const old=S.data.jobs.find(x=>x.id===j.id);return old&&old.updated>j.updated?old:j;});
  next.jobs = [...submissions.values(), ...next.jobs];
  if (S.data)
    next.clips = next.clips.map((c) => {
      const old = S.data.clips.find((x) => x.id === c.id);
      return old && old.revision > c.revision ? old : c;
    });
  S.data = next;
  if (!next.workers.includes(S.worker) && !(S.worker==='auto'&&next.unified_workers)) S.worker = next.workers[0] || null;
  if (!S.data.batches.some((b) => b.id === S.batch))
    S.batch = S.data.batches[0]?.id;
  if (!S.data.clips.some((c) => c.id === S.clip && c.batch_id === S.batch))
    S.clip = S.data.clips.find((c) => c.batch_id === S.batch)?.id;
  sidebar();
  if (render) draw();
}
// Keep unchanged controls, videos and canvases mounted while their neighbours update.
const mountedMarkup = new WeakMap();
function reconcileItems(host, items) {
  const current = new Map([...host.children].filter(node=>node.dataset.uiKey).map(node => [node.dataset.uiKey, node]));
  const unkeyed=[...host.children].filter(node=>!node.dataset.uiKey);
  let next = host.firstElementChild;
  for (const {key, html} of items) {
    let node = current.get(key);
    if (!node || mountedMarkup.get(node) !== html) {
      const template = document.createElement('template');
      template.innerHTML = html.trim();
      const replacement = template.content.firstElementChild;
      replacement.dataset.uiKey = key;
      mountedMarkup.set(replacement, html);
      if (node) {
        if (node === next) next = replacement;
        node.replaceWith(replacement);
      }
      node = replacement;
    }
    current.delete(key);
    if (node !== next) host.insertBefore(node, next);
    next = node.nextElementSibling;
  }
  for (const node of current.values()) node.remove();
  for (const node of unkeyed) node.remove();
}
function sidebar() {
  const list = S.data.clips.filter((c) => c.batch_id === S.batch);
  const batchOptions = S.data.batches
    .map(
      (b) =>
        `<option value="${b.id}" ${b.id === S.batch ? "selected" : ""}>${esc(b.name)}</option>`,
    )
    .join("");
  if ($("#batch").dataset.options !== batchOptions) {
    $("#batch").innerHTML = batchOptions;
    $("#batch").dataset.options = batchOptions;
  }
  $("#clip-count").textContent = list.length;
  const query = $("#search").value.toLowerCase();
  reconcileItems($("#clips"), list
    .filter((c) => c.name.toLowerCase().includes(query))
    .map(c => ({key:c.id,html:clipTreeHtml(c)})));
  bindShotLinks();
  $$("[data-clip]").forEach(
    (b) =>
      (b.onclick = async () => {
        if (!(await prepareWorkspaceLeave()))
          return;
        S.clip = b.dataset.clip;
        S.tab = "shots";
        S.collapsedClips?.delete(S.clip);
        S.shot = null;
        S.dirty = false;
        localStorage.h3clip = S.clip;
        sidebar();
        draw();
      }),
  );
  const n = S.data.jobs.filter((j) =>
    ["submitting", "queued", "running", "waiting", "cancelling"].includes(j.state),
  ).length;
  $("#queue-count").textContent = n;
  $$('[data-worker-queue]').forEach(b=>{const count=S.data.jobs.filter(j=>gpuJob(j)&&j.data.worker===b.dataset.workerQueue&&['submitting','queued','running','waiting','cancelling'].includes(j.state)).length;b.textContent=b.textContent.replace(/· \d+ jobs$/,`· ${count} jobs`)});
}
$("#search").oninput = sidebar;
$("#batch").onchange = async (e) => {
  if (!(await prepareWorkspaceLeave())) {
    e.target.value = S.batch;
    return;
  }
  S.batch = e.target.value;
  S.clip = null;
  S.shot = null;
  S.dirty = false;
  localStorage.h3batch = S.batch;
  S.clip=S.data.clips.find(c=>c.batch_id===S.batch)?.id;
  sidebar(); draw();
};
let writeQueue = Promise.resolve();
function patch(changes, scope = "shot", expected = null, target = null) {
  const clipId = target?.clipId || clip().id,
    shotId = target?.shotId || shot()?.id;
  const pending = writeQueue.then(async () => {
    const c = S.data.clips.find((c) => c.id === clipId);
    if (expected) {
      const target=scope==='shot'?c.shots.find(s=>s.id===shotId):c;
      for(const [key,value] of Object.entries(expected)) {
        if(JSON.stringify(target[key] ?? (key==='mask_points'?[]:null))!==JSON.stringify(value))
          throw Error('Saved settings changed elsewhere. Your unsaved edits have been preserved.');
      }
    }

    const x = await api(
      `clips/${clipId}${scope === "shot" ? "/shots/" + shotId : ""}`,
      "PATCH",
      { revision: c.revision, changes },
    );
    S.data.clips[S.data.clips.findIndex((c) => c.id === x.id)] = x;
    return x;
  });
  writeQueue = pending.catch(() => {});
  return pending;
}

function imagePanel(title, id, cls = "", footer = "") {
  return `<section class="panel ${cls}"><div class="panel-title">${title}</div><div class="visual ${id ? "" : "empty"}" data-asset="${esc(id||'')}">${id ? `<img src="${imageAsset(id)}" alt="${title}" decoding="async" data-still-preview>` : "No image yet"}</div>${footer ? `<div class="panel-footer">${footer}</div>` : ""}</section>`;
}
function syncImagePanel(panel,id,title) {
  const visual=panel?.querySelector('.visual');
  if(!visual||visual.dataset.asset===(id||''))return;
  visual.dataset.asset=id||'';
  visual.classList.toggle('empty',!id);
  visual.innerHTML=id?`<img src="${imageAsset(id)}" alt="${esc(title)}" decoding="async" data-still-preview>`:'No image yet';
}
function syncEditableField(selector,value) {
  const field=$(selector);if(!field)return false;
  if(shotAutosave?.pending && shotAutosave.fields.some(item=>item.field===field))return field.dataset.conflict==='true';
  const remote=String(value??''),current=field.type==='checkbox'?String(field.checked):field.value,previous=field.dataset.remoteValue??current;
  if(remote!==previous){
    if(current===previous){if(field.type==='checkbox')field.checked=remote==='true';else field.value=remote;}
    else if(current!==remote)field.dataset.conflict='true';
    field.dataset.remoteValue=remote;
    delete field.dataset.missingRemote;
  }
  if((field.type==='checkbox'?String(field.checked):field.value)===remote)delete field.dataset.conflict;
  return field.dataset.conflict==='true';
}
function fieldsConflict(selectors) {
  return selectors.some(selector=>{
    const field=$(selector);
    return field?.dataset.conflict==='true'&&(field.type==='checkbox'?String(field.checked):field.value)!==field.dataset.remoteValue;
  });
}
function shotButtonHtml(s,x,i) {
  return `<button class="shotbtn ${x.id === s.id ? "active" : ""}" data-ui-key="${esc(x.id)}" data-shot="${esc(x.id)}"><strong>Shot ${i + 1} ${x.mode === "preserve" ? "· source" : ""}</strong><small>Frames ${x.start}–${x.end - 1} · ${((x.end - x.start) / 24).toFixed(2)}s</small></button>`;
}
function shotsBar(c, s) {
  return `<div class="shotbar">${c.shots.map((x, i) => shotButtonHtml(s,x,i)).join("")}</div>`;
}
function workerSelect() {
  const auto=S.data.auto_placement_supported??S.data.unified_workers;
  return `<select id="worker" aria-label="Render worker">${auto||S.worker==='auto'?`<option value="auto" ${S.worker==='auto'?'selected':''} ${auto?'':'disabled'}>Auto · ${auto?'prefer warm worker':'waiting for worker support'}</option>`:''}${S.data.workers.map(worker=>`<option value="${esc(worker)}" ${S.worker===worker?'selected':''}>${esc(worker)}</option>`).join('')}</select>`;
}
function syncWorkerChoices() {
  ['worker','batch-worker'].forEach(id=>{
    const select=document.getElementById(id);if(!select)return;
    const previous=select.value,option=select.querySelector('option[value="auto"]');
    const auto=S.data.auto_placement_supported??S.data.unified_workers;
    if(auto&&!option){select.insertBefore(new Option('Auto · prefer warm worker','auto'),select.firstChild);select.value=previous;}
    if(option){option.disabled=!auto;option.textContent=auto?'Auto · prefer warm worker':'Auto · waiting for worker support';if(!auto&&previous!=='auto')option.remove();}
  });
}
function viewBinding(c,s) {
  return JSON.stringify([c.id,c.source,c.render_source,c.frames,c.dimensions,s.id,s.start,s.end,S.tab==='refine'?s.selected_render:null]);
}
function syncWorkspace() {
  syncWorkerChoices();
  if(S.tab==='batch'){updateBatch();return;}
  const c=clip(),s=shot(),main=$('#main');
  if(!c||!s){if(main.dataset.viewClip)draw();return;}
  const binding=viewBinding(c,s);
  if(main.dataset.viewClip!==c.id||main.dataset.viewTab!==S.tab||main.dataset.viewShot!==s.id||main.dataset.viewBinding!==binding){
    if(S.dirty||maskHasDraft())return;
    draw();return;
  }
  const title=main.querySelector(':scope > .title-row');
  if(title){
    title.querySelector('h1').textContent=c.name;
    const badge=title.querySelector('.badge');
    badge.textContent=c.result?'Review ready':'In progress';
    badge.classList.toggle('pending',c.review!=='keep');
  }
  const bar=main.querySelector(':scope > .shotbar');
  if(bar){
    reconcileItems(bar,c.shots.map((x,i)=>({key:x.id,html:shotButtonHtml(s,x,i)})));
    bar.querySelectorAll('[data-shot]').forEach(b=>b.onclick=async()=>{
      if(!(await prepareWorkspaceLeave()))return;
      S.dirty=false;S.shot=b.dataset.shot;draw();
    });
  }
  if(S.tab==='anchors')syncAnchors(c,s);
  if(S.tab==='render')syncRenderFields(s);
  if(S.tab==='review')syncReview(c);
}
function draw() {
  if(!["shots","anchors","render","review"].includes(S.tab))S.tab="shots";
  sidebar();
  disposeMaskPlayback();
  disposeReviewPlayer();
  disposeRefineEditor();
  disposeShotEditor();
  if (S.tab === "batch") {
    drawBatch();
    return;
  }
  const c = clip(),
    s = shot();
  if (!c) {
    $("#main").dataset.viewClip='';
    $("#main").innerHTML =
      '<div class="empty">Import clips to start a batch.</div>';
    return;
  }
  S.shot = s.id;
  $('#main').dataset.viewClip=c.id;
  $('#main').dataset.viewShot=s.id;
  $('#main').dataset.viewTab=S.tab;
  $('#main').dataset.viewBinding=viewBinding(c,s);
  $("#main").innerHTML =
    `<nav class="tabs" aria-label="Clip sections">${(shotPage()?["anchors","render"]:["shots","review"]).map((t) => `<button data-tab="${t}" aria-current="${(S.tab === t) ? "page" : "false"}" class="${(S.tab === t) ? "active" : ""}">${t[0].toUpperCase() + t.slice(1)}</button>`).join("")}</nav><div class="title-row"><div><h1>${esc(c.name)}</h1><div class="sub">${c.frames} frames · ${c.fps} fps · ${(c.frames / 24).toFixed(2)}s · ${c.width} × ${c.height}</div></div><span class="badge ${c.review === "keep" ? "" : "pending"}">${c.review === "keep" ? "Kept" : c.result ? "Review ready" : "In progress"}</span></div><div id="workspace"></div>`;
  $('#main > .shotbar')?.querySelectorAll('[data-ui-key]').forEach((node,i)=>mountedMarkup.set(node,shotButtonHtml(s,c.shots[i],i)));
  $$("[data-tab]").forEach(
    (b) =>
      (b.onclick = async () => {
        if (!(await prepareWorkspaceLeave()))
          return;
        S.dirty = false;
        S.tab = b.dataset.tab;
        draw();
      }),
  );
  $$("[data-shot]").forEach(
    (b) =>
      (b.onclick = async () => {
        if (!(await prepareWorkspaceLeave()))
          return;
        S.dirty = false;
        S.shot = b.dataset.shot;
        draw();
      }),
  );
  ({
    shots: (c) => {drawShots(c); $("#workspace").insertAdjacentHTML("beforeend",shotCardsHtml(c));bindShotLinks();},
    anchors: drawAnchors,
    render: drawRender,
    review: drawReview,

  })[S.tab](c, s);
  $$(
    "#workspace textarea,#workspace input:not([type=range]):not([type=checkbox]),#workspace select:not(#worker):not(#render-filter)",
  ).forEach((el) =>
    el.addEventListener("input", () => {
      if (el.closest(".mask-editor, .shots-editor, .refine-editor, .comparison")) return;
      S.dirty = true;
      if (S.tab === "render") S.renderDraftDirty = true;
    }),
  );
  $("#main").scrollTop=0;
  $("#main").closest(".h3-main")?.scrollTo(0,0);
  const w = $("#worker");
  if (w)
    w.onchange = (e) => {
      S.worker = e.target.value;
    };
}
function anchorVariantsHtml(s) {
  return s.anchors.map(a=>`<button data-anchor="${esc(a.asset)}" class="${a.asset===s.anchor?'selected':''}"><img src="${imageAsset(a.asset,256)}" alt="Anchor variant" loading="lazy" decoding="async" data-still-preview></button>`).join('');
}
function bindAnchorVariants() {
  $$('#anchor-variants [data-anchor]').forEach(b=>b.onclick=guarded(async()=>{
    await patch({anchor:b.dataset.anchor});
    syncAnchors(clip(),shot());
  }));
}
function activeAnchorJob(clipId, shotId) {
  return S.data.jobs.some(j=>j.kind==='anchor'&&j.clip_id===clipId&&j.shot_id===shotId&&['submitting','queued','waiting','running','cancelling'].includes(j.state));
}
function anchorInstructionsDialog(generate = false) {
  const c=clip(),s=shot(),target={clipId:c.id,shotId:s.id};
  let saved={anchor_instructions:s.anchor_instructions||'',anchor_prompt:s.anchor_prompt||''},busy=false;
  const action=generate?(s.anchor?'Regenerate anchor':'Generate anchor'):'Save instructions';
  dialog(generate?action:'Anchor instructions',
    `<div class="dialog-form"><div class="anchor-dialog-images"><figure><img src="${imageAsset(s.source_frame,640)}" alt="Source frame" decoding="async" data-still-preview><figcaption>Source frame · choose the person here</figcaption></figure>${c.reference?`<figure><img src="${imageAsset(c.reference,640)}" alt="Character reference" decoding="async" data-still-preview><figcaption>Character reference</figcaption></figure>`:''}</div><div class="field"><label for="anchor-instructions">Extra instructions for this shot</label><textarea id="anchor-instructions" rows="4" maxlength="20000" placeholder="Replace the man on the left in the striped shirt. Keep the man on the right unchanged.">${esc(saved.anchor_instructions)}</textarea></div><p class="note">Optional. Describe who to replace and any details to preserve. We keep the source pose, reference identity and scene guidance. These instructions are saved for this shot and reused by batch anchor generation. Review the same person's mask in Render.</p><details><summary>Advanced: base prompt</summary><p class="note">Leave empty to use the default guidance. Existing custom prompts are preserved; extra instructions above are added to this prompt.</p><textarea id="anchor-prompt" rows="6" maxlength="20000" aria-label="Custom anchor base prompt">${esc(saved.anchor_prompt)}</textarea></details><p id="anchor-dialog-error" class="error-text" role="alert" hidden></p><div class="row"><button id="anchor-dialog-cancel">Cancel</button><button id="anchor-prompt-save" class="primary">${action}</button></div></div>`);
  $('#anchor-dialog-cancel').onclick=()=>$('#dialog').close();
  $('#anchor-prompt-save').onclick=async()=>{
    if(busy)return;
    busy=true;
    const button=$('#anchor-prompt-save'),error=$('#anchor-dialog-error');
    const inputs=[$('#anchor-instructions'),$('#anchor-prompt')];
    button.disabled=true;error.hidden=true;
    const changes={anchor_instructions:$('#anchor-instructions').value.trim(),anchor_prompt:$('#anchor-prompt').value};
    inputs.forEach(input=>input.readOnly=true);
    try {
      if(clip()?.id!==target.clipId||shot()?.id!==target.shotId)throw Error('The selected shot changed. Reopen its anchor instructions.');
      const current=shot();
      if(Object.keys(saved).some(k=>(current[k]||'')!==saved[k]))throw Error('Anchor instructions changed elsewhere. Reopen the dialog to review the saved version. Your draft is still shown here.');
      if(generate){
        if(activeAnchorJob(c.id,s.id))throw Error('This shot already has an anchor queued or generating. Wait for it to finish before regenerating.');
        if(!clip().reference)throw Error('Choose a character reference first.');
        button.textContent='Saving & queuing…';
        await saveFields();
      }
      if(Object.keys(saved).some(k=>(shot()[k]||'')!==saved[k]))throw Error('Anchor instructions changed elsewhere. Reopen the dialog to review the saved version. Your draft is still shown here.');
      await patch(changes,'shot',null,target);
      saved=changes;
      if(generate)await queue('anchor',s.id,c.id);
      else toast('Anchor instructions saved');
      syncAnchors(clip(),shot());
      $('#dialog').close();
    } catch(e) {
      error.textContent=e.message;error.hidden=false;
    } finally {
      busy=false;button.disabled=false;button.textContent=action;
      inputs.forEach(input=>input.readOnly=false);
    }
  };
  $('#anchor-instructions').focus();
}
async function transcribeClip(){
  await flushShotAutosave();
  const resource=await api('transcription-resource');
  if(resource.model.state!=='installed'){
    dialog('Install transcription model', '<p>Transcription uses the optional Whisper Medium.en model (about 1.5 GB), running on CPU.</p><p>Install it from Resources before transcribing.</p><a href="#/resources">Open Resources</a>');return;
  }
  await queue('transcribe');
}
function updateTranscriptionStatus(c) {
  const jobs=S.data.jobs.filter(j=>j.kind==='transcribe'&&j.clip_id===c.id).sort((a,b)=>b.created-a.created);
  const busy=j=>['submitting','queued','waiting','running','cancelling'].includes(j.state);
  const job=jobs.find(busy)||jobs[0], active=job&&busy(job), failed=job?.state==='failed';
  const button=$('#transcribe'), status=$('#transcribe-status');
  if(!button||!status)return;
  button.disabled=!!active;
  uiButton(button,active?'Transcribing…':failed?'Retry transcription':'Transcribe clip', 'audio');
  status.hidden=!job || job.state==='success';
  status.className=failed?'error-text':'note';
  status.textContent=!job?'':failed?`Transcription failed: ${job.data.error||'Unknown error'}`:
    active?(job.data.phase||'Queued'):job.state==='success'?'Transcription complete':`Transcription ${job.state}`;
}
function syncAnchors(c,s) {
  if(S.tab!=='anchors'||!$('#anchor-variants'))return;
  updateTranscriptionStatus(c);
  const panels=$$('.triptych > .panel');
  syncImagePanel(panels[0],s.source_frame,'Source frame');
  syncImagePanel(panels[1],c.reference,'Character reference');
  syncImagePanel(panels[2],s.anchor,'Shot anchor');
  const variants=$('#anchor-variants'),markup=anchorVariantsHtml(s);
  variants.hidden=s.anchors.length<=1;
  if(variants.dataset.markup!==markup){variants.innerHTML=markup;variants.dataset.markup=markup;bindAnchorVariants();}

}
function drawAnchors(c, s) {
  $("#workspace").innerHTML =
    `<div class="triptych">${imagePanel("Source frame", s.source_frame, "", `<span class="h3-numeric">Frame ${s.start}</span><a class="h3-icon-link" href="${asset(s.source_frame, true)}">${uiIcon("download")}Download</a>`)}${imagePanel("Character reference", c.reference, "", `<button class="quiet" id="reference-upload">${uiIcon("image")}Change image</button><button class="quiet" id="character-edit">${uiIcon("text")}Description</button>`)}${imagePanel("Shot anchor · optional", s.anchor, "anchor-panel", `<button id="anchor-chat-prompt" class="quiet">${uiIcon("text")}Anchor prompt</button><button id="anchor-upload" class="quiet">${uiIcon("upload")}Upload</button>`)}</div><div id="anchor-variants" class="variants" ${s.anchors.length > 1 ? "" : "hidden"}>${anchorVariantsHtml(s)}</div><div class="anchor-actions">${shotAutosaveHtml()}<div class="row"><button id="go-render" class="primary">Set up mask & render ${uiIcon("arrow")}</button></div></div><p id="anchor-sync-conflict" class="error-text" role="alert" hidden>A field you edited also changed elsewhere. Reload saved settings before saving.</p>`;
  $('#anchor-variants').dataset.markup=anchorVariantsHtml(s);
  $("#reference-upload").onclick = () => uploadImage("reference");
  $("#anchor-upload").onclick = () => uploadImage("anchor");
  $("#anchor-chat-prompt").onclick = showAnchorChatPrompt;
  $("#go-render").onclick = guarded(async () => {
    await saveFields();
    S.tab = "render";
    draw();
  });
  $("#character-edit").onclick = () => {
    dialog(
      "Character description",
      `<div class="dialog-form"><textarea id="character" rows="5">${esc(clip().character)}</textarea><button id="character-save" class="primary">Save description</button></div>`,
    );
    $("#character-save").onclick = guarded(async () => {
      await patch({ character: $("#character").value }, "clip");
      $("#dialog").close();
      toast("Description saved");
    });
  };
  bindShotAutosave([]);
  bindAnchorVariants();
}
async function saveFields() { await flushShotAutosave(); }
async function queue(kind, sid, clipId = clip().id, expectedRender = undefined, worker = S.worker, options = undefined) {
  const r = await api("jobs", "POST", {
    kind,
    clip_id: clipId,
    shot_id: sid,
    worker,
    ...(expectedRender?{expected_render:expectedRender}:{}),
    ...(options?{options}:{}),
  });
  syncWorkspace(); updateRenderHistory(); updateQueueDialog();
  toast(`${kind[0].toUpperCase() + kind.slice(1)} queued`);
  return r;
}
function renderEntries(c, s) {
  const saved = s.renders.map((r, index) => ({
    ...r,
    state: "success",
    order: r.created || index,
  }));
  const ids = new Set(saved.map((r) => r.id));
  const jobs = S.data.jobs
    .filter(
      (j) =>
        j.kind === "render" &&
        j.clip_id === c.id &&
        j.shot_id === s.id &&
        !ids.has(j.id),
    )
    .map((j) => ({
      id: j.id,
      state: j.state,
      seed: j.data.shot?.seed,
      prompt: j.data.shot?.prompt,
      dimensions: j.data.render_spec?.dimensions,
      render_mp: j.data.render_spec?.render_mp,
      created: j.created,
      order: j.created,
      phase: j.data.phase,
      error: j.data.error,
      worker: j.data.worker,
      lora: j.data.lora,
      ...j.data.generation,
      capacity_failure:j.data.capacity_failure,
      offload_after:j.data.options?.offload_after,
      residency:j.data.options?.model_offloading?"offload":undefined,
      elapsed: j.data.runtime_status?.elapsed,
    }));
  let pending = S.renderSubmission;
  if (
    pending &&
    S.data.jobs.some(
      (j) =>
        j.kind === "render" &&
        j.clip_id === pending.clip_id &&
        j.shot_id === pending.shot_id &&
        j.created >= pending.order &&
        j.data.shot?.seed === pending.seed,
    )
  ) {
    S.renderSubmission = null;
    pending = null;
  }
  if (
    pending?.clip_id === c.id &&
    pending.shot_id === s.id &&
    !jobs.some((j) => ["queued", "waiting", "running"].includes(j.state))
  )
    jobs.push(pending);
  return [...saved, ...jobs].sort((a, b) => b.order - a.order);
}
function renderStatus(r) {
  return (
    {
      submitting: "Submitting…",
      queued: S.data.queue_paused ? "Queued · paused" : "Queued",
      waiting: "Waiting for worker",
      running: "Generating",
      success: "Completed",
      failed: "Failed",
      interrupted: "Needs inspection",
      cancelled: "Cancelled",
      unconfirmed: "Submission not confirmed",
    }[r.state] || r.state
  );
}
function loRALabel(id) {return {'v1-1000':'Character Swap LoRA V1','v2-1000':'Character Swap LoRA V1.5',none:'No character-swap LoRA'}[id||'v1-1000'] || 'Unknown character LoRA';}
function generationSettings(s) {return {lora_id:s.lora_id||'v1-1000',use_anchor:s.use_anchor??true,use_mask:s.use_mask??true,window_frames:s.window_frames??124,history_frames:s.history_frames??18};}
function renderLoRALabel(r) {const id=r.lora?.id??r.lora_id;return ['v1-1000','v2-1000','none'].includes(id)?loRALabel(id):r.lora?.label||loRALabel(id);}
function renderConditionLabel(r) {return [r.use_anchor===false?'Anchor off':'',r.use_mask===false?'Mask off':''].filter(Boolean).join(' · ');}

function renderCard(r, s, picking = false) {
  const done=r.state==='success'&&r.asset, favorite=isFavorite(s,r.id), busy=['submitting','queued','waiting','running'].includes(r.state);
  return `<article class="render-card ${favorite?'favorited':''}" data-render-id="${esc(r.id)}" data-state="${esc(r.state)}">
    ${done?`<button class="render-thumb" data-open-render="${esc(r.id)}" aria-label="Preview ${takeLabel(s,r)}"><img src="${asset(r.asset)}/thumbnail" alt="Generated take" loading="lazy"><span class="play-mark" aria-hidden="true">▶</span><span class="take-duration">${((s.end-s.start)/clip().fps).toFixed(1)}s</span></button>`:`<div class="render-placeholder" role="status">${busy?'<span class="render-spinner"></span>':''}<strong>${esc(renderStatus(r))}</strong><span>${esc(r.phase||'')}</span></div>`}
    <div class="render-card-body"><div class="row spread"><strong>${takeLabel(s,r)}</strong>${done?`<button class="favorite-button" data-favorite="${esc(r.id)}" aria-label="${favorite?'Unfavorite':'Favorite'} ${takeLabel(s,r)}" aria-pressed="${favorite}" title="Favorite · saved in this browser">${uiIcon("star")}</button>`:`<span class="note">${esc(renderStatus(r))}</span>`}</div>
    <div class="row spread"><span class="note">Seed ${esc(r.seed??'—')}</span>${done?`<a class="take-download" href="${asset(r.asset,true)}" aria-label="Download ${takeLabel(s,r)}">${uiIcon("download")}Download</a>`:''}</div>
    ${r.error?`<details class="render-error"><summary>Details</summary><p>${esc(r.error)}</p></details>`:''}
    ${r.capacity_failure&&!r.capacity_failure.offloading?`<button data-render-offload="${esc(r.id)}">Retry with model offloading</button>`:''}
    ${picking&&done?`<button class="take-select ${r.id===s.selected_render?'':'primary'}" data-choose-take="${esc(r.id)}" ${r.id===s.selected_render?'disabled':''}>${r.id===s.selected_render?'Current take':'Choose this take'}</button>`:''}
    ${['queued','waiting'].includes(r.state)?`<button data-cancel-render="${esc(r.id)}">Cancel queued take</button>`:''}</div></article>`;
}
function updateRenderHistory(force = false) {
  if(segmentActive())return updateSegmentHistory();
  const host = $("#render-history-cards");
  if (!host || S.tab !== "render") return;
  const c = clip(),
    s = shot(),
    entries = renderEntries(c, s);
  const filter = S.renderFilter || "all";
  const filtered = entries.filter(
    (r) =>
      filter === "all" ||
      (filter === "favorites" ? isFavorite(s,r.id) : filter === "completed"
        ? r.state === "success"
        : filter === "active"
          ? ["submitting", "queued", "waiting", "running"].includes(r.state)
          : ["failed", "interrupted", "cancelled", "unconfirmed"].includes(
              r.state,
            )),
  );
  const active = entries.find((r) =>
    ["submitting", "queued", "waiting", "running"].includes(r.state),
  );
  const queueButton = $("#queue-shot");
  if (queueButton) {
    const e=maskEditState?.key===`${c.id}:${s.id}`?maskEditState:null;
    const pending=maskPending(e?.points || s.mask_points,s);
    const useAnchor=$('#render-use-anchor')?.checked??s.use_anchor??true, useMask=$('#render-use-mask')?.checked??s.use_mask??true;
    const reason=s.mode==='preserve'?'This shot keeps the original source.':active?'This shot already has an active render.':!c.reference?'Add a character reference in Anchors before rendering.':useAnchor&&!s.anchor?'Select an anchor in Anchors before rendering.':useMask&&pending.length?'Apply the include/exclude points with Update frame or Track shot, then review coverage.':useMask&&!s.mask?'Create a person mask before rendering.':useMask&&s.mask_empty_frames?.length?'Correct missing mask frames before rendering.':useMask&&e?.paddingError?'Padding could not save. Retry saving padding above.':renderWindowPlan?.error||'';
    queueButton.disabled=!!reason;
    queueButton.title=reason;
    if($('#render-queue-reason'))$('#render-queue-reason').textContent=reason;
    uiButton(queueButton, active ? renderStatus(active) : "Generate shot", active ? "refresh" : "play");
  }
  $("#render-count").textContent = entries.length;
  reconcileItems(host, filtered.length
    ? filtered.map(r => ({key:r.id,html:renderCard(r,s)}))
    : [{key:'empty',html:`<div class="render-empty"><span class="play-mark" aria-hidden="true">▶</span><strong>${entries.length ? "No results in this view" : "Your takes will appear here"}</strong><p>${entries.length ? "Choose another filter to see your generations." : "Review the mask and queue a shot. Its progress will appear here."}</p></div>`}]);
  $$("[data-open-render]").forEach((button) => {
    button.onclick = () => openRender(button.dataset.openRender);
    button.onpointerenter = button.onfocus = () => warmRenderComparison(c, s, button.dataset.openRender);
  });
  bindFavorites(s);
  $$('[data-render-offload]').forEach(button=>button.onclick=guarded(async()=>{
    const x=await api(`jobs/${button.dataset.renderOffload}/retry-offload`,'POST');
    acknowledgedJobs.set(x.job.id,x.job);mergeJob(x.job);toast('Retry queued with model offloading; original settings preserved');
  }));
  const newest = entries.find(r => r.state === "success" && r.asset);
  if (newest) warmRenderComparison(c, s, newest.id);
  $$("[data-cancel-render]").forEach((button) => {
    button.onclick = guarded(async () => {
      await api(`jobs/${button.dataset.cancelRender}/cancel`, "POST");
      toast("Queued render cancelled");
      updateRenderHistory();
    });
  });
}
async function openRender(id, contextShot = shot(), returnTo = null) {
  const c = clip(),
    s = contextShot,
    r = s.renders.find((r) => r.id === id);
  if (!r) return;
  S.openRender = id;
  dialog(
    `${takeLabel(s,r)} · Seed ${r.seed}`,
    `<div class="render-detail">${comparisonHtml(c, s, r)}<div class="row spread"><span class="note">${r.imported ? "Imported take" : "Generated "+new Date(r.created * 1000).toLocaleString()}</span><div class="row">${returnTo?'<button id="return-to-takes">← Choose a take</button>':''}<button id="modal-favorite" aria-pressed="${isFavorite(s,r.id)}">${uiIcon("star")}${isFavorite(s,r.id)?'Favorited':'Favorite'}</button><a href="${asset(r.asset, true)}">Download video</a>${r.api ? `<a href="${asset(r.api, true)}">Workflow JSON</a>` : ""}</div></div><details><summary>Saved render settings</summary><div id="render-modal-settings" class="stack"><p class="note">Loading saved settings…</p></div></details></div>`,
  );
  bindRenderComparison(c, s, r);
  $('#modal-favorite').onclick=()=>{toggleFavorite(s,r.id);uiButton($('#modal-favorite'),isFavorite(s,r.id)?'Favorited':'Favorite','star');$('#modal-favorite').setAttribute('aria-pressed',String(isFavorite(s,r.id)));updateRenderHistory();};
  if(returnTo)$('#return-to-takes').onclick=returnTo;
  try {
    const saved = await api(
      `clips/${c.id}/shots/${s.id}/renders/${encodeURIComponent(id)}`,
    );
    const host = $("#render-modal-settings");
    if (!host || S.openRender !== id) return;
    if(saved.segment_chain){
      const items=saved.settings?.segments||[];
      host.innerHTML=`<p>${saved.dimensions.join(' × ')} · assembled from ${items.length} selected segments</p><p class="label">Shared instructions</p><pre class="saved-prompt">${esc(saved.prompt)}</pre>${items.map((seg,i)=>`<details><summary>Segment ${i+1} · ${segmentTime(seg.start)}–${segmentTime(seg.end)}</summary><p class="note">Seed ${seg.seed} · ${seg.connection==='continue'?seg.history_frames+' history frames':'Fresh start'}</p><pre class="saved-prompt">${esc(seg.prompt||'No additional performance directions.')}</pre></details>`).join('')}`;
      return;
    }
    host.innerHTML = `${saved.dimensions?`<p>${saved.dimensions.join(" × ")} · ${(saved.actual_mp??saved.dimensions[0]*saved.dimensions[1]/1000000).toFixed(3)} MP actual${saved.render_mp?` / ${saved.render_mp} MP budget`:''}</p>`:""}<p>Seed <strong>${esc(saved.seed)}</strong></p><p class="note">Diffusion window ${((saved.window_frames??124)/24).toFixed(2)}s · history ${saved.history_frames??18} frames</p><pre class="saved-prompt">${esc(saved.prompt || "Prompt unavailable for this imported render")}</pre><button id="reuse-render-settings" ${saved.prompt === undefined ? "disabled" : ""}>Copy settings to next render</button>`;
    $("#reuse-render-settings").onclick = guarded(async () => {
      await flushShotAutosave();
      await patch({ seed: saved.seed, prompt: saved.prompt, render_mp: saved.render_mp??1, ...generationSettings(saved) },"shot",null,{clipId:c.id,shotId:s.id});
      S.dirty = false;
      $("#dialog").close();
      draw();
      toast("Render settings copied. Your selected render is unchanged.");
    });
  } catch (error) {
    if ($("#render-modal-settings"))
      $("#render-modal-settings").textContent = error.message;
  }
}
async function queueRender(c, s) {
  if(S.renderSubmission?.state==='submitting'){toast('A render is still being submitted. Wait for its acknowledgement.');return;}
  const requestedWorker=S.worker;
  const mp = Number($('#render-mp').value);
  if (!$('#render-mp').value || !Number.isFinite(mp) || mp < 0.1 || mp > 1) {
    toast('Choose a render size between 0.1 and 1.0 MP');
    return;
  }
  S.renderFilter = "all";
  $("#render-filter").value = "all";
  S.renderSubmission = {
    id: "submitting",
    clip_id: c.id,
    shot_id: s.id,
    state: "submitting",
    seed: Number($("#render-seed").value),
    dimensions: renderDimensions(c, $("#render-mp").value),
    render_mp: Number($("#render-mp").value),
    ...readGenerationControls(),
    model_offloading: S.modelOffloading,
    worker: requestedWorker,
    order: Date.now() / 1000,
    phase: "Saving settings and submitting shot",
  };
  updateRenderHistory();
  const submission={...S.renderSubmission};
  try {
    const submitted=await saveRenderSettings();
    await queue("render", s.id, c.id, submitted, requestedWorker, {model_offloading:submission.model_offloading});
    S.renderSubmission = null;
  } catch (error) {
    try {
      await refresh(false);
    } catch (_) {
      /* Keep uncertainty visible until a successful refresh. */
    }
    S.renderSubmission = {
      ...submission,
      state: error.status?"failed":"unconfirmed",
      phase: error.status?"Submission rejected":"Check the job queue before retrying",
      error: error.message,
    };
    toast(error.message);
  }
  updateRenderHistory();
}
function renderDimensions(c, mp = 1) {
  if (Number(mp) === 1) return [...c.dimensions];
  const budget = Math.floor(Number(mp) * 1000000), ratio = c.dimensions[0] / c.dimensions[1];
  const iw = Math.sqrt(budget * ratio), ih = Math.sqrt(budget / ratio);
  let best;
  for (let w = 32; w <= Math.min(16384, Math.floor(budget / 32)); w += 32) {
    const top = Math.min(16384, Math.floor(budget / w / 32) * 32);
    for (const h of new Set([Math.max(32, Math.min(top, Math.floor(ih / 32) * 32)), Math.max(32, Math.min(top, Math.ceil(ih / 32) * 32))])) {
      const score = Math.log(w / iw) ** 2 + Math.log(h / ih) ** 2;
      if (!best || score < best.score || (score === best.score && w * h > best.w * best.h)) best = {score, w, h};
    }
  }
  return [best.w, best.h];
}
function syncRenderSize() {
  const control = $('#render-mp'), output = $('#render-size-output');
  if (!control || !output) return;
  const mp = Number(control.value);
  if (!control.value || !Number.isFinite(mp) || mp < 0.1 || mp > 1) {
    output.textContent = 'Choose 0.1–1.0 MP';
    return;
  }
  const dims = renderDimensions(clip(), control.value);
  output.textContent = `${dims.join(' × ')} · 24 fps`;
  output.title = `${(dims[0] * dims[1] / 1000000).toFixed(3)} MP on H3’s 32-pixel grid`;
}
let renderWindowPlan = null;
function windowSeconds(frames) { return (frames/24).toFixed(2).replace(/0$/, '').replace(/\.$/, ''); }
function renderWindowControls(s) {
  return `<div class="render-window-controls"><div class="field"><label for="render-window">Diffusion window</label><select id="render-window" aria-describedby="render-window-help"><option value="${s.window_frames??124}">${windowSeconds(s.window_frames??124)} seconds</option></select></div><p id="render-window-help" class="note">Maximum length per pass. Longer windows use more memory.</p><p id="render-window-plan" class="note" role="status">Calculating windows…</p><div class="field"><label for="render-history">Continuation history</label><select id="render-history" aria-describedby="render-history-help"><option value="${s.history_frames??18}">${windowSeconds(s.history_frames??18)} seconds</option></select></div><p id="render-history-help" class="note">Previous generated frames carried into the next window.</p></div>`;
}
async function syncRenderWindowPlan() {
  const control=$('#render-window'), history=$('#render-history'), output=$('#render-window-plan'),summary=$('#render-window-summary');
  if(!control||!history||!output)return;
  const c=clip(),s=shot(),windowFrames=Number(control.value),historyFrames=Number(history.value);
  const key=JSON.stringify([c.id,s.id,s.start,s.end,windowFrames,historyFrames]);
  if(renderWindowPlan?.key===key&&renderWindowPlan.output===output)return;
  renderWindowPlan?.abort.abort();
  const state=renderWindowPlan={key,output,abort:new AbortController(),error:null};
  output.textContent='Calculating windows…';summary.textContent='';
  try {
    const plan=await api(`clips/${c.id}/shots/${s.id}/render-plan?window_frames=${windowFrames}&history_frames=${historyFrames}`,'GET',undefined,state.abort.signal);
    if(renderWindowPlan!==state||!output.isConnected)return;
    // A few useful lengths keep the dropdown short; preserve any custom saved value.
    const lengths=[...new Set([56,124,243,362,windowFrames])].filter(n=>plan.window_choices.includes(n)).sort((a,b)=>a-b);
    control.innerHTML=lengths.map(n=>`<option value="${n}" ${n===windowFrames?'selected':''} ${n<=historyFrames?'disabled':''}>${windowSeconds(n)} seconds${n===124?' · default':''}</option>`).join('');
    history.innerHTML=plan.history_choices.map(n=>`<option value="${n}" ${n===historyFrames?'selected':''} ${n>=windowFrames?'disabled':''}>${n===0?'None':n===1?'Boundary frame only':windowSeconds(n)+' seconds'+(n===18?' · default':'')}</option>`).join('');
    const count=plan.windows.length, seconds=(plan.frames/plan.fps).toFixed(1);
    summary.textContent=count===1?'Single pass':`${count} windows`;
    output.textContent=count===1?`Entire ${seconds}s shot in one diffusion pass.`:`${seconds}s shot → ${count} sequential windows, up to ${windowSeconds(windowFrames)}s each. Output stays one continuous shot.`;
    history.disabled=count===1;
    $('#render-history-help').textContent=count===1?'History is only used when a shot spans multiple windows.':historyFrames===0?'Each window starts without generated continuation history.':'Previous generated frames carried into the next window.';
    state.plan=plan;
  } catch(error) {
    if(renderWindowPlan!==state||state.abort.signal.aborted||!output.isConnected)return;
    state.error=error.message;output.textContent=error.message;
  }
  updateRenderHistory();
}
const renderFields=['#render-seed','#render-prompt','#render-mp','#render-lora','#render-use-anchor','#render-use-mask','#render-window','#render-history'];
function readGenerationControls(){return {lora_id:$('#render-lora').value,use_anchor:$('#render-use-anchor').checked,use_mask:$('#render-use-mask').checked,window_frames:Number($('#render-window').value),history_frames:Number($('#render-history').value)};}
function syncRenderFields(s) {
  if(segmentActive())return syncSegmentWorkspace(s);
  if(S.tab!=='render'||!$('#render-seed'))return;
  const conflicts=[syncEditableField('#render-seed',s.seed),syncEditableField('#render-prompt',s.prompt),syncEditableField('#render-mp',s.render_mp??1),syncEditableField('#render-lora',s.lora_id||'v1-1000'),syncEditableField('#render-use-anchor',s.use_anchor??true),syncEditableField('#render-use-mask',s.use_mask??true),syncEditableField('#render-window',s.window_frames??124),syncEditableField('#render-history',s.history_frames??18)];
  $('#render-sync-conflict').hidden=!conflicts.some(Boolean);
  syncRenderSize();
  syncRenderWindowPlan();
}
async function saveRenderSettings() {
  await flushShotAutosave();
  const changes = {seed:Number($('#render-seed').value),prompt:$('#render-prompt').value,render_mp:Number($('#render-mp').value),...readGenerationControls()};
  if(changes.use_mask)await saveMaskEdits();
  return changes;
}
function drawLegacyRender(c, s) {
  S.renderDraftDirty = false;
  $("#workspace").innerHTML =
    `<div class="render-layout"><section class="panel render-controls"><div class="panel-title">Actor mask <span class="note h3-coverage" id="mask-coverage"></span></div>${maskEditorHtml(c, s)}</section><section class="panel generation-panel"><div class="panel-title">Generation</div><div class="panel-body stack"><div class="render-generation"><strong class="generation-model">MiniMax H3 · ${esc(loRALabel(s.lora_id))} · Turbo 8</strong><div class="row"><div class="field"><label for="worker">Worker</label>${workerSelect()}</div><div class="field"><label for="render-seed">Seed</label><div class="h3-seed-control"><input id="render-seed" type="number" min="0" value="${s.seed}"><button id="render-seed-random" class="h3-icon-button" aria-label="Randomize seed" title="Randomize seed">${uiIcon("refresh")}</button></div></div></div><div class="field"><label for="render-mp">Resolution (MP)</label><select id="render-mp" aria-label="Resolution (MP)">${[0.5,0.6,0.7,0.8,0.9,1].map(mp=>`<option value="${mp}" ${Number(s.render_mp??1)===mp?'selected':''}>${mp.toFixed(1)}</option>`).join('')}${![0.5,0.6,0.7,0.8,0.9,1].includes(Number(s.render_mp??1))?`<option value="${s.render_mp}" selected>${s.render_mp} (saved)</option>`:''}</select><span id="render-size-output" class="note" role="status"></span></div><p class="note">Fits the shot to H3’s 32-pixel grid. Final clip canvas: ${c.dimensions.join(" × ")}.</p><span id="mask-review-state" class="h3-sr-only">${s.mask ? "Available" : "Missing"}</span><input id="render-lora" type="hidden" value="${esc(s.lora_id||'v1-1000')}"><label class="conditioning-toggle"><input id="render-use-anchor" type="checkbox" ${s.anchor && s.use_anchor!==false?'checked':''} ${s.anchor?'':'disabled'}> Use shot anchor</label><label class="conditioning-toggle"><input id="render-use-mask" type="checkbox" ${s.use_mask===false?'':'checked'}> Use mask</label><p class="note">Turning masking off regenerates the whole frame. Source video and audio guides stay active. With the anchor off, remove any Picture 2 instructions from your prompt.</p><label class="conditioning-toggle"><input id="render-model-offloading" type="checkbox" ${S.modelOffloading?'checked':''}> Model offloading</label><p class="note">Enabled by default to reduce GPU memory use. Applies to the next render.</p><details><summary>Performance prompt</summary><textarea id="render-prompt" rows="7" aria-label="Next performance prompt">${esc(optionalAnchorPrompt(s.prompt,!!s.anchor && s.use_anchor!==false))}</textarea></details><p id="render-sync-conflict" class="error-text" role="alert" hidden>Render settings changed elsewhere. Reload saved settings before saving.</p>${shotAutosaveHtml()}<div class="row render-actions"><button id="queue-shot" class="primary" ${s.mode === "preserve" ? "disabled" : ""}>Queue shot</button></div><p id="render-queue-reason" class="note" role="status"></p></div></div></section><section class="panel render-results"><div class="panel-title"><h2>Your takes <span id="render-count" class="note"></span></h2><select id="render-filter" aria-label="Filter renders"><option value="all">All takes</option><option value="favorites">Favorites</option><option value="active">In progress</option><option value="completed">Completed</option><option value="failed">Failed / cancelled</option></select></div><div id="render-history-cards" class="render-grid"></div></section></div>`;
  $('#render-seed').dataset.remoteValue=String(s.seed);
  $('#render-seed-random').onclick=()=>{
    const input=$('#render-seed');input.value=String(crypto.getRandomValues(new Uint32Array(1))[0] & 0x7fffffff);
    input.dispatchEvent(new Event('input',{bubbles:true}));
  };
  $('#render-prompt').dataset.remoteValue=s.prompt||'';
  $('#render-mp').dataset.remoteValue=String(s.render_mp??1);
  $('#render-model-offloading').onchange=event=>{S.modelOffloading=event.target.checked;};
  $('#render-lora').value=s.lora_id||'v1-1000';
  for(const selector of ['#render-lora','#render-use-anchor','#render-use-mask']){const field=$(selector);field.dataset.remoteValue=field.type==='checkbox'?String(field.checked):field.value;field.oninput=()=>{S.dirty=true;S.renderDraftDirty=true;updateRenderHistory();};}
  $('#render-mp').oninput=syncRenderSize;
  syncRenderSize();
  $("#render-filter").value = S.renderFilter || "all";
  $("#render-filter").onchange = (event) => {
    S.renderFilter = event.target.value;
    updateRenderHistory();
  };
  bindMaskEditor(c, s);
  const clear=$('#mask-clear-frame')?.parentElement, help=$('.mask-help'), advanced=$('.mask-advanced');
  if(advanced&&clear)advanced.append(clear);
  if(advanced&&help)advanced.append(help);
  const tools=$(".mask-tools");if(tools)$(".mask-editor").prepend(tools);
  const paddingRow=$('#mask-padding-number')?.parentElement;
  if(paddingRow){paddingRow.classList.add('mask-padding-row');paddingRow.insertBefore($('#mask-padding'),$('#mask-padding-number'));}
  arrangeMaskWorkspace();
  const generation=$('.render-generation'), advancedSettings=generation.querySelector('details');
  if(advancedSettings){
    advancedSettings.querySelector('summary').innerHTML='Advanced settings <span id="render-window-summary" class="note"></span>';
    advancedSettings.querySelector('summary').insertAdjacentHTML('afterend',renderWindowControls(s));
    $('#render-window').addEventListener('input',syncRenderWindowPlan);
    $('#render-history').addEventListener('input',syncRenderWindowPlan);
    syncRenderWindowPlan();
    advancedSettings.querySelector('textarea').insertAdjacentHTML('beforebegin','<label class="label" for="render-prompt">Performance prompt</label>');
    generation.querySelectorAll(':scope > p.note').forEach(note=>{if(/^(Fits the shot|Turning masking|Enabled by default)/.test(note.textContent))advancedSettings.append(note);});
  }
  bindShotAutosave([['#render-seed','seed',s.seed],['#render-prompt','prompt',s.prompt],['#render-mp','render_mp',s.render_mp??1],['#render-lora','lora_id',s.lora_id||'v1-1000'],['#render-use-anchor','use_anchor',s.use_anchor??true],['#render-use-mask','use_mask',s.use_mask??true],['#render-window','window_frames',s.window_frames??124],['#render-history','history_frames',s.history_frames??18]]);
  $("#queue-shot").onclick = () => queueRender(c, s);

  updateRenderHistory(true);
}

function reviewPairHtml(c) { return finalReviewHtml(c); }
function reviewDownloadsHtml(c) {
  return c.result?`<div class="panel"><div class="panel-title">Full-quality downloads</div><div class="panel-body row"><a class="download" href="${asset(c.result, true)}">${uiIcon("download")}Result MP4</a>${c.comparison ? `<a class="download" href="${asset(c.comparison, true)}">${uiIcon("film")}16:9 comparison</a>` : ""}${c.comparison_fit ? `<a class="download" href="${asset(c.comparison_fit, true)}">${uiIcon("film")}Fitted comparison</a>` : ""}${c.provenance ? `<a class="download" href="${asset(c.provenance, true)}">${uiIcon("text")}Provenance</a>` : ""}<a class="download" href="${asset(c.source_pair || c.source, true)}">${uiIcon("download")}Source MP4</a></div></div>`:'';
}
function bindReviewMedia(c=clip()) { bindFinalReview(c); }
function syncReview(c) {
  const pair=$('#review-pair-host'),downloads=$('#review-downloads-host');if(S.tab!=='review'||!pair)return;
  const missing=c.shots.some(s=>s.mode==='generate'&&!s.selected_render);
  const refined=c.shots.some(s=>s.selected_finish);
  $('#review-assemble').disabled=missing||refined;
  $('#review-assembly-note').textContent=refined?'This clip has a legacy refined selection. Raw assembly migration is pending.':missing?'Choose a completed take for each generated shot.':'Selected takes are ready to assemble.';
  const pairKey=reviewPreviewKey(c);
  if(pair.dataset.key!==pairKey){disposeReviewPlayer();pair.innerHTML=reviewPairHtml(c);pair.dataset.key=pairKey;bindReviewMedia(c);}else reviewPlayer?.refresh(c);
  const downloadsKey=JSON.stringify([c.result,c.source_pair,c.source,c.comparison,c.comparison_fit,c.provenance]);
  if(downloads.dataset.key!==downloadsKey){downloads.innerHTML=reviewDownloadsHtml(c);downloads.dataset.key=downloadsKey;}
  const shots=$('#assembly-shots-host'),key=JSON.stringify(c.shots.map(s=>[s.id,s.selected_render,s.renders.length,s.mode]));
  if(shots.dataset.key!==key){shots.innerHTML=assemblyShotsHtml(c);shots.dataset.key=key;bindAssemblyShots(c);}
}
function drawReview(c) {
  $('#workspace').innerHTML=`<div class="review-workspace"><div class="section-heading"><div><h2>Final clip</h2><p id="review-assembly-note" class="note"></p></div><button id="review-assemble" class="primary">${uiIcon("film")}Assemble clip</button></div><div id="review-pair-host"></div><section class="panel assembly-panel" id="assembly-shots-host"></section><div id="review-downloads-host"></div></div>`;
  $('#review-assemble').onclick=guarded(()=>queue('assemble'));
  syncReview(c);
}
function uploadImage(role) {
  const input = $("#image-upload"), target = {clipId:clip().id,shotId:shot().id};
  input.value = "";
  input.onchange = guarded(async () => {
    const file = input.files[0];
    if (!file) return;
    await flushShotAutosave();
    toast("Uploading image…");
    const upload = writeQueue.then(async () => {
      const c = S.data.clips.find(c=>c.id===target.clipId), f = new FormData();
      f.append("file", file); f.append("role", role);
      f.append("revision", c.revision); f.append("shot_id", target.shotId);
      const updated = await api(`clips/${target.clipId}/image`, "POST", f);
      S.data.clips[S.data.clips.findIndex(c=>c.id===updated.id)] = updated;
    });
    writeQueue = upload.catch(()=>{});
    await upload;
    if(role==='anchor')await patch({use_anchor:true},'shot',null,target);
    // Keep the mounted fields: edits made during upload must not be discarded.
    syncWorkspace();
    toast("Image uploaded");
  });
  input.click();
}
$("#new-batch").onclick = () => {
  dialog(
    "New batch",
    '<div class="dialog-form"><input id="batch-name" placeholder="Batch name"><button id="create-batch" class="primary">Create batch</button></div>',
  );
  $("#create-batch").onclick = guarded(async () => {
    S.batch = (
      await api("batches", "POST", { name: $("#batch-name").value })
    ).id;
    S.clip = null;
    $("#dialog").close();
    await refresh();
  });
};
$("#import").onclick = () => {
  dialog(
    "Import clips",
    `<div class="dialog-form"><p class="note">Upload videos or a folder from this device. Originals are retained in the configured media folder; working clips use 24 fps.</p><div class="row"><input id="clip-files" type="file" multiple accept="video/*"><button id="choose-folder">Choose folder</button><input id="folder-files" type="file" webkitdirectory multiple hidden></div><button id="upload-files" class="primary">Import selected files</button><hr style="border:0;border-top:1px solid #e5e8ed;width:100%"><label class="label">Or import an existing media folder</label><input id="folder-path" placeholder="Absolute folder path inside your media directory"><button id="import-path">Import media folder</button><div id="import-status" class="note"></div></div>`,
  );
  $("#choose-folder").onclick = () => $("#folder-files").click();
  $("#folder-files").onchange = () =>
    ($("#import-status").textContent =
      `${$("#folder-files").files.length} files selected`);
  async function finish(x) {
    toast(
      `${x.clips.length} clips imported${x.errors.length ? "; " + x.errors.map((e) => e.file + ": " + e.error).join(" | ") : ""}`,
    );
    $("#dialog").close();
    S.clip = x.clips[0]?.id || S.clip;
    S.dirty = false;
    await refresh();
  }
  $("#upload-files").onclick = guarded(async () => {
    if (!S.batch)
      S.batch = (await api("batches", "POST", { name: "New clips" })).id;
    const files = $("#folder-files").files.length
      ? $("#folder-files").files
      : $("#clip-files").files;
    if (!files.length) throw Error("Select video files first");
    $("#import-status").textContent = "Importing and preparing previews…";
    $("#upload-files").disabled = true;
    const f = new FormData();
    f.append("batch_id", S.batch);
    for (const file of files) f.append("files", file);
    try {
      await finish(await api("import", "POST", f));
    } finally {
      if ($("#upload-files")) $("#upload-files").disabled = false;
    }
  });
  $("#import-path").onclick = guarded(async () => {
    if (!S.batch)
      S.batch = (await api("batches", "POST", { name: "New clips" })).id;
    $("#import-status").textContent = "Importing shared files…";
    await finish(
      await api("import-folder", "POST", {
        batch_id: S.batch,
        path: $("#folder-path").value,
      }),
    );
  });
};
$("#batch-actions").onclick = () => {
  dialog(
    "Batch actions",
    `<div class="dialog-form"><p class="note">Each clip keeps separate progress. Save shot boundaries first. Renders use the saved segments, directions, references, model and masking settings.</p><select id="batch-kind"><option value="cuts">Detect edits</option><option value="transcribe">Transcribe audio</option><option value="prompts">Build shot prompts</option><option value="mask">Track missing masks</option><option value="render">Render missing shots</option><option value="assemble">Assemble clips & reports</option></select><select id="batch-worker">${(S.data.auto_placement_supported??S.data.unified_workers)?'<option value="auto">Auto · prefer warm worker</option>':''}<option value="both" selected>All configured workers</option>${S.data.workers.map(worker=>`<option value="${esc(worker)}">${esc(worker)}</option>`).join('')}</select><button id="run-batch" class="primary">Queue batch</button><div id="batch-response" class="note"></div></div>`,
  );
  $("#run-batch").onclick = guarded(async () => {
    await flushShotAutosave();
    const x = await api(`batches/${S.batch}/queue`, "POST", {
      kind: $("#batch-kind").value,
      worker: $("#batch-worker").value,
      missing_only: true,
    });
    $("#batch-response").textContent =
      `${x.jobs.length} jobs queued. ${x.errors.map((e) => e.clip + ": " + e.reason).join(" | ")}`;
    for(const job of x.job_records||[])mergeJob(job);
    toast(`${x.jobs.length} jobs queued`);
    updateQueueDialog();
  });
};
function gpuJob(j) {return ["mask","mask_edit","render","refine_extract","refine_mask","refine_face","backplate"].includes(j.kind)}
function queueJobs() {
  return S.data.jobs.filter(j=>!S.queueWorker||(j.data.worker===S.queueWorker&&gpuJob(j)))
    .sort((a,b)=>({submitting:0,running:0,cancelling:0,queued:1,waiting:1}[a.state]??2)-({running:0,cancelling:0,queued:1,waiting:1}[b.state]??2));
}
function queueJobHtml(j) {
  return `<div class="job ${j.state}" data-ui-key="${esc(j.id)}" data-job-id="${esc(j.id)}"><div class="row spread"><strong>${esc(j.kind)} · ${esc(S.data.clips.find((c) => c.id === j.clip_id)?.name || j.clip_id)}</strong><span class="state">${esc(j.state)}</span></div><p class="note">${esc(j.data.phase)} ${j.data.runtime_status?.elapsed ? `· ${Math.floor(j.data.runtime_status.elapsed)}s` : ""} ${["mask", "render"].includes(j.kind) ? "· " + esc(j.data.worker) : ""}</p>${j.data.error ? `<pre>${esc(j.data.error)}</pre>` : ""}${["queued", "waiting"].includes(j.state) || (j.state==="running" && ["mask","mask_edit","render","refine_extract","refine_mask","refine_face","backplate"].includes(j.kind)) ? `<button data-cancel="${j.id}">${j.state==="running"?"Stop running job":"Cancel queued job"}</button>` : ""}<button data-job-open="${j.id}" class="quiet">Open clip / shot</button>${j.data.result?.asset || j.data.result?.result ? `<a href="${asset(j.data.result.asset || j.data.result.result, true)}">Download output</a>` : ""}</div>`;
}
function queueHtml() {
  return (
    `<div class="row spread" style="margin-bottom:16px"><span id="queue-summary" class="note">${S.data.queue_paused ? "New jobs are paused. Active jobs continue." : "Jobs continue when you close the browser."}</span><button id="queue-pause">${S.data.queue_paused ? "Resume queue" : "Pause new jobs"}</button></div>` +
    `<div id="worker-lifecycle">${workerLifecycleHtml()}</div>` +
    `<div id="queue-list">${queueJobs().length?queueJobs().map(queueJobHtml).join(''):'<div class="empty" data-ui-key="empty">No queued jobs.</div>'}</div>`
  );
}
function workerLabel(w) {
  return ({warm_idle:`Ready · ${w.family?.label || 'models'} warm`,unloaded_idle:'Ready · models unloaded',ready:'Ready',starting:'Starting',running:'Processing',unloading:'Unloading models',stopping:'Stopping',needs_inspection:'Needs inspection',available:'Available',busy:'Busy',external_busy:'Busy · another workload',offline:'Offline'})[w.state] || w.state;
}
function workerLifecycleHtml() {
  const w=S.workers[S.queueWorker];
  if(!w?.owned)return '';
  const idle=['ready','warm_idle','unloaded_idle'].includes(w.state);
  const seconds=Math.max(0,Math.ceil((w.state==='unloaded_idle'?w.stop_seconds:w.unload_seconds)-(w.idle_seconds||0)));
  return `<div class="row spread" style="margin-bottom:16px"><span class="note">${esc(workerLabel(w))}${idle?` · ${w.state==='unloaded_idle'?'Stops':'Models unload'} after about ${Math.ceil(seconds/60)} idle minutes`:''}</span><button id="release-worker" ${idle&&S.releasingWorker!==S.queueWorker?'':'disabled'}>${S.releasingWorker===S.queueWorker?'Releasing…':'Release idle worker'}</button></div>`;
}
function updateQueueDialog() {
  if (!$('#dialog').open || !$('#queue-list')) return;
  $('#queue-summary').textContent=S.data.queue_paused?'New jobs are paused. Active jobs continue.':'Jobs continue when you close the browser.';
  $('#queue-pause').textContent=S.data.queue_paused?'Resume queue':'Pause new jobs';
  if($('#worker-lifecycle'))$('#worker-lifecycle').innerHTML=workerLifecycleHtml();
  const jobs=queueJobs();
  reconcileItems($('#queue-list'),jobs.length?jobs.map(j=>({key:j.id,html:queueJobHtml(j)})):[{key:'empty',html:'<div class="empty">No queued jobs.</div>'}]);
  bindQueue();
}
function bindQueue() {
  if($('#release-worker'))$('#release-worker').onclick=guarded(async()=>{
    const worker=S.queueWorker;S.releasingWorker=worker;updateQueueDialog();
    try {
      await api(`workers/${worker}/release`,'POST',{});
      S.workers[worker]={...S.workers[worker],state:'stopping'};
      toast('Releasing idle worker…');
    } finally {S.releasingWorker=null;updateQueueDialog()}
  });
  $$('[data-job-open]').forEach(b=>b.onclick=async()=>{
    const j=S.data.jobs.find(j=>j.id===b.dataset.jobOpen);
    if(!(await prepareWorkspaceLeave()))return;
    S.dirty=false;S.clip=j.clip_id;S.shot=j.shot_id;S.batch=S.data.clips.find(c=>c.id===j.clip_id)?.batch_id||S.batch;localStorage.h3batch=S.batch;localStorage.h3clip=S.clip;S.tab='render';
    $('#dialog').close();sidebar();draw();
  });
  if ($("#queue-pause"))
    $("#queue-pause").onclick = guarded(async () => {
      await api("queue", "POST", { paused: !S.data.queue_paused });
      await refresh(false);
      updateQueueDialog();
    });
  $$("[data-cancel]").forEach(
    (b) =>
      (b.onclick = guarded(async () => {
        const j=S.data.jobs.find(j=>j.id===b.dataset.cancel);
        if(j.state==='running'&&!confirm(j.data.gpu_waiting||['prep','cpu'].includes(j.data.execution_resource)?'Cancel this remaining processing? Completed artifacts are retained. Other worker jobs continue.':'Stop this running job and release its worker? Completed artifacts are retained.'))return;
        await api(`jobs/${b.dataset.cancel}/cancel`, "POST", {confirm_running:j.state==='running'});
        toast(j.state==='running'?'Stopping owned job…':'Queued job cancelled');
        updateQueueDialog();
      })),
  );
}
$("#queue-open").onclick = () => {
  S.queueWorker=null;dialog("Job queue", queueHtml());
  updateQueueDialog();
};


function batchCardHtml(c) {
  return `<section class="panel"><div class="panel-title">${esc(c.name)}<button data-open-review="${esc(c.id)}" class="quiet">Open →</button></div>${c.result ? `<video controls playsinline preload="metadata" class="media-wide" src="${asset(c.comparison || c.result)}"></video>` : `<div class="visual"><img src="${imageAsset(c.poster)}" alt="Source preview" loading="lazy" decoding="async" data-still-preview></div>`}<div class="panel-footer"><span>${c.result ? "Source audio · " + c.frames + " frames" : "Waiting for selected shots"}</span><span class="badge" data-review-state></span>${c.result ? `<a href="${asset(c.result, true)}">Download</a>` : ""}</div></section>`;
}
function updateBatch() {
  if(S.tab!=='batch'||!$('#batch-grid'))return;
  const list=S.data.clips.filter(c=>c.batch_id===S.batch);
  const batch=S.data.batches.find(b=>b.id===S.batch);
  $('#main > .title-row h1').textContent=(batch?.name||'Batch')+' · Review';
  $('#main > .title-row .sub').textContent=`${list.length} clips · ${list.filter(c=>c.result).length} assembled · ${list.filter(c=>c.review==='keep').length} kept`;
  reconcileItems($('#batch-grid'),list.map(c=>({key:c.id,html:batchCardHtml(c)})));
  for(const c of list){
    const card=$$('#batch-grid > .panel').find(node=>node.dataset.uiKey===c.id);
    const badge=card.querySelector('[data-review-state]');
    badge.textContent=c.review||'pending';badge.classList.toggle('pending',c.review!=='keep');
  }
  $$('#batch-grid [data-open-review]').forEach(b=>b.onclick=()=>{
    S.clip=b.dataset.openReview;S.tab='review';sidebar();draw();
  });
}
function drawBatch() {
  disposeRefineEditor();
  disposeShotEditor();
  const list = S.data.clips.filter((c) => c.batch_id === S.batch),
    batch = S.data.batches.find((b) => b.id === S.batch);
  $('#main').dataset.viewTab='batch';
  $("#main").innerHTML =
    `<div class="title-row"><div><h1>${esc(batch?.name)} · Review</h1><div class="sub">${list.length} clips · ${list.filter((c) => c.result).length} assembled · ${list.filter((c) => c.review === "keep").length} kept</div></div><button id="export-decisions">Export decisions</button></div><div id="batch-grid" class="batch-grid"></div>`;
  updateBatch();
  $("#export-decisions").onclick = () => {
    const list = S.data.clips.filter(c=>c.batch_id===S.batch);
    const batch = S.data.batches.find(b=>b.id===S.batch);
    const data = {
      batch: batch?.name,
      exported_at: new Date().toISOString(),
      clips: list.map((c) => ({
        id: c.id,
        name: c.name,
        review: c.review || "pending",
        result: c.result,
        revision: c.revision,
      })),
    };
    const a = document.createElement("a");
    a.href = URL.createObjectURL(
      new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }),
    );
    a.download = "review-decisions.json";
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  };
}

async function updateFromServer() {
  await refresh(false);
  syncWorkspace();
  updateRenderHistory();
  updateMaskEditorStatus();
  updateShotEditorStatus();
  updateRefineStatus();
  updateQueueDialog();
}
async function poll() {
  try { await updateFromServer(); }
  catch (e) { console.warn(e); }
  const correcting=S.tab==='refine'&&S.data?.jobs.some(j=>j.clip_id===S.clip&&j.shot_id===S.shot&&j.kind==='refine_mask'&&['submitting','queued','waiting','running','cancelling'].includes(j.state));
  const active=S.data?.jobs.some(j=>['submitting','queued','waiting','running','cancelling'].includes(j.state));
  setTimeout(poll, correcting||active?1000:4000);
}
async function workerPoll() {
  try {
    const workers = await api("workers");
    const host = $("#workers");
    if (!host) return; // The scoped editor may have been removed during fetch.
    S.workers=workers;
    reconcileItems(host,Object.values(workers)
      .map(
        (w) => ({key:w.worker,html:
          `<button data-worker-queue="${esc(w.worker)}" title="${esc(w.gpu || w.error || "")}" class="worker ${w.state}">${esc(workerDisplayName(w.worker))} · ${esc(workerLabel(w))} · ${S.data.jobs.filter(j=>gpuJob(j)&&j.data.worker===w.worker&&["queued","waiting","running","cancelling"].includes(j.state)).length} jobs</button>`}),
      ));
    $$('[data-worker-queue]').forEach(b=>b.onclick=()=>{S.queueWorker=b.dataset.workerQueue;dialog(workerDisplayName(S.queueWorker)+' · Job queue',queueHtml());updateQueueDialog()});
    updateQueueDialog();
  } catch (e) {
    const host = $("#workers");
    if (!host) return;
    host.textContent = "Workers unavailable";
  }
  setTimeout(workerPoll, 15000);
}
guarded(async () => {
  await refresh();
  poll();
  workerPoll();
})();
