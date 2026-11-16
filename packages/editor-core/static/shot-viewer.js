// One persistent viewer and playhead for cast masks, captures and segments.
function normalizeShotPage() {
  if (['anchors','shots','review','batch','refine'].includes(S.tab)) S.tab = 'render';
}
function shotViewerHeaderHtml(s) {
  return '<div class="panel-title shot-viewer-header"><div id="cast-mask-selector" class="cast-mask-selector"></div></div>';
}
function bindShotViewer(c,s) {
  S.viewerKey=`${c.id}:${s.id}`;S.viewerMode='mask';S.requestedViewerMode=null;
  bindCastMaskSelector();
  syncShotViewer(s);
}
function setShotViewerMode() {
  S.viewerMode='mask';
  if(maskEditState){maskDrawPoints();maskLoadFrame();}
}
function syncShotViewer(s=shot()) {
  if(maskEditState)maskViewingControls(maskEditState);
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
    e.loading=true;$('#mask-loading').hidden=false;$('#mask-loading').textContent='Loading shot anchor…';$('#mask-loading').dataset.state='loading';
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
  const capture=$('#shot-capture');if(capture)capture.disabled=anchor||!e.ready;
  $('#mask-points')?.toggleAttribute('hidden',anchor);
  const label=$('#mask-anchor-label');
  if(label){label.hidden=!anchor;label.textContent=shot().anchor_conditioning==='first_frame'?'Shot anchor · first-frame guide':'Shot anchor · appearance reference';}
  updateMaskEditorStatus();
}
async function maskAnchorImage(e) {
  const id=shot().anchor;if(!id)throw Error('Add a shot anchor in Anchors before viewing it.');
  if(e.anchorAsset===id&&e.anchorImage)return e.anchorImage;
  if(e.anchorAsset===id&&e.anchorRequest)return e.anchorRequest;
  e.anchorAsset=id;
  const image=new Image();image.decoding='async';image.src=imageAsset(id,1280);
  const request=image.decode().then(()=>{
    if(e.anchorAsset===id){e.anchorImage=image;e.anchorRequest=null;}return image;
  }).catch(()=>{
    if(e.anchorAsset===id)e.anchorRequest=null;
    throw Error('Shot anchor could not load. Retry preview or choose Source video.');
  });
  e.anchorRequest=request;return request;
}
