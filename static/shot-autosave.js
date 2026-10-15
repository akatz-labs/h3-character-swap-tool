// Autosave only editable shot settings. Mask tracking and rendering remain explicit.
let shotAutosave = null;
const anchorPromptSentence = '<Picture 2> is the exact first-frame scene and pose anchor. ';
function optionalAnchorPrompt(value, enabled) {
  // Remove only our old generated boilerplate; preserve custom prompt wording.
  return enabled ? value : value.replace(anchorPromptSentence, '');
}
function shotAutosaveHtml() {
  return `<div class="shot-autosave">${uiIcon('check')}<span id="shot-save-status" class="note" role="status" aria-live="polite">All changes saved</span> <button id="shot-save-retry" class="quiet" hidden>Retry</button> <button id="shot-save-reload" class="quiet" hidden>Use saved values</button></div>`;
}
function fieldValue(field) { return field.type === 'checkbox' ? String(field.checked) : field.value; }
function autosaveDirty(form = shotAutosave) {
  return !!form && form.fields.some(({field}) => fieldValue(field) !== field.dataset.remoteValue);
}
function autosaveStatus(message, failed = false) {
  const status = $('#shot-save-status');
  if (!status) return;
  status.textContent = message;
  status.classList.toggle('error-text', failed);
  $('#shot-save-retry').hidden = !failed;
  $('#shot-save-reload').hidden = !failed;
}
function bindShotAutosave(specs) {
  clearTimeout(shotAutosave?.timer);
  const form = shotAutosave = {clipId:clip().id, shotId:shot().id, fields:specs.map(([selector,key,remote]) => {
    const field = $(selector);
    field.dataset.remoteValue = String(remote ?? '');
    if(shot()[key] == null)field.dataset.missingRemote = 'true';
    return {field,key};
  }), timer:null, pending:null};
  const schedule = () => {
    S.dirty = true;
    if (S.tab === 'render') S.renderDraftDirty = true;
    autosaveStatus('Saving…');
    clearTimeout(form.timer);
    form.timer = setTimeout(() => { void flushShotAutosave().catch(() => {}); }, 400);
  };
  form.fields.forEach(({field}) => field.addEventListener('input', schedule));
  $('#shot-save-retry').onclick = guarded(flushShotAutosave);
  $('#shot-save-reload').onclick = guarded(async () => {
    if (!confirm('Replace these unsaved settings with the saved values?')) return;
    if(form.pending)await form.pending.catch(()=>{});
    await refresh(false);
    S.dirty = false;
    draw();
  });
  if (autosaveDirty(form)) schedule();
}
async function flushShotAutosave() {
  const form = shotAutosave;
  if (!form || form.clipId !== S.clip || form.shotId !== shot()?.id || !form.fields[0]?.field.isConnected) return;
  clearTimeout(form.timer);
  if (form.pending) await form.pending;
  if (shotAutosave !== form || !form.fields[0]?.field.isConnected || !autosaveDirty(form)) return;
  const task = async () => {
    while (autosaveDirty(form)) {
      const changes = {}, expected = {}, saved = [];
      for (const {field,key} of form.fields) {
        if (fieldValue(field) === field.dataset.remoteValue) continue;
        if (field.dataset.conflict === 'true') throw Error('These settings changed elsewhere. Your edits are kept here; review them or use the saved values.');
        if (field.type === 'number' && (!field.value || !field.checkValidity())) throw Error(key === 'seed' ? 'Enter a whole-number seed of 0 or greater.' : 'Choose a render size between 0.1 and 1.0 MP.');
        const convert = value => field.type === 'checkbox' ? value === 'true' : (field.type === 'number' || ['render_mp','window_frames','history_frames'].includes(key)) ? Number(value) : value;
        changes[key] = convert(fieldValue(field));
        expected[key] = field.dataset.missingRemote === 'true' ? null : convert(field.dataset.remoteValue);
        saved.push({field,value:fieldValue(field)});
      }
      autosaveStatus('Saving…');
      await patch(changes, 'shot', expected, form);
      for (const {field,value} of saved) { field.dataset.remoteValue = value; delete field.dataset.conflict; delete field.dataset.missingRemote; }
    }
    if (shotAutosave === form) {
      S.renderDraftDirty = false;
      S.dirty = !!maskHasDraft();
      autosaveStatus('All changes saved');
    }
  };
  form.pending = task().catch(async error => {
    S.dirty = true;
    if (error.status === 409) {
      await refresh(false).catch(() => {});
      form.fields.forEach(({field}) => { field.dataset.conflict = 'true'; });
    }
    autosaveStatus('Not saved: ' + error.message, true);
    throw error;
  });
  try { await form.pending; } finally { form.pending = null; }
}
async function prepareWorkspaceLeave() {
  try { await flushShotAutosave(); }
  catch (error) { toast(error.message); return false; }
  return !(S.dirty || maskHasDraft()) || confirm('Discard unsaved changes?');
}
