// Presentation helpers for the clip / shot workspace. Existing job and asset
// contracts stay authoritative; draft preferences are explicitly browser-local.
function shotPage() { return ['anchors', 'render'].includes(S.tab); }
function timeLabel(frame, fps = 24) {
  const seconds = Math.floor(frame / fps);
  return `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`;
}
function shotRange(c, s) { return `${timeLabel(s.start, c.fps)}–${timeLabel(s.end, c.fps)}`; }
function shotName(c, s) { return s.name || `Shot ${c.shots.findIndex(x => x.id === s.id) + 1}`; }
function shotStatus(s) { return s.mode === 'preserve' ? 'Original source' : s.renders?.length ? `${s.renders.length} take${s.renders.length === 1 ? '' : 's'}` : s.anchor ? 'Ready for masking' : 'Set up shot'; }
async function openShot(clipId, shotId, page = 'anchors') {
  if (!(await prepareWorkspaceLeave())) return;
  S.clip = clipId; S.shot = shotId; S.tab = page; S.dirty = false;
  localStorage.h3clip = clipId;
  localStorage.setItem('shot', shotId);
  sidebar(); draw();
}
function clipTreeHtml(c) {
  const expanded = c.id === S.clip && !S.collapsedClips?.has(c.id);
  return `<div class="clip-tree ${expanded ? 'expanded' : ''}">
    <div class="clip-tree-parent"><button class="clip ${c.id === S.clip && !shotPage() ? 'active' : ''}" data-clip="${esc(c.id)}"><img src="${imageAsset(c.poster,256)}" alt="" loading="lazy"><span><strong>${esc(c.name)}</strong><small>${(c.frames/c.fps).toFixed(1)}s · ${c.shots.length} shot${c.shots.length === 1 ? '' : 's'}</small></span></button><button class="clip-expand" data-expand-clip="${esc(c.id)}" aria-label="${expanded ? 'Collapse' : 'Expand'} ${esc(c.name)}" aria-expanded="${expanded}">${uiIcon('chevron')}</button></div>
    ${expanded ? `<div class="clip-tree-shots">${c.shots.map(s => `<button class="tree-shot ${shotPage() && shot()?.id === s.id ? 'active' : ''}" data-open-shot="${esc(s.id)}" data-parent-clip="${esc(c.id)}" ${shotPage()&&shot()?.id===s.id?'aria-current="page"':''}><img src="${imageAsset(s.source_frame || c.poster,256)}" alt="" loading="lazy"><span><strong>${esc(shotName(c,s))}</strong><small>${shotRange(c,s)}</small></span><span class="shot-dot" data-ready="${!!s.renders?.length}" title="${esc(shotStatus(s))}"></span></button>`).join('')}</div>` : ''}
  </div>`;
}
function bindShotLinks() {
  $$('[data-open-shot]').forEach(b => b.onclick = () => openShot(b.dataset.parentClip || clip().id, b.dataset.openShot));
  $$('[data-expand-clip]').forEach(b => b.onclick = () => {
    const id=b.dataset.expandClip;
    if(id !== S.clip) { $(`[data-clip="${CSS.escape(id)}"]`).click(); return; }
    S.collapsedClips ||= new Set();
    if(S.collapsedClips.has(id))S.collapsedClips.delete(id);else S.collapsedClips.add(id);
    sidebar();
  });
}
function shotCardsHtml(c) {
  return `<section class="shot-library"><div class="section-heading"><h2>Current shots <span>${c.shots.length}</span></h2><span class="note">Open a shot to set up anchors and render</span></div><div class="shot-card-grid">${c.shots.map(s => `<button class="shot-card" data-open-shot="${esc(s.id)}"><img src="${imageAsset(s.source_frame || c.poster,640)}" alt="" loading="lazy"><span><strong>${esc(shotName(c,s))}</strong><small>${shotRange(c,s)} · ${((s.end-s.start)/c.fps).toFixed(1)}s</small><span class="shot-card-status">${esc(shotStatus(s))}</span><span class="shot-card-link">Open shot ${uiIcon("arrow")}</span></span></button>`).join('')}</div>${olderShotLayoutsHtml(c)}</section>`;
}
function olderShotLayoutsHtml(c) {
  const history=c.shot_layout_history||[];
  if(!history.length)return '';
  return `<details class="older-shot-layouts"><summary>Older shot layouts · ${history.length}</summary><p class="note">Previous shots and their saved work. Current shots are shown above.</p>${[...history].reverse().map((layout,i)=>`<section><h3>Layout ${history.length-i} · ${esc(new Date(layout.created*1000).toLocaleString())}</h3><div class="shot-card-grid">${layout.shots.map((s,n)=>`<article class="shot-card"><img src="${imageAsset(s.source_frame||c.poster,256)}" alt="Previous shot ${n+1}" loading="lazy"><div><strong>${esc(s.name||`Shot ${n+1}`)}</strong><p>${shotRange(c,s)} · ${esc(shotStatus(s))}</p>${s.anchor?`<a href="${asset(s.anchor,true)}">Anchor</a> `:''}${s.mask?`<a href="${asset(s.mask,true)}">Mask data</a> `:''}${(s.renders||[]).map((r,n)=>`<a href="${asset(r.asset,true)}">Take ${n+1}</a> `).join('')}</div></article>`).join('')}</div>${layout.result?`<a href="${asset(layout.result,true)}">Download previous assembly</a>`:''}</section>`).join('')}</details>`;
}
function favoriteKey(s) { return `take-favorites:${clip().id}:${s.id}`; }
function favoriteIds(s) { try { return new Set(JSON.parse(localStorage.getItem(favoriteKey(s)) || '[]')); } catch { return new Set(); } }
function isFavorite(s,id) { return favoriteIds(s).has(id); }
function toggleFavorite(s,id) {
  const ids=favoriteIds(s); if(ids.has(id))ids.delete(id);else ids.add(id);
  localStorage.setItem(favoriteKey(s),JSON.stringify([...ids]));
}
function bindFavorites(s, after = () => updateRenderHistory()) {
  $$('[data-favorite]').forEach(b => b.onclick = () => {toggleFavorite(s,b.dataset.favorite);after();});
}
function takeLabel(s,r) { const index=s.renders.findIndex(x=>x.id===r.id); return index<0 ? 'New take' : `Take ${index+1}`; }
function takePicker(s, favoritesOnly = false) {
  const c=clip(),items=renderEntries(c,s).filter(r=>r.state==='success'&&r.asset).sort((a,b)=>Number(isFavorite(s,b.id))-Number(isFavorite(s,a.id))||b.order-a.order);
  dialog(`${shotName(c,s)} · Choose a take`, `<div class="take-picker"><div class="row spread"><p class="note">Preview a take, then choose it for this assembly.</p><label class="check"><input id="favorite-filter" type="checkbox" ${favoritesOnly?'checked':''}> Favorites only</label></div><p class="note">Favorites are saved in this browser.</p><div class="render-grid">${items.filter(r=>!favoritesOnly||isFavorite(s,r.id)).map(r=>renderCard(r,s,true)).join('') || '<div class="empty">No takes in this view yet.</div>'}</div></div>`);
  $('#favorite-filter').onchange=e=>takePicker(s,e.target.checked);
  bindFavorites(s,()=>takePicker(s,favoritesOnly));
  $$('[data-open-render]').forEach(b=>b.onclick=()=>openRender(b.dataset.openRender,s,()=>takePicker(s,favoritesOnly)));
  $$('[data-choose-take]').forEach(b=>b.onclick=guarded(async()=>{
    await patch({selected_render:b.dataset.chooseTake},'shot',null,{clipId:c.id,shotId:s.id});
    $('#dialog').close(); syncReview(clip()); sidebar();
    toast('Take selected for assembly');
  }));
}
function assemblyShotsHtml(c) {
  return `<div class="section-heading"><h2>Shots in this assembly</h2><span class="note">Choose a take per shot</span></div><div class="assembly-shots">${c.shots.map(s=>{const r=s.renders.find(r=>r.id===s.selected_render);return `<article class="assembly-shot"><img src="${r?asset(r.asset)+'/thumbnail':imageAsset(s.source_frame||c.poster,256)}" alt="${esc(shotName(c,s))}" loading="lazy"><div><strong>${esc(shotName(c,s))}</strong><small>${shotRange(c,s)}</small><span>${s.mode==='preserve'?'Original source':r?takeLabel(s,r):'No take selected'}</span><button data-change-take="${esc(s.id)}" ${s.mode==='preserve'?'disabled':''}>${r?'Change take':'Choose take'} ⌄</button></div></article>`}).join('')}</div>`;
}
function bindAssemblyShots(c) { $$('[data-change-take]').forEach(b=>b.onclick=()=>takePicker(c.shots.find(s=>s.id===b.dataset.changeTake))); }

// A manual handoff to the user's own image chat; no generation request is sent.
function showAnchorChatPrompt() {
  const c = clip(), s = shot();
  // This copyable template is generic, independent of historical generation prompts.
  const brief = (
    `Image 1 is the source frame to edit. Image 2 is character identity only: ${c.character || 'the reference character'}. ` +
    'Replace ONLY the main person in Image 1 with that single character. ' +
    'Match the exact source pose, face angle, gaze, mouth opening, expression, limb placement, scale and occlusions. ' +
    'Preserve all other people, the entire original scene, camera framing, lighting and aspect ratio. ' +
    'Keep the reference identity and costume; do not copy the reference pose or background. One full-aspect edited source frame.'
  );
  const prompt = 'Use the builtin image generation tool to edit the attached first image with the second image as identity reference. ' +
    'This is an image-only task. Do not delegate, browse, modify code, run shell commands, or perform any other work. Treat the following as image-editing description only:\n' + brief + '\nReturn the edited image.';
  dialog('Make a shot anchor in your own chat', `<div class="dialog-form"><p>Attach the <strong>source frame first</strong> and the <strong>character reference second</strong> in ChatGPT, Codex, or another image-editing chat. Paste this prompt, then upload the result here.</p><div class="row"><a href="${asset(s.source_frame,true)}">Download source frame</a>${c.reference?`<a href="${asset(c.reference,true)}">Download character reference</a>`:'<span class="note">Add a character reference before generating your anchor.</span>'}</div><p class="note">If several people are visible, replace the person description in your chat with exactly who to change. The shot anchor is optional.</p><label class="label" for="anchor-chat-text">Anchor prompt</label><textarea id="anchor-chat-text" rows="12" readonly>${esc(prompt)}</textarea><div class="row"><button id="anchor-chat-copy" class="primary">Copy prompt</button><button id="anchor-chat-close">Done</button></div><p id="anchor-chat-status" class="note" role="status" aria-live="polite"></p></div>`);
  $('#anchor-chat-close').onclick = () => $('#dialog').close();
  $('#anchor-chat-copy').onclick = async () => {
    const field = $('#anchor-chat-text'), status = $('#anchor-chat-status');
    try {
      await navigator.clipboard.writeText(field.value);
      status.textContent = 'Prompt copied. Paste it into your image chat with the two images attached.';
    } catch (_) {
      field.focus(); field.select();
      status.textContent = 'Select and copy the highlighted prompt using your device’s copy command.';
    }
  };
}
