import {attachWipeHandle} from './report-wipe.js';
// Local ES module. Sources must be preview derivatives; originals are download links only.
function node(tag, className, text) {
  const n = document.createElement(tag);
  n.className = className;
  if (text) n.textContent = text;
  return n;
}
function validImage(p) {
  if (!p?.src || !(p.width > 0) || !(p.height > 0)) throw new Error('A preview URL and positive dimensions are required');
}
function assignImage(img, p, label) {
  validImage(p);
  img.width = p.width;
  img.height = p.height;
  img.alt = label;
  img.decoding = 'async';
  img.src = p.src;
}
export function createLightbox(parent = document.body) {
  const dialog = node('dialog', 'ak-report-lightbox'), bar = node('div', 'ak-report-lightbox-bar');
  const title = node('strong', '', ''), download = node('a', '', 'Download original ↓'), close = node('button', '', 'Close ×');
  const img = node('img', 'ak-report-enlarged'), status = node('p', 'ak-report-media-status');
  close.type = 'button'; close.setAttribute('aria-label', 'Close image'); download.download = '';
  status.setAttribute('role', 'status');
  bar.append(title, download, close); dialog.append(bar, status, img); parent.append(dialog);
  let returnFocus;
  const hide = () => dialog.close();
  close.addEventListener('click', hide);
  dialog.addEventListener('click', e => { if (e.target === dialog) { const b = dialog.getBoundingClientRect(); if (e.clientX < b.left || e.clientX > b.right || e.clientY < b.top || e.clientY > b.bottom) hide(); } });
  dialog.addEventListener('close', () => { img.removeAttribute('src'); if (returnFocus?.isConnected) returnFocus.focus(); });
  img.addEventListener('load', () => { status.hidden = true; });
  img.addEventListener('error', () => { status.hidden = false; status.textContent = 'Preview unavailable. Try again or download the original.'; });
  return {
    open({preview, original, title: label = 'Image preview'}) {
      validImage(preview);
      if (!dialog.open) returnFocus = document.activeElement;
      title.textContent = label; dialog.setAttribute('aria-label', label);
      download.hidden = !original;
      if (original) download.href = original; else download.removeAttribute('href');
      status.textContent = 'Loading preview…'; status.hidden = false;
      // Clear the previous case immediately, even while the next request is pending.
      img.removeAttribute('src'); assignImage(img, preview, label);
      if (!dialog.open) dialog.showModal();
    },
    close: hide,
    destroy() { if (dialog.open) hide(); dialog.remove(); }
  };
}
export function createImageWipe(container, {left, right, value = 50}) {
  validImage(left); validImage(right);
  if (Math.abs((left.width / left.height) / (right.width / right.height) - 1) > 0.002) throw new Error('Wipe inputs need matching aspect ratios; use a grid or explicitly aligned derivatives');
  const root = node('div', 'ak-report-wipe'), stage = node('div', 'ak-report-wipe-stage');
  const before = node('img', 'ak-report-wipe-base'), after = node('img', 'ak-report-wipe-top');
  const line = node('span', 'ak-report-wipe-line'), l = node('span', 'ak-report-wipe-label ak-report-left', left.label || 'Before');
  const r = node('span', 'ak-report-wipe-label ak-report-right', right.label || 'After');
  const control = node('label', 'ak-report-wipe-control', 'Compare images '), slider = node('input', ''), status = node('p', 'ak-report-media-status');
  slider.type = 'range'; slider.min = 0; slider.max = 100; slider.value = Math.max(0, Math.min(100, value));
  slider.setAttribute('aria-label', `Reveal ${right.label || 'after'} over ${left.label || 'before'}`);
  line.setAttribute('aria-hidden', 'true'); stage.style.aspectRatio = `${left.width} / ${left.height}`;
  status.setAttribute('role', 'status'); status.textContent = 'Select this view to load the comparison.';
  stage.append(before, after, l, r, line); control.append(slider); root.append(stage, control, status); container.append(root);
  const update = () => { after.style.clipPath = `inset(0 0 0 ${slider.value}%)`; line.style.left = `${slider.value}%`; slider.setAttribute('aria-valuetext', `${slider.value}% ${left.label || 'before'}, ${100 - slider.value}% ${right.label || 'after'}`); };
  slider.addEventListener('input', update); update();
  const direct = attachWipeHandle(stage, slider, {label: slider.getAttribute('aria-label'), line});
  let loaded = false, failed = false;
  const ready = () => { if (!failed && [before, after].every(i => i.complete && i.naturalWidth)) status.hidden = true; };
  for (const img of [before, after]) {
    img.addEventListener('load', ready);
    img.addEventListener('error', () => { failed = true; status.hidden = false; status.textContent = 'Comparison preview unavailable. Retry this view or use the image grid.'; });
  }
  return {
    load() {
      if (loaded && !failed) return;
      loaded = true; failed = false; status.hidden = false; status.textContent = 'Loading comparison…';
      assignImage(before, left, left.label || 'Before'); assignImage(after, right, right.label || 'After');
    },
    destroy() { direct.destroy(); before.removeAttribute('src'); after.removeAttribute('src'); root.remove(); }
  };
}
