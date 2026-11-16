// One on-media interaction layer for images and Atlas video; no pixel redraws or idle loop.
export function attachWipeHandle(stage, range, {label = 'Comparison wipe', line = null} = {}) {
  const handle = document.createElement('div');
  handle.className = 'ak-report-wipe-handle'; handle.tabIndex = 0;
  handle.setAttribute('role', 'slider'); handle.setAttribute('aria-label', label);
  handle.setAttribute('aria-orientation', 'horizontal'); handle.setAttribute('aria-valuemin', '0'); handle.setAttribute('aria-valuemax', '100');
  handle.title = 'Drag or click the preview to move the wipe'; handle.textContent = '↔';
  stage.classList.add('ak-report-direct-wipe'); stage.append(handle);
  const ownLine = !line;
  if (!line) { line = document.createElement('span'); line.className = 'ak-report-direct-line'; line.setAttribute('aria-hidden', 'true'); stage.append(line); }
  let pointer = null, scheduled = 0;
  const sync = () => {
    const n = Math.max(0, Math.min(100, Number(range.value) || 0));
    line.style.left = n + '%'; handle.style.left = `clamp(22px, ${n}%, calc(100% - 22px))`;
    handle.setAttribute('aria-valuenow', String(n)); handle.setAttribute('aria-valuetext', range.getAttribute('aria-valuetext') || `${n}%`);
  };
  const set = n => { range.value = Math.max(0, Math.min(100, n)); range.dispatchEvent(new Event('input', {bubbles:true})); sync(); };
  const place = () => {
    scheduled = 0; const r = stage.getBoundingClientRect();
    if (!r.height) return;
    const visibleTop = window.visualViewport?.offsetTop || 0;
    const visibleBottom = visibleTop + (window.visualViewport?.height || innerHeight);
    const top = Math.max(22, visibleTop - r.top + 22), bottom = Math.min(r.height - 22, visibleBottom - r.top - 22);
    handle.style.top = `${Math.max(22, Math.min(r.height - 22, (top + bottom) / 2))}px`;
  };
  const schedule = () => { if (!scheduled) scheduled = requestAnimationFrame(place); };
  const move = e => { const r = stage.getBoundingClientRect(); if (r.width) set((e.clientX - r.left) / r.width * 100); };
  const down = e => {
    if (!e.isPrimary || e.button !== 0 || (e.target !== handle && e.target.closest('a,button,input,select,textarea'))) return;
    pointer = e.pointerId; stage.setPointerCapture(pointer); handle.focus({preventScroll:true}); move(e);
  };
  const drag = e => { if (e.pointerId === pointer) move(e); };
  const release = e => { if (e.pointerId !== pointer) return; if (stage.hasPointerCapture(pointer)) stage.releasePointerCapture(pointer); pointer = null; };
  const keyboard = e => {
    const delta = e.shiftKey ? 10 : 1, n = Number(range.value);
    const values = {ArrowLeft:n-delta, ArrowDown:n-delta, ArrowRight:n+delta, ArrowUp:n+delta, Home:0, End:100};
    if (Object.hasOwn(values,e.key)) { e.preventDefault(); set(values[e.key]); }
  };
  range.addEventListener('input',sync); handle.addEventListener('keydown',keyboard);
  stage.addEventListener('pointerdown',down); stage.addEventListener('pointermove',drag);
  for (const type of ['pointerup','pointercancel','lostpointercapture']) stage.addEventListener(type,release);
  window.addEventListener('scroll',schedule,true); window.addEventListener('resize',schedule);
  window.visualViewport?.addEventListener('scroll',schedule); window.visualViewport?.addEventListener('resize',schedule);
  const observer = new ResizeObserver(schedule); observer.observe(stage); sync(); schedule();
  return {handle, set, destroy() {
    if (pointer !== null && stage.hasPointerCapture(pointer)) stage.releasePointerCapture(pointer);
    pointer = null; observer.disconnect(); cancelAnimationFrame(scheduled);
    window.removeEventListener('scroll',schedule,true); window.removeEventListener('resize',schedule);
    window.visualViewport?.removeEventListener('scroll',schedule); window.visualViewport?.removeEventListener('resize',schedule);
    range.removeEventListener('input',sync); handle.removeEventListener('keydown',keyboard);
    stage.removeEventListener('pointerdown',down); stage.removeEventListener('pointermove',drag);
    for (const type of ['pointerup','pointercancel','lostpointercapture']) stage.removeEventListener(type,release);
    handle.remove(); if (ownLine) line.remove(); stage.classList.remove('ak-report-direct-wipe');
  }};
}
