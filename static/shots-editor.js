// Clip-wide cut editing. Draft boundaries never change current shots until confirmation.
let shotEditor = null;
const shotPositions = new Map();
function disposeShotEditor() {
  if (!shotEditor) return;
  const e = shotEditor;
  shotPositions.set(e.id, e.frame);
  e.closed = true; e.abort?.abort(); e.player?.dispose();
  shotEditor = null;
}
function updateShotEditorStatus() { shotEditor?.updateStatus?.(); }
function drawShots(c) {
  const e = shotEditor = {id:c.id, frame:shotPositions.get(c.id)||0, requested:shotPositions.get(c.id)||0,
    rows:c.shots.map(s=>({start:s.start,mode:s.mode})), undo:[], redo:[], ready:false, seeking:false, wanted:false};
  const initial = JSON.stringify(e.rows), max = c.frames-1;
  const draftKey=`h3-layout-draft:${c.id}`;
  try {
    const draft=JSON.parse(localStorage.getItem(draftKey)||'null');
    if(draft?.base===initial && Array.isArray(draft.rows))e.rows=draft.rows;
  } catch {}
  if(e.rows.length===c.shots.length && JSON.stringify(e.rows)===initial && c.shot_layout_draft){
    const d=c.shot_layout_draft;
    e.rows=[0,...d.cuts].map((start,i)=>({start,mode:d.modes?.[i]||'generate'}));
  }
  const alive = () => shotEditor===e && !e.closed;
  $('#workspace').innerHTML = `<section class="shots-editor">
    <div class="shots-heading"><div><h2>Define your shots</h2><p class="note">Preview a new layout here. Current shots stay active until you confirm changes.</p></div><span id="shots-saved-state" class="badge"></span></div>
    <div class="shots-layout"><section class="panel shots-source"><div class="panel-title">Source clip <a href="${asset(c.original,true)}">Original file ↗</a></div>
      <div id="shots-stage" class="shots-stage" tabindex="0" aria-label="Clip preview. Left and right arrows step one frame; space plays or pauses."><canvas id="shots-canvas"></canvas><img id="shots-poster" src="${imageAsset(c.poster)}" decoding="async" alt="Source clip preview"></div>
      <video id="source-player" class="shots-decoder" playsinline preload="auto" aria-hidden="true"></video>
      <div class="shots-player-controls"><div class="row spread"><span id="shots-preview-status" class="note" role="status">Preparing compact preview…</span><button id="shots-retry" hidden>Retry</button></div>
      <div class="shots-transport" style="${playerMetrics(c.frames)}"><div class="h3-playback-main"><button id="shots-play" class="h3-icon-button" aria-label="Play clip" title="Play clip">${uiIcon('play')}</button><div class="h3-frame-stepper"><button id="shots-back" class="h3-icon-button" aria-label="Previous frame" title="Previous frame (←)">${uiIcon('back')}</button><label class="h3-sr-only" for="shots-frame">Frame</label><input id="shots-frame" type="number" min="0" max="${max}" value="${e.frame}" aria-label="Source frame"><span class="h3-frame-total">/ ${max}</span><button id="shots-next" class="h3-icon-button" aria-label="Next frame" title="Next frame (→)">${uiIcon('next')}</button></div><span id="shots-time" class="note h3-play-time"></span></div><button id="shots-split" class="primary" disabled>${uiIcon('cut')}Split here</button></div>
      <input id="shots-timeline" class="range" type="range" min="0" max="${max}" step="1" value="${e.frame}" aria-label="Clip frame timeline">
      <div id="shots-segments" class="shots-segments" aria-label="Shot ranges"></div><div class="row spread shots-timeline-labels"><span>0</span><span>${max} · ${(c.frames/24).toFixed(2)}s</span></div>
      <div class="row spread"><span class="note">← → one frame · Shift + ← → ten frames</span><div class="row"><label>Speed <select id="shots-speed" aria-label="Preview speed"><option value="0.25">¼×</option><option value="0.5">½×</option><option value="1" selected>1×</option><option value="2">2×</option></select></label><label class="check"><input id="shots-audio" type="checkbox" ${c.audio?'checked':'disabled'}>Audio</label></div></div></div></section>
      <section class="panel shots-cuts"><div class="panel-title"><span id="shots-count"></span><div class="row"><button id="shots-undo" class="quiet h3-icon-button" aria-label="Undo cut edit" title="Undo cut edit">${uiIcon("undo")}</button><button id="shots-redo" class="quiet h3-icon-button" aria-label="Redo cut edit" title="Redo cut edit">${uiIcon("redo")}</button></div></div><div id="shots-list"></div><div class="shots-detect"><button id="detect-cuts" class="quiet">${uiIcon("cut")}Detect edits</button><span id="shots-detection-status" class="note"></span><button id="shots-suggested" hidden>Use detected cuts</button></div></section></div>
    <div class="shots-savebar"><p id="shots-save-note" class="note"></p><div class="row"><button id="shots-save" hidden>Save layout draft</button><button id="shots-confirm" class="primary">Confirm shots</button></div><p id="shots-error" role="alert" hidden></p></div>
    </section>`;
  const video=$('#source-player'), timeline=$('#shots-timeline');
  const status = text => {if(alive()){const label=$('#shots-preview-status');label.textContent=text;label.dataset.ready=String(text.includes('preview ·'));}};
  function fail(error) { if(!alive())return; e.ready=false; e.wanted=false; e.player?.stop(); uiButton($('#shots-play'),'Play clip','play',true); status(error.message); $('#shots-retry').hidden=false; frameUI(); }
  function pause() { e.wanted=false; e.player?.stop(); if(alive()) uiButton($('#shots-play'),'Play clip','play',true); }
  function frameUI() {
    if(!alive())return;
    $('#shots-time').textContent=`${(e.frame/24).toFixed(2)}s`;
    $('#shots-time').title=`${(e.frame/24).toFixed(2)}s / ${(c.frames/24).toFixed(2)}s`;
    if(!e.seeking && !e.player?.scrubbing) timeline.value=e.frame;
    if(!e.seeking && document.activeElement!==$('#shots-frame')) $('#shots-frame').value=e.frame;
    $('#shots-split').disabled=!e.ready||e.seeking||e.wanted||e.saving||e.frame===0||e.rows.some(r=>r.start===e.frame);
    document.querySelectorAll('[data-cut-row]').forEach(row=>{
      const i=+row.dataset.cutRow;
      row.classList.toggle('at-playhead',e.frame>=e.rows[i].start&&e.frame<(e.rows[i+1]?.start??c.frames));
    });
    document.querySelectorAll('[data-cut-playhead]').forEach(b=>b.disabled=!e.ready||e.seeking||e.wanted||e.saving);
  }
  async function seek(n) {
    pause(); n=Math.max(0,Math.min(max,Math.round(Number(n)||0))); e.requested=n;
    timeline.value=n; $('#shots-frame').value=n;
    if(!e.ready)return;
    e.seeking=true; frameUI();
    try { await e.player.seek(n,max); } catch(err) { fail(err); }
    if(!alive())return;
    e.seeking=false; frameUI();
  }
  function changed() {
    e.changed=JSON.stringify(e.rows)!==initial;
    S.dirty=false;
    if(e.changed)localStorage.setItem(draftKey,JSON.stringify({base:initial,rows:e.rows}));
    else localStorage.removeItem(draftKey);
    $('#shots-error').hidden=true;
    $('#shots-saved-state').textContent=e.changed?'Draft layout':'Current layout';
    $('#shots-saved-state').classList.toggle('pending',e.changed);
    $('#shots-save-note').textContent=e.changed?'Draft saved in this browser. Confirm to make these the current shots; previous shots and their work stay in Older shot layouts.':'The current shots are active. Open any shot to set up anchors, masks and renders.';
    $('#shots-save').disabled=!e.changed;
    $('#shots-confirm').textContent='Confirm shots';
    $('#shots-confirm').disabled=!e.changed||e.saving;
    $('#shots-undo').disabled=!e.undo.length; $('#shots-redo').disabled=!e.redo.length;
    $('#shots-count').textContent=`${e.rows.length} draft shot${e.rows.length===1?'':'s'}`;
    $('#shots-list').innerHTML=e.rows.map((r,i)=>{const end=e.rows[i+1]?.start??c.frames;return `<article class="shots-cut" data-cut-row="${i}"><div class="row spread"><button data-cut-jump="${r.start}" class="shots-jump">Shot ${i+1} <small>${r.start}–${end-1} · ${((end-r.start)/24).toFixed(2)}s</small></button>${i?`<button data-cut-remove="${i}" class="quiet" aria-label="Remove cut ${i}">Remove cut</button>`:'<span class="note">Clip start</span>'}</div><div class="row shots-cut-edit"><label>Start <input data-cut-start="${i}" type="number" value="${r.start}" min="${i?e.rows[i-1].start+1:0}" max="${end-1}" ${i?'':'disabled'} aria-label="Shot ${i+1} start frame"></label>${i?`<button data-cut-playhead="${i}" class="quiet" title="Move this cut to the displayed frame">Use playhead</button><div class="row shots-boundary"><button data-cut-jump="${r.start-1}" class="quiet" title="Last frame before cut" aria-label="Frame before cut ${i}">Before</button><button data-cut-jump="${r.start}" class="quiet" title="First frame after cut" aria-label="Frame after cut ${i}">After</button></div>`:''}</div><select data-cut-mode="${i}" aria-label="Shot ${i+1} action"><option value="generate" ${r.mode==='generate'?'selected':''}>Replace character</option><option value="preserve" ${r.mode==='preserve'?'selected':''}>Keep original</option></select></article>`}).join('');
    $('#shots-segments').innerHTML=e.rows.map((r,i)=>`<button class="${r.mode==='preserve'?'preserve':''}" data-cut-jump="${r.start}" style="flex:${(e.rows[i+1]?.start??c.frames)-r.start}" title="Shot ${i+1}: frames ${r.start}–${(e.rows[i+1]?.start??c.frames)-1}" aria-label="Jump to shot ${i+1}">${i+1}</button>`).join('');
    document.querySelectorAll('[data-cut-jump]').forEach(b=>b.onclick=()=>seek(+b.dataset.cutJump));
    document.querySelectorAll('[data-cut-remove]').forEach(b=>b.onclick=()=>edit(rows=>rows.splice(+b.dataset.cutRemove,1)));
    document.querySelectorAll('[data-cut-start]').forEach(input=>input.onchange=()=>move(+input.dataset.cutStart,Number(input.value)));
    document.querySelectorAll('[data-cut-playhead]').forEach(b=>b.onclick=()=>{pause();move(+b.dataset.cutPlayhead,e.frame)});
    document.querySelectorAll('[data-cut-mode]').forEach(input=>input.onchange=()=>edit(rows=>rows[+input.dataset.cutMode].mode=input.value));
    frameUI();
  }
  function edit(fn) { if(e.saving)return; const previous=structuredClone(e.rows); fn(e.rows); if(JSON.stringify(previous)===JSON.stringify(e.rows))return; e.undo.push(previous); e.redo=[]; changed(); }
  function move(i,n) {
    if(!i||!Number.isInteger(n)||n<=e.rows[i-1].start||n>=(e.rows[i+1]?.start??c.frames)) {toast('A cut must be between its neighboring cuts.');changed();return;}
    edit(rows=>rows[i].start=n);
  }
  $('#shots-split').onclick=()=>{
    if($('#shots-split').disabled)return;
    pause(); const frame=e.frame, index=e.rows.findLastIndex(r=>r.start<frame);
    edit(rows=>rows.splice(index+1,0,{start:frame,mode:rows[index].mode}));
  };
  $('#shots-undo').onclick=()=>{if(!e.undo.length||e.saving)return;e.redo.push(structuredClone(e.rows));e.rows=e.undo.pop();changed();};
  $('#shots-redo').onclick=()=>{if(!e.redo.length||e.saving)return;e.undo.push(structuredClone(e.rows));e.rows=e.redo.pop();changed();};
  async function save(reviewed) {
    if(e.saving)return;
    pause(); e.saving=true; S.dirty=true;
    document.querySelectorAll('.shots-cuts button,.shots-cuts input,.shots-cuts select,.shots-savebar button').forEach(b=>b.disabled=true);frameUI();
    try {
      await api(`clips/${c.id}/cuts`,'POST',{revision:c.revision,cuts:e.rows.slice(1).map(r=>r.start),modes:e.rows.map(r=>r.mode),reviewed});
      if(!alive())return;
      localStorage.removeItem(draftKey); S.dirty=false; S.shot=null; await refresh(); draw(); toast('Current shots updated');
    } catch(err) {
      if(!alive())return; e.saving=false; changed();
      $('#detect-cuts').disabled=false;
      $('#shots-error').textContent=err.message; $('#shots-error').hidden=false;
    }
  }
  $('#shots-save').onclick=()=>save(false); $('#shots-confirm').onclick=()=>save(true);
  e.updateStatus=()=>{
    if(!alive())return;
    const current=clip(), active=S.data.jobs.some(j=>j.clip_id===c.id&&j.kind==='cuts'&&['queued','running'].includes(j.state));
    $('#detect-cuts').disabled=active||e.saving;
    $('#shots-detection-status').textContent=active?'Detecting…':current?.suggested_cuts?`${current.suggested_cuts.length} detected cuts`:'';
    $('#shots-suggested').hidden=active||!current?.suggested_cuts;
  };
  $('#detect-cuts').onclick=guarded(async()=>{await queue('cuts');e.updateStatus();});
  $('#shots-suggested').onclick=()=>{
    const cuts=clip()?.suggested_cuts; if(!cuts)return;
    edit(rows=>{const old=structuredClone(rows);rows.splice(0,rows.length,...[0,...cuts].map(start=>({start,mode:old.findLast(r=>r.start<=start)?.mode||'generate'})))});
  };
  async function play() {
    if(!e.ready||!e.wanted)return;
    try {await e.player.start(e.frame>=max?0:null);if(alive()){e.wanted=e.player.intent;uiButton($('#shots-play'),e.wanted?'Pause clip':'Play clip',e.wanted?'pause':'play',true);frameUI();}}catch(err){fail(err)}
  }
  $('#shots-play').onclick=()=>{if(e.wanted)pause();else{e.wanted=true;uiButton($('#shots-play'),'Pause clip','pause',true);play();}frameUI();};
  $('#shots-back').onclick=()=>seek(e.requested-1); $('#shots-next').onclick=()=>seek(e.requested+1);
  $('#shots-frame').onchange=event=>seek(event.target.value);timeline.oninput=event=>seek(event.target.value);
  $('#shots-speed').onchange=event=>video.playbackRate=Number(event.target.value);
  $('#shots-audio').onchange=event=>video.muted=!event.target.checked;
  video.onended=()=>{pause();frameUI();};
  $('.shots-editor').onkeydown=event=>{
    if(event.target.closest('input,select,textarea,button,a,summary'))return;
    if(['ArrowLeft','ArrowRight','Home','End',' '].includes(event.key)) {
      event.preventDefault();
      if(event.key===' ')$('#shots-play').click();
      else seek(event.key==='Home'?0:event.key==='End'?max:e.requested+(event.key==='ArrowRight'?1:-1)*(event.shiftKey?10:1));
    }
  };
  async function load(retry=false) {
    e.ready=false; e.abort?.abort();e.player?.dispose();e.player=null;
    const controller=e.abort=new AbortController(), still=()=>alive()&&e.abort===controller;
    $('#shots-retry').hidden=true;$('#shots-poster').hidden=false;frameUI();
    try {
      let result;const deadline=Date.now()+120000;
      do {
        result=await api(`clips/${c.id}/preview`,'POST',{retry},AbortSignal.any([controller.signal,AbortSignal.timeout(15000)]));retry=false;
        if(!still())return;
        if(result.state==='failed')throw Error(result.error);
        if(result.state!=='ready'){status('Preparing compact preview…');await new Promise(r=>setTimeout(r,250));}
        if(Date.now()>deadline)throw Error('Preview is still preparing. Retry in a moment.');
      }while(result.state!=='ready');
      status('Loading compact preview…');
      e.player=new H3Player.Atlas(video,$('#shots-canvas'),null,timeline,()=>result.fps);
      e.player.onFrame(()=>{
        if(!still()||e.player.time===null)return;
        e.frame=Math.round(e.player.time*24);if(e.wanted)e.requested=e.frame;
        e.player.render(1,0,0);e.player.top.hidden=true;frameUI();
      });
      const decoded=new Promise((resolve,reject)=>{
        const cleanup=()=>{clearTimeout(timer);video.removeEventListener('loadeddata',ok);video.removeEventListener('error',bad);controller.signal.removeEventListener('abort',cancel)};
        const ok=()=>{cleanup();resolve()},bad=()=>{cleanup();reject(Error('Preview could not load. Try again.'))},cancel=()=>{cleanup();reject(Error('Preview closed'))};
        const timer=setTimeout(bad,30000);
        video.addEventListener('loadeddata',ok,{once:true});video.addEventListener('error',bad,{once:true});controller.signal.addEventListener('abort',cancel,{once:true});
      });
      video.src=asset(result.asset);video.muted=!$('#shots-audio').checked;video.playbackRate=Number($('#shots-speed').value);video.load();
      await decoded;if(!still())return;await e.player.seek(e.requested,max);if(!still())return;
      e.ready=true;$('#shots-poster').hidden=true;status(`${result.width} × ${result.height} preview · ${result.fps} fps`);frameUI();await play();
    } catch(err) {if(still())fail(err);}
  }
  $('#shots-retry').onclick=()=>load(true);
  changed();e.updateStatus();load();
}
