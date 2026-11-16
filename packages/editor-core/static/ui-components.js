// Shared presentation only. Callers own data loading, selection and mutations.
// Reference: docs/ui-components.md and static/components.html.
const H3UI = (() => {
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const status = (label, tone='neutral') => `<span class="h3-ui-status" data-tone="${escape(tone)}">${escape(label)}</span>`;
  const button = ({label,action,primary=false,disabled=false}) => `<button class="h3-ui-button ${primary?'h3-ui-primary':''}" data-ui-action="${escape(action)}" ${disabled?'disabled':''}>${escape(label)}</button>`;
  const select = ({label,id,options,value}) => `<label class="h3-ui-select"><span class="h3-ui-sr">${escape(label)}</span><select id="${escape(id)}" aria-label="${escape(label)}">${options.map(o=>`<option value="${escape(o.value)}" ${o.value===value?'selected':''}>${escape(o.label)}</option>`).join('')}</select></label>`;
  const empty = (title,description) => `<div class="h3-ui-empty"><strong>${escape(title)}</strong><p>${escape(description)}</p></div>`;
  const shotRow = ({id,clipId,title,poster,mediaId=poster,duration,active,busy=false}) => `<button class="h3-ui-shot" data-open-shot="${escape(id)}" data-parent-clip="${escape(clipId)}" ${active?'aria-current="page"':''}><span class="h3-shot-thumbnail" data-media-id="${escape(mediaId)}" data-state="${poster?'loading':'empty'}" ${poster?'aria-label="Loading thumbnail" aria-busy="true"':''}>${poster?`<img src="${escape(poster)}" alt="" loading="lazy" decoding="async">`:''}</span><span class="h3-shot-label"><strong>${escape(title)}</strong><small>${escape(duration)}</small></span><span class="h3-ui-busy" role="status" aria-label="Generating" ${busy?'':'hidden'}></span></button>`;
function bindShotThumbnails(root){
  root.querySelectorAll('.h3-shot-thumbnail img').forEach(img=>{
    const host=img.parentElement;
    const finish=()=>{
      host.dataset.state=img.naturalWidth?'ready':'error';
      host.removeAttribute('aria-busy');
      if(img.naturalWidth)host.removeAttribute('aria-label');
      else host.setAttribute('aria-label','Thumbnail unavailable');
    };
    img.onload=finish;img.onerror=finish;
    if(img.complete)finish();
  });
}

  const mediaCard = ({id,title,poster,duration,download,state='success',label='',selected=false,selectable=false}) => {
    const done=state==='success',busy=['queued','waiting','running','submitting','cancelling'].includes(state);
    return `<article class="h3-ui-media-card" data-state="${escape(state)}" ${selected?'data-selected="true"':''}>
      <button class="h3-ui-card-open" data-output-open="${escape(id)}" aria-label="${escape(done?'Play '+title:'Inspect '+title)}"><span class="h3-ui-poster">${poster?`<img src="${escape(poster)}" alt="" loading="lazy">`:''}${done?'<span class="h3-ui-play" aria-hidden="true">▶</span>':`<span class="h3-ui-card-state">${busy?'<span class="h3-ui-busy" aria-hidden="true"></span>':''}${escape(label||state)}</span>`}${duration?`<span class="h3-ui-duration">${escape(duration)}</span>`:''}${done&&label?status(label,label==='Final'?'accent':'neutral'):''}</span><strong class="h3-ui-card-title">${escape(title)}</strong></button>
      ${done&&download?`<a class="h3-ui-download" href="${escape(download)}" aria-label="Download ${escape(title)}" title="Download video">↓</a>`:''}
      ${selectable?`<button class="h3-ui-choice" data-output-choose="${escape(id)}" aria-label="Choose ${escape(title)}" aria-pressed="${selected}">${selected?'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m5 12 4 4L19 6"/></svg>':''}<span>${selected?'Selected':'Use take'}</span></button>`:''}
    </article>`;
  };
  const parameters = rows => `<dl class="h3-ui-parameters">${rows.filter(([,value])=>value!==undefined&&value!==null).map(([label,value])=>`<div><dt>${escape(label)}</dt><dd>${escape(value)}</dd></div>`).join('')}</dl>`;
  const failure = ({message,suggestion,details}) => `<section class="h3-ui-failure" aria-label="Generation failure"><h3>Generation failed</h3><p>${escape(message)}</p>${suggestion?`<strong>Suggestion</strong><p>${escape(suggestion)}</p>`:''}${details?`<details><summary>Technical details</summary><pre>${escape(details)}</pre></details>`:''}</section>`;
  // media/inspector/footer are trusted component markup, never unescaped user text.
  const viewer = ({media,inspector,footer=''}) => `<div class="h3-ui-viewer"><section class="h3-ui-viewer-media">${media}</section><aside class="h3-ui-inspector" aria-label="Generation parameters"><div class="h3-ui-inspector-scroll">${inspector}</div>${footer?`<footer>${footer}</footer>`:''}</aside></div>`;
  return {escape,status,button,select,empty,shotRow,bindShotThumbnails,mediaCard,parameters,failure,viewer};
})();
