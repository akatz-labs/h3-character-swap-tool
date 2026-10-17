// Viewer modes change local presentation, never the shot or segment selection.
function normalizeShotPage() {
  // Preserve old Anchors bookmarks without retaining a separate workspace.
  if (S.tab === 'anchors') { S.tab = 'render'; S.requestedViewerMode = 'anchors'; }
}
function shotViewerHeaderHtml(s) {
  return `<div class="panel-title shot-viewer-header"><div class="shot-viewer-tabs" role="tablist" aria-label="Shot viewer">
    <button id="shot-mask-tab" role="tab" aria-selected="true" aria-controls="shot-mask-panel" data-viewer-mode="mask">${uiIcon('play')}Actor mask</button>
    <button id="shot-anchors-tab" role="tab" aria-selected="false" aria-controls="shot-anchors-panel" data-viewer-mode="anchors" tabindex="-1">${uiIcon('image')}Anchors</button>
    </div><div id="mask-viewing-controls" class="mask-viewing-controls"><span class="note h3-coverage" id="mask-coverage"></span><label for="mask-viewing">Viewing</label><select id="mask-viewing" aria-label="Viewing"><option value="source">Source video</option><option value="anchor" ${s.anchor?'':'disabled'}>Shot anchor</option></select></div></div>`;
}
function bindShotViewer(c,s) {
  const key = `${c.id}:${s.id}`;
  S.viewerMode = S.requestedViewerMode || (S.viewerKey === key ? S.viewerMode : 'mask') || 'mask';
  S.viewerKey = key; S.requestedViewerMode = null;
  bindAnchorEditor(c,s);
  const tabs = $$('[data-viewer-mode]');
  tabs.forEach((button,index) => {
    button.onclick = () => setShotViewerMode(button.dataset.viewerMode);
    button.onkeydown = event => {
      const next = event.key==='Home'?0:event.key==='End'?tabs.length-1:
        event.key==='ArrowRight'?(index+1)%tabs.length:event.key==='ArrowLeft'?(index+tabs.length-1)%tabs.length:null;
      if(next===null)return;
      event.preventDefault();tabs[next].focus();setShotViewerMode(tabs[next].dataset.viewerMode);
    };
  });
  $('#mask-viewing').onchange = event => maskSetViewing(event.target.value);
  setShotViewerMode(S.viewerMode);
  syncShotViewer(s);
}
function setShotViewerMode(mode) {
  S.viewerMode = mode === 'anchors' ? 'anchors' : 'mask';
  maskPause();
  const anchors = S.viewerMode === 'anchors';
  $('#shot-mask-panel').hidden = anchors;
  $('#shot-anchors-panel').hidden = !anchors;
  $('#mask-viewing-controls').hidden = anchors;
  $$('[data-viewer-mode]').forEach(button => {
    const selected = button.dataset.viewerMode === S.viewerMode;
    button.setAttribute('aria-selected',String(selected));button.tabIndex=selected?0:-1;
  });
  if(!anchors){maskDrawPoints();maskLoadFrame();}
}
function syncShotViewer(s=shot()) {
  const select=$('#mask-viewing'),e=maskEditState;
  if(!select||!e)return;
  select.querySelector('[value="anchor"]').disabled=!s.anchor;
  if(e.viewing==='anchor'&&!s.anchor){maskSetViewing('source');return;}
  if(e.anchorAsset!==s.anchor){
    e.anchorAsset=s.anchor;e.anchorImage=null;e.anchorRequest=null;
    if(e.viewing==='anchor')void maskPresent(e);
  }
  select.value=e.viewing==='anchor'?'anchor':'source';
  maskViewingControls(e);
}
function maskSetViewing(value, present=true) {
  const e=maskEditState;if(!e)return;
  maskPause();e.presentSerial=(e.presentSerial||0)+1;
  if(e.viewing!=='anchor')e.sourceFrame=e.player?.time!=null?Math.round(e.player.time*24):e.frame;
  e.viewing=value==='anchor'&&shot().anchor?'anchor':'source';
  e.selected=null;
  // Source playback stays parked at its exact frame while the anchor is viewed.
  // Clearing the decoder render key restores it after painting a still there.
  if(e.player)e.player.renderKey='';
  if(e.viewing==='anchor'&&!e.anchorImage){
    e.loading=true;$('#mask-loading').hidden=false;$('#mask-loading').textContent='Loading shot anchor…';
  }
  maskViewingControls(e);
  if(present)void maskPresent(e);
}
function maskViewingControls(e) {
  const anchor=e.viewing==='anchor';
  e.controlsViewing=e.viewing;e.controlsReady=e.ready;
  if($('#mask-viewing'))$('#mask-viewing').value=anchor?'anchor':'source';
  for(const selector of ['#mask-play','#mask-frame','#mask-frame-number','#mask-prev','#mask-next','#mask-speed','#mask-loop','#mask-audio']){
    const control=$(selector);if(control)control.disabled=anchor||!e.ready;
  }
  $$('[data-mask-tool], [data-mask-frame]').forEach(button=>button.disabled=anchor);
  $('#mask-points')?.toggleAttribute('hidden',anchor);
  const label=$('#mask-anchor-label');
  if(label){label.hidden=!anchor;label.textContent=`Shot anchor · first frame (source ${e.start})`;}
  updateMaskEditorStatus();
}
async function maskAnchorImage(e) {
  const id=shot().anchor;if(!id)throw Error('Add a shot anchor in Anchors before viewing it.');
  if(e.anchorAsset===id&&e.anchorImage)return e.anchorImage;
  if(e.anchorAsset===id&&e.anchorRequest)return e.anchorRequest;
  e.anchorAsset=id;
  const image=new Image();image.decoding='async';image.src=asset(id);
  const request=image.decode().then(()=>{
    if(e.anchorAsset===id){e.anchorImage=image;e.anchorRequest=null;}return image;
  }).catch(()=>{
    if(e.anchorAsset===id)e.anchorRequest=null;
    throw Error('Shot anchor could not load. Retry preview or choose Source video.');
  });
  e.anchorRequest=request;return request;
}
