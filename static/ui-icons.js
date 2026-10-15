// Small, consistent line icons for the app-owned editors.
const uiPaths = {
  play: 'm8 5 11 7-11 7z', pause: 'M8 5v14M16 5v14',
  back: 'M5 5v14m14-14-9 7 9 7z', next: 'M19 5v14M5 5l9 7-9 7z',
  plus: 'M12 5v14M5 12h14', minus: 'M5 12h14',
  arrow: 'M5 12h14m-6-6 6 6-6 6', left: 'M19 12H5m6-6-6 6 6 6',
  upload: 'M12 16V4m-5 5 5-5 5 5M5 15v5h14v-5',
  download: 'M12 3v12m-5-5 5 5 5-5M5 16v5h14v-5',
  image: 'M3 4h18v16H3zM3 17l6-6 4 4 3-3 5 5M7 8h.01',
  text: 'M5 3h10l4 4v14H5zM14 3v5h5M8 12h8m-8 4h6',
  audio: 'M4 9v6m4-10v14m4-17v20m4-17v14m4-10v6',
  undo: 'M8 4 3 9l5 5M3 9h11a6 6 0 0 1 0 12',
  redo: 'm16 4 5 5-5 5m5-5H10a6 6 0 0 0 0 12',
  move: 'M12 2v20M2 12h20M8 6l4-4 4 4m-8 12 4 4 4-4M6 8l-4 4 4 4m12-8 4 4-4 4',
  trash: 'M4 6h16M9 6V3h6v3M6 6l1 15h10l1-15M10 10v7m4-7v7',
  track: 'M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5M12 7a5 5 0 1 0 0 10 5 5 0 0 0 0-10',
  cut: 'M9 8 20 19M9 16 20 5M7 3a3 3 0 1 0 0 6 3 3 0 0 0 0-6M7 15a3 3 0 1 0 0 6 3 3 0 0 0 0-6',
  layers: 'm12 3 10 5-10 5L2 8zm-10 9 10 5 10-5M2 16l10 5 10-5',
  queue: 'M9 5h12M9 12h12M9 19h12M3 5h.01M3 12h.01M3 19h.01',
  film: 'M3 5h18v14H3zM7 5v14M17 5v14M3 9h4m10 0h4M3 15h4m10 0h4',
  check: 'm5 12 4 4L19 6', search: 'M10.5 3a7.5 7.5 0 1 0 0 15 7.5 7.5 0 0 0 0-15M16 16l5 5',
  chevron: 'm7 10 5 5 5-5', swap: 'M3 7h17m-5-5 5 5-5 5M21 17H4m5-5-5 5 5 5',
  fullscreen: 'M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5',
  star: 'm12 3 2.8 5.7L21 9.6l-4.5 4.4 1.1 6.2-5.6-3-5.6 3 1.1-6.2L3 9.6l6.2-.9z',
};
function uiIcon(name) {
  if (appKitIcons[name]) return appKitIcons[name];
  return `<svg class="h3-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="${uiPaths[name] || uiPaths.play}"/></svg>`;
}
function uiButton(button, label, icon, iconOnly = false) {
  if (!button) return;
  const key = `${label}:${icon}:${iconOnly}`;
  if (button.dataset.uiLabel === key) return;
  button.dataset.uiLabel = key;
  button.innerHTML = uiIcon(icon) + (iconOnly ? '' : `<span>${label}</span>`);
  button.setAttribute('aria-label', label);
  if (iconOnly) button.title = label;
}
function playerMetrics(frames, start = 0) {
  return `--frame-digits:${Math.max(3, String(start + frames - 1).length)};--time-digits:${Math.max(4, ((frames - 1) / 24).toFixed(2).length)}`;
}
