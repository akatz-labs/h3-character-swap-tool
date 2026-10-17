// Compact source video + lossless native-pixel distance fields, eight frames per
// page. Keep only three decoded pages; padding is a local threshold, not a job.
function disposeMaskPlayback() {
  const e = maskEditState;
  if (!e) return;
  e.playbackAbort?.abort(); e.presentSerial=(e.presentSerial||0)+1; e.player?.dispose(); e.player = null;
  e.pages?.clear(); e.pageRequests?.clear(); e.ready = false; e.wanted = false;
  clearTimeout(e.paddingTimer);
}
function maskPause() {
  const e = maskEditState;
  if (!e) return;
  e.wanted = false; e.player?.stop();
  if ($('#mask-play')) uiButton($('#mask-play'), 'Play', 'play', true);
}
function maskFrameUI(e) {
  if (!$('#mask-frame')) return;
  if (!e.player?.scrubbing) $('#mask-frame').value = e.frame;
  $('#mask-frame-number').value = e.start + e.frame;
  $('#mask-time').textContent = `${(e.frame/24).toFixed(2)}s`;
  $('#mask-time').title = `${(e.frame/24).toFixed(2)}s / ${((e.end-e.start)/24).toFixed(2)}s`;
  uiButton($('#mask-play'), e.wanted ? 'Pause' : 'Play', e.wanted ? 'pause' : 'play', true);
  maskDrawPoints();
  if(e.controlsViewing!==e.viewing || e.controlsReady!==e.ready)maskViewingControls(e);
}
function maskPlaybackError(e, error) {
  if (e !== maskEditState || e.playbackAbort?.signal.aborted) return;
  maskPause(); e.loading = false; e.loadError = true;
  $('#mask-loading').hidden = false; $('#mask-loading').textContent = error.message;
  $('#mask-preview-retry').hidden = false;
}
async function maskDistancePage(e, index) {
  if (e.pages.has(index)) {
    const page = e.pages.get(index); e.pages.delete(index); e.pages.set(index, page); return page;
  }
  if (e.pageRequests.has(index)) return e.pageRequests.get(index);
  const controller=e.playbackAbort, pages=e.pages, pending=e.pageRequests, preview=e.preview;
  const request = (async () => {
    const response = await fetch(asset(preview.pages[index].asset), {signal: controller.signal, cache:'force-cache'});
    if (!response.ok) throw Error('Could not load mask playback. Retry preview.');
    const buffer = await new Response(response.body.pipeThrough(new DecompressionStream('gzip'))).arrayBuffer();
    const expected = preview.pages[index].frames * preview.width * preview.height;
    if (buffer.byteLength !== expected * 2) throw Error('Mask preview frame count mismatch');
    const page = new Uint16Array(buffer);
    if(preview.encoding!=='gzip-uint16-le-delta-x')throw Error('Unsupported mask preview; reload the page.');
    for(let row=0;row<page.length;row+=preview.width)
      for(let x=1;x<preview.width;x++)page[row+x]=(page[row+x]+page[row+x-1])&65535;
    if (controller.signal.aborted) return page;
    pages.set(index, page);
    while (pages.size > 3) pages.delete(pages.keys().next().value);
    return page;
  })();
  pending.set(index, request);
  try {return await request;} finally {pending.delete(index);}
}
function maskPaint(e, frame, page) {
  if (!e.player || !$('#mask-preview')) return;
  const {width:w, height:h, page_frames:n} = e.preview;
  const canvas = $('#mask-preview');
  if (canvas.width !== w || canvas.height !== h) {canvas.width=w; canvas.height=h;}
  const context = canvas.getContext('2d'), pixels = e.maskPixels ||= context.createImageData(w,h);
  const offset = (frame % n)*w*h, data = pixels.data;
  for (let i=0;i<w*h;i++) {
    const p=i*4; data[p]=62; data[p+1]=149; data[p+2]=243;
    data[p+3]=page[offset+i] <= e.padding ? 140 : 0;
  }
  // Anchor inspection always uses the shot's first-frame mask. Keep the video
  // decoder parked so returning to Source video restores its captured frame.
  if(e.viewing==='anchor') {
    const source=$('#mask-source'),image=e.anchorImage;
    if(!image)return;
    source.width=w;source.height=h;
    const ctx=source.getContext('2d'),scale=Math.min(w/image.naturalWidth,h/image.naturalHeight);
    ctx.fillStyle='#10151d';ctx.fillRect(0,0,w,h);
    const iw=image.naturalWidth*scale,ih=image.naturalHeight*scale;
    ctx.drawImage(image,(w-iw)/2,(h-ih)/2,iw,ih);
    e.player.renderKey='';
  } else e.player.render(1,0,0);
  e.player.top.hidden=true;
  context.putImageData(pixels,0,0);
  e.frame=frame; e.loading=false; e.loadError=false; e.displayedFrame=frame;
  $('#mask-loading').hidden=true; maskFrameUI(e);
}
async function maskPresent(e) {
  if (!e.preview || e.player?.time == null || e !== maskEditState) return;
  const anchor=e.viewing==='anchor',frame = anchor?0:Math.round(e.player.time*24), index = Math.floor(frame/e.preview.page_frames);
  if(!anchor && e.seeking && frame!==e.requested)return;
  const serial = e.presentSerial = (e.presentSerial||0)+1;
  try {
    if(anchor)await maskAnchorImage(e);
    let page=e.pages.get(index);
    if (!page) {
      e.loading=true;
      $('#mask-loading').hidden=false; $('#mask-loading').textContent='Buffering mask…';
      // Pause the decoder while fetching a page, keeping the last matched pair.
      if (!e.player.video.paused) e.player.stop();
      page=await maskDistancePage(e,index);
    }
    if (e !== maskEditState || e.playbackAbort.signal.aborted || serial!==e.presentSerial) return;
    maskPaint(e,frame,page);
    // Two pages ahead, with three decoded pages retained in total.
    for(let next=index+1;next<=index+2&&next<e.preview.pages.length;next++)
      if(!e.pages.has(next))maskDistancePage(e,next).catch(()=>{});
    if (!anchor && e.wanted && e.ready && e.player.video.paused) await e.player.start();
  } catch(error) {if(serial===e.presentSerial)maskPlaybackError(e,error);}
}
async function maskPlay() {
  const e=maskEditState;
  if (!e?.ready || e.viewing==='anchor') return;
  if (e.wanted) {maskPause();return;}
  e.wanted=true; maskFrameUI(e);
  try {await e.player.start(e.frame>=e.end-e.start-1?0:null);}
  catch(error) {maskPlaybackError(e,error);}
}
async function loadMaskPlayback(retry=false) {
  const e=maskEditState;
  if (!e || !$('#mask-source')) return;
  disposeMaskPlayback();
  const controller=e.playbackAbort=new AbortController();
  const alive=()=>e===maskEditState&&!controller.signal.aborted&&!!$('#mask-source');
  e.pages=new Map(); e.pageRequests=new Map(); e.maskPixels=null; e.loading=true; e.loadError=false;
  e.loadedMask=shot().mask; e.requested=e.viewing==='anchor'?(e.sourceFrame??0):e.frame; e.seeking=false;
  $('#mask-loading').hidden=false; $('#mask-loading').textContent='Preparing playback…';
  $('#mask-preview-retry').hidden=true; $('#mask-play').disabled=true;
  const video=$('#mask-video'), timeline=$('#mask-frame');
  try {
    let result; const deadline=Date.now()+180000;
    do {
      result=await api(`clips/${S.clip}/shots/${S.shot}/mask-playback`,'POST',{retry},AbortSignal.any([controller.signal,AbortSignal.timeout(15000)]));retry=false;
      if(!alive())return;
      if(result.state==='failed')throw Error(result.error);
      if(result.state!=='ready')await new Promise(resolve=>setTimeout(resolve,250));
      if(Date.now()>deadline)throw Error('Playback is still preparing. Retry in a moment.');
    } while(result.state!=='ready');
    e.preview=result;
    await maskDistancePage(e,Math.floor(e.requested/result.page_frames));if(!alive())return;
    e.player=new H3Player.Atlas(video,$('#mask-source'),null,timeline,()=>24);
    e.player.onFrame(()=>{if(alive())maskPresent(e);});
    video.muted=!$('#mask-audio').checked; video.playbackRate=Number($('#mask-speed').value);
    video.loop=$('#mask-loop').checked;
    const loaded=e.player.event('loadeddata',e.player.epoch);
    video.src=asset(result.asset);video.load();await loaded;if(!alive())return;
    await e.player.seek(e.requested,e.end-e.start-1);if(!alive())return;
    e.ready=true; $('#mask-play').disabled=false;
    await maskPresent(e);
  } catch(error) {if(alive())maskPlaybackError(e,error);}
}
