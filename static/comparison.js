// Render-modal comparison UI. One H3 report Atlas decoder drives both views.
let renderComparison = null;
const warmComparisons = new Set();
function warmRenderComparison(c, s, id) {
  const key = `${c.id}:${s.id}:${s.start}:${s.end}:${id}`;
  if (warmComparisons.has(key)) return;
  warmComparisons.add(key);
  if (warmComparisons.size > 64) warmComparisons.delete(warmComparisons.values().next().value);
  api(`clips/${c.id}/shots/${s.id}/comparison`, "POST", {left: "source", right: id})
    .catch(() => warmComparisons.delete(key));
}
function comparisonHtml(c, s, opened, prefix = "") {
  const renders = [...s.renders].sort((a, b) => (b.created || 0) - (a.created || 0));
  const options = [{ id: "source", label: "Original source" }, ...renders.map((r, i) => ({
    id: r.id, label: `${takeLabel(s,r)} · Seed ${r.seed}${r.id === s.selected_render ? " · selected" : ""}`,
  }))];
  const select = (side, selected) => `<label class="compare-choice">${side === "left" ? "Left" : "Right"}<select id="compare-${side}" aria-label="${side} comparison clip">${options.map(o => `<option value="${esc(o.id)}" ${o.id === selected ? "selected" : ""}>${esc(o.label)}</option>`).join("")}</select></label>`;
  const html = `<section class="comparison" aria-label="Shot comparison"><div class="compare-choices">${select("left", opened.left || "source")}<button id="compare-swap" title="Swap left and right" aria-label="Swap comparison sides">${uiIcon("swap")}</button>${select("right", opened.id)}</div><div class="row spread compare-toolbar"><div class="row"><label>View <select id="compare-mode" aria-label="Comparison view"><option value="wipe">Wipe</option><option value="side">Side by side</option><option value="left">Left only</option><option value="right">Right only</option></select></label><button id="compare-original" class="quiet">Source + take</button><button id="compare-previous" class="quiet" ${renders.length < 2 ? "disabled" : ""}>Latest two takes</button></div><button id="compare-fullscreen" class="quiet h3-icon-button" aria-label="Fullscreen" title="Fullscreen">${uiIcon("fullscreen")}</button></div><div id="compare-stage" class="compare-stage" data-mode="wipe" tabindex="0" aria-label="Comparison. Drag to move wipe; arrow keys step frames; space plays or pauses."><canvas id="compare-canvas"></canvas><img id="compare-poster" src="${asset(opened.asset)}/thumbnail" alt="Selected render preview"><div id="compare-divider"></div><div class="compare-tags"><span id="compare-label-left"></span><span id="compare-label-right"></span></div></div><video id="render-modal-video" class="compare-decoder" playsinline preload="auto" aria-hidden="true"></video><div id="compare-status" class="note" role="status">Preparing synchronized comparison…</div><button id="compare-retry" hidden>Retry comparison</button><div id="compare-wipe-control" class="row"><label for="compare-wipe">Wipe</label><input id="compare-wipe" type="range" min="0" max="100" step="0.1" value="50" aria-label="Wipe position"></div><div class="compare-transport" style="${playerMetrics(s.end-s.start,s.start)}"><div class="h3-playback-main"><button id="compare-play" class="primary h3-icon-button" aria-label="Play" title="Play">${uiIcon('play')}</button><div class="h3-frame-stepper"><button id="compare-back" class="h3-icon-button" aria-label="Previous comparison frame" title="Previous frame">${uiIcon('back')}</button><input id="compare-frame" type="number" min="0" max="${s.end-s.start-1}" value="0" aria-label="Comparison frame"><span class="h3-frame-total">/ ${s.end-s.start-1}</span><button id="compare-next" class="h3-icon-button" aria-label="Next comparison frame" title="Next frame">${uiIcon('next')}</button></div><span id="compare-time" class="note h3-play-time">0.00s</span></div><div class="h3-playback-options"><select id="compare-speed" aria-label="Playback speed"><option value="0.25">¼×</option><option value="0.5">½×</option><option value="1" selected>1×</option><option value="2">2×</option></select><label class="check"><input id="compare-audio" type="checkbox" ${c.audio ? "checked" : "disabled"}>Audio</label><label class="check"><input id="compare-loop" type="checkbox" checked>Loop</label></div></div><input id="compare-timeline" class="range" type="range" min="0" max="${s.end-s.start-1}" step="1" value="0" aria-label="Comparison timeline"><p class="note compare-shortcuts">Drag to wipe · ← / → step frames · Space to play</p></section>`;
  return prefix ? html.replace(/((?:id|for)=")([^" ]+)/g, (_,a,b)=>a+prefix+b) : html;
}
function disposeRenderComparison() {
  if (!renderComparison) return;
  renderComparison.dispose();
  renderComparison = null;
  $("#dialog").classList.remove("comparison-dialog");
  $("#dialog").classList.remove("compare-expanded");
}
function bindRenderComparison(c, s, opened, options = {}) {
  const e = { token: 0, frame: 0, wanted: false, ready: false, closed: false, pair: null, player: null };
  const root = options.root || document.querySelector("#dialog-body"), prefix = options.prefix || "";
  const q = selector => root.querySelector(selector.replace(/#([\w-]+)/g,(_,id)=>"#"+prefix+id));
  if (!options.root) renderComparison = e;
  const video = q("#render-modal-video"), stage = q("#compare-stage"), timeline = q("#compare-timeline"), wipe = q("#compare-wipe");
  const max = s.end - s.start - 1;
  if (!options.root) $("#dialog").classList.add("comparison-dialog");
  const alive = () => !e.closed && root.isConnected;
  e.dispose = () => {e.closed=true;e.token++;e.loadingAbort?.abort();e.requestAbort?.abort();e.player?.dispose()};
  const status = text => { if (alive()) q("#compare-status").textContent = text; };
  const error = err => { if (!alive()) return; e.wanted = false; uiButton(q("#compare-play"), "Play", "play", true); status(err.message); q("#compare-retry").hidden = false; };
  const syncIntent = () => { uiButton(q("#compare-play"), e.wanted ? "Pause" : "Play", e.wanted ? "pause" : "play", true); };
  function reveal() {
    e.player?.reveal();
    q("#compare-divider").style.left = `${wipe.value}%`;
  }
  function draw() {
    if (!e.player || !e.pair || !alive()) return;
    const mode = q("#compare-mode").value;
    const [left,right] = options.indices ? options.indices(e.pair,mode) : [e.pair.columns.indexOf(q("#compare-left").value),e.pair.columns.indexOf(q("#compare-right").value)];
    const report=mode==='report16'||mode==='reportFit';
    if (left < 0 || right < 0) return;
    e.player.render(e.pair.columns.length, mode === "right" ? right : left, right);
    e.player.top.hidden = mode === "left" || mode === "right" || report;
    stage.dataset.mode = report ? "left" : mode;
    q("#compare-wipe-control").hidden = mode !== "wipe";
    q("#compare-divider").hidden = mode !== "wipe";
    q("#compare-label-left").hidden = mode === "right" || report;
    q("#compare-label-right").hidden = mode === "left" || report;
    const label = id => q("#compare-left").querySelector(`option[value="${CSS.escape(id)}"]`)?.textContent || id;
    q("#compare-label-left").textContent = e.labels?.[0] || label(q("#compare-left").value);
    q("#compare-label-right").textContent = e.labels?.[1] || label(q("#compare-right").value);
    reveal();
  }
  function frameShown() {
    if (!alive() || e.player.time === null) return;
    e.frame = Math.max(0, Math.min(max, Math.round(e.player.time * 24)));
    if (!e.player.scrubbing) timeline.value = e.frame;
    if (document.activeElement !== q("#compare-frame")) q("#compare-frame").value = e.frame;
    q("#compare-time").textContent = `${(e.frame/24).toFixed(2)}s`;
    q("#compare-time").title = `Source frame ${s.start+e.frame} · ${(e.frame/24).toFixed(2)}s / ${((max+1)/24).toFixed(2)}s`;
    draw();
  }
  async function play() {
    if (!e.ready || !e.wanted) return;
    try {
      await e.player.start(e.frame >= max ? 0 : null);
      if (!alive()) return;
      e.wanted = e.player.intent;
      syncIntent();
    } catch (err) { error(err); }
  }
  function pause() { e.wanted = false; e.player?.stop(); syncIntent(); }
  function seek(frame) {
    pause();
    frame = Math.max(0, Math.min(max, Math.round(Number(frame) || 0)));
    e.frame = frame;
    e.requestedFrame = frame;
    timeline.value = frame;
    q("#compare-frame").value = frame;
    if (e.ready) e.player.seek(frame, max).catch(error);
  }
  async function loadPair(retry = false) {
    const token = ++e.token, left = q("#compare-left").value, right = q("#compare-right").value;
    const still = () => alive() && e.token === token;
    const mode=q('#compare-mode').value,requestKey=options.requestKey?.(mode)||'';
    if (e.ready && e.loadedRequestKey===requestKey && (options.indices || (e.pair.columns.includes(left) && e.pair.columns.includes(right)))) { draw(); return; }
    e.requestedFrame = e.frame;
    e.ready = false;
    e.loadingAbort?.abort();
    e.requestAbort?.abort();
    e.player?.dispose(); e.player = null; e.pair = null;
    stage.classList.add("is-loading");
    q("#compare-poster").hidden = false;
    status("Preparing synchronized comparison…");
    q("#compare-retry").hidden = true;
    try {
      let result;
      const deadline = Date.now() + 120000;
      do {
        const requestAbort = e.requestAbort = new AbortController();
        const requestTimer = setTimeout(() => requestAbort.abort(), 15000);
        try {
          result = options.prepare ? await options.prepare({mode,retry,signal:e.requestAbort.signal}) : await api(`clips/${c.id}/shots/${s.id}/comparison`, "POST", { left, right, ...(retry ? { retry: true } : {}) }, e.requestAbort.signal);
        } finally { clearTimeout(requestTimer); }
        retry = false;
        if (!still()) return;
        if (result.state === "failed") throw Error(result.error);
        if (Date.now() > deadline) throw Error("Preview is still preparing. Retry in a moment.");
        if (result.state !== "ready") {
          status(result.phase || "Preparing small comparison preview…");
          await new Promise(resolve => setTimeout(resolve, 250));
        }
      } while (result.state !== "ready" && still());
      if (!still()) return;
      e.pair = result;e.loadedRequestKey=requestKey;
      status("Loading compact preview…");
      e.player = new H3Player.Atlas(video, q("#compare-canvas"), wipe, timeline, () => result.fps);
      e.player.onFrame(frameShown);
      e.loadingAbort = new AbortController();
      const decoded = new Promise((resolve, reject) => {
        const clean = () => { clearTimeout(timer); video.removeEventListener("loadeddata", ok); video.removeEventListener("error", bad); e.loadingAbort.signal.removeEventListener("abort", cancel); };
        const ok = () => { clean(); resolve(); };
        const bad = () => { clean(); reject(Error("Comparison could not decode. Retry or download the original render.")); };
        const cancel = () => { clean(); reject(Error("Comparison changed")); };
        const timer = setTimeout(bad, 30000);
        video.addEventListener("loadeddata", ok, { once: true });
        video.addEventListener("error", bad, { once: true });
        e.loadingAbort.signal.addEventListener("abort", cancel, { once: true });
      });
      video.src = asset(result.asset);
      video.loop = q("#compare-loop").checked;
      video.muted = !q("#compare-audio").checked;
      video.playbackRate = Number(q("#compare-speed").value);
      video.load();
      await decoded;
      if (!still()) return;
      await e.player.seek(e.requestedFrame, max);
      if (!still()) return;
      e.ready = true;options.onReady?.(result);
      stage.classList.remove("is-loading");
      q("#compare-poster").hidden = true;
      status(`${result.frames} frames · ${result.fps} fps${result.audio ? " · source audio" : " · silent source"}`);
      draw();
      await play();
    } catch (err) { if (still()) { stage.classList.remove("is-loading"); error(err); } }
  }
  ["left", "right"].forEach(side => q(`#compare-${side}`).onchange = () => loadPair());
  q("#compare-swap").onclick = () => {
    const left = q("#compare-left").value;
    q("#compare-left").value = q("#compare-right").value;
    q("#compare-right").value = left;
    loadPair();
  };
  q("#compare-original").onclick = () => { q("#compare-left").value = "source"; q("#compare-right").value = opened.id; loadPair(); };
  q("#compare-previous").onclick = () => {
    const renders = [...s.renders].sort((a,b) => (b.created || 0) - (a.created || 0));
    q("#compare-left").value = renders[1].id; q("#compare-right").value = renders[0].id; loadPair();
  };
  q("#compare-mode").onchange = () => options.prepare ? loadPair() : draw();
  wipe.oninput = reveal;
  q("#compare-play").onclick = () => { options.onPlay?.(); if (e.wanted) pause(); else { e.wanted = true; syncIntent(); play(); } };
  q("#compare-back").onclick = () => seek(e.frame - 1);
  q("#compare-next").onclick = () => seek(e.frame + 1);
  timeline.oninput = event => seek(event.target.value);
  q("#compare-frame").onchange = event => seek(event.target.value);
  q("#compare-speed").onchange = event => { video.playbackRate = Number(event.target.value); };
  q("#compare-audio").onchange = event => { video.muted = !event.target.checked; };
  q("#compare-loop").onchange = event => { video.loop = event.target.checked; };
  video.onended = () => { e.wanted = false; syncIntent(); };
  q("#compare-retry").onclick = () => loadPair(true);
  q("#compare-fullscreen").onclick = async () => {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else if (q(".comparison").requestFullscreen) await q(".comparison").requestFullscreen();
      else (options.root || $("#dialog")).classList.toggle("compare-expanded");
    } catch (err) { status(err.message); }
  };
  const moveWipe = event => {
    const rect = stage.getBoundingClientRect();
    wipe.value = Math.max(0, Math.min(100, (event.clientX - rect.left) / rect.width * 100)); reveal();
  };
  stage.onpointerdown = event => {
    if (stage.dataset.mode !== "wipe") return;
    event.preventDefault(); stage.focus(); stage.setPointerCapture(event.pointerId); moveWipe(event);
  };
  stage.onpointermove = event => { if (stage.hasPointerCapture(event.pointerId)) moveWipe(event); };
  stage.onpointerup = event => { if (stage.hasPointerCapture(event.pointerId)) stage.releasePointerCapture(event.pointerId); };
  q(".comparison").onkeydown = event => {
    if (["INPUT", "SELECT", "TEXTAREA", "BUTTON"].includes(event.target.tagName)) return;
    if (["ArrowLeft", "ArrowRight", " "].includes(event.key)) event.preventDefault();
    if (event.key === "ArrowLeft") seek(e.frame - 1);
    if (event.key === "ArrowRight") seek(e.frame + 1);
    if (event.key === " ") q("#compare-play").click();
  };
  e.pause = pause;
  e.setPair = (left, right, mode, labels) => {
    q('#compare-left').value=left;q('#compare-right').value=right;
    if(mode)q('#compare-mode').value=mode;e.labels=labels;
    return loadPair();
  };
  e.frame = options.frame || 0;
  if(options.mode)q('#compare-mode').value=options.mode;
  e.labels=options.labels;
  if(options.inline)q('.comparison').classList.add('inline-comparison');
  loadPair();
  return e;
}
