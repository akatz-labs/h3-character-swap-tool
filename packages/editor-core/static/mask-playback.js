// Prepare a bounded browser-local overlay cache independently of source playback.
// Preview sampling never changes the native masks submitted for generation.
const maskPreviewCache = new Map();
function savedImportPlayback(c,s) {
  // A full, unmasked shot can play the already validated Platform proxy.
  // Do not rebuild video and empty mask atlases in a CPU transaction on open.
  const prepared=c.platform_playback, normalized=c.platform_ingestion?.normalized;
  if(prepared?.state==='ready' && normalized &&
    (c.render_source||c.source)==='asset-'+normalized.id &&
    c.frames===normalized.frames && c.audio===normalized.audio &&
    c.dimensions[0]===normalized.width && c.dimensions[1]===normalized.height &&
    s.start===0 && s.end===c.frames && !s.mask)
    return {...prepared,encoding:'none',pages:[],page_frames:8};
  const saved=c.import_playback,spec=saved?.spec;
  return spec && spec.source===(c.render_source||c.source) &&
    spec.start===s.start && spec.end===s.end && spec.audio===c.audio &&
    JSON.stringify(spec.dimensions)===JSON.stringify(c.dimensions) &&
    (spec.mask??null)===(s.mask??null) ? saved : null;
}
async function requestPlayback(path,body,signal){
  // These endpoints prepare/cache a preview, never submit a generation job.
  // Recover transient transport failures without repeating cache invalidation.
  for(let attempt=0;attempt<3;attempt++){
    signal.throwIfAborted();
    try{return await api(path,'POST',attempt?{...body,retry:false}:body,AbortSignal.any([signal,AbortSignal.timeout(15000)]));}
    catch(error){
      if(signal.aborted)throw error;
      if(error.name!=='TimeoutError'&&!(error instanceof TypeError))throw error;
      if(attempt===2)throw Error('Preview connection timed out. Check your connection, then choose Retry preview.');
      await new Promise(resolve=>setTimeout(resolve,1000));
    }
  }
}
function disposeMaskPlayback() {
  const e = maskEditState;
  if (!e) return;
  e.playbackAbort?.abort(); e.presentSerial=(e.presentSerial||0)+1; e.player?.dispose(); e.player = null;
  if(e.maskVideo){e.maskVideo.pause();e.maskVideo.removeAttribute('src');e.maskVideo.load();e.maskVideo=null;}
  e.pages = new Map(); e.pageRequests = new Map(); e.maskDecode = null; e.ready = false; e.wanted = false;
  clearTimeout(e.paddingTimer);
  const overlay = $('#mask-preview');
  if (overlay) overlay.getContext('2d').clearRect(0, 0, overlay.width, overlay.height);
  if ($('#mask-points')) $('#mask-points').innerHTML = '';
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
  syncSegmentPlayhead(e.frame);
  $('#mask-time').textContent = `${(e.frame/24).toFixed(2)}s`;
  $('#mask-time').title = `${(e.frame/24).toFixed(2)}s / ${((e.end-e.start)/24).toFixed(2)}s`;
  uiButton($('#mask-play'), e.wanted ? 'Pause' : 'Play', e.wanted ? 'pause' : 'play', true);
  maskDrawPoints();
  if(e.controlsViewing!==e.viewing || e.controlsReady!==e.ready)maskViewingControls(e);
}
function maskPlaybackError(e, error) {
  if (e !== maskEditState || e.playbackAbort?.signal.aborted) return;
  maskPause(); e.loading = false; e.loadError = true;
  $('#mask-loading').hidden = false; $('#mask-loading').textContent = error.message; $('#mask-loading').dataset.state='error';
  $('#mask-preview-retry').hidden = false;
}
function maskCacheStatus(message) {
  let status=$('#mask-cache-status');
  if(!status && $('#mask-show')) {
    status=document.createElement('span');status.id='mask-cache-status';status.className='note';status.setAttribute('role','status');
    $('#mask-show').closest('label').after(status);
  }
  if(status){status.textContent=message;status.hidden=!message;}
}
function cachedMaskPreview(preview) {
  const key=JSON.stringify([preview.encoding,preview.mask_asset,preview.pages,preview.width,preview.height,preview.mask_start,preview.mask_end]);
  if(maskPreviewCache.has(key))return maskPreviewCache.get(key);
  // At most 96 MiB of distance fields per prepared shot. Distances retain source
  // pixel units, so the expansion setting remains identical at any preview size.
  const frames=preview.encoding==='mask-video'?preview.mask_end-preview.mask_start:preview.pages.reduce((n,p)=>n+p.frames,0);
  const scale=Math.min(1,Math.sqrt(Math.min(160000,48*1024*1024/Math.max(1,frames))/(preview.width*preview.height)));
  const w=Math.max(1,Math.round(preview.width*scale)),h=Math.max(1,Math.round(preview.height*scale));
  const task=prepareMaskPreview(preview,w,h);
  maskPreviewCache.set(key,task);
  while(maskPreviewCache.size>2)maskPreviewCache.delete(maskPreviewCache.keys().next().value);
  task.catch(()=>{if(maskPreviewCache.get(key)===task)maskPreviewCache.delete(key);});
  return task;
}
async function prepareMaskPreview(preview,w,h) {
  const pages=new Map(),result={...preview,width:w,height:h,page_frames:1};
  const signal=AbortSignal.timeout(180000);
  if(preview.encoding==='mask-video') {
    const response=await fetch(asset(preview.mask_asset),{signal,cache:'force-cache'});
    if(!response.ok)throw Error('Could not download mask. Retry preview.');
    const url=URL.createObjectURL(await response.blob()),video=document.createElement('video');
    video.muted=true;video.playsInline=true;
    const event=(name,action)=>new Promise((resolve,reject)=>{
      const cleanup=()=>{clearTimeout(timer);video.removeEventListener(name,ok);video.removeEventListener('error',bad);signal.removeEventListener('abort',bad);};
      const ok=()=>{cleanup();resolve();},bad=()=>{cleanup();reject(Error('Could not prepare mask. Retry preview.'));};
      const timer=setTimeout(bad,15000);video.addEventListener(name,ok,{once:true});video.addEventListener('error',bad,{once:true});signal.addEventListener('abort',bad,{once:true});action();
    });
    try {
      await event('loadeddata',()=>{video.src=url;video.load();});
      const canvas=document.createElement('canvas');canvas.width=w;canvas.height=h;
      const ctx=canvas.getContext('2d',{willReadFrequently:true});
      for(let index=preview.mask_start;index<preview.mask_end;index++) {
        signal.throwIfAborted();const time=(index-preview.mask_start+.25)/24;
        if(Math.abs(video.currentTime-time)>.001)await event('seeked',()=>{video.currentTime=time;});
        ctx.drawImage(video,0,0,w,h);
        const page=maskDistances(ctx.getImageData(0,0,w,h).data,w,h),scale=preview.width/w;
        for(let i=0;i<page.length;i++)page[i]=Math.min(65535,Math.round(page[i]*scale));
        pages.set(index,page);
      }
    } finally {video.removeAttribute('src');video.load();URL.revokeObjectURL(url);}
  } else {
    if(preview.encoding!=='gzip-uint16-le-delta-x')throw Error('Unsupported mask preview; reload the page.');
    for(let index=0;index<preview.pages.length;index++) {
      const response=await fetch(asset(preview.pages[index].asset),{signal,cache:'force-cache'});
      if(!response.ok)throw Error('Could not download mask. Retry preview.');
      const buffer=await new Response(response.body.pipeThrough(new DecompressionStream('gzip'))).arrayBuffer();
      if(buffer.byteLength!==preview.pages[index].frames*preview.width*preview.height*2)throw Error('Mask preview frame count mismatch');
      const original=new Uint16Array(buffer);
      for(let row=0;row<original.length;row+=preview.width)
        for(let x=1;x<preview.width;x++)original[row+x]=(original[row+x]+original[row+x-1])&65535;
      for(let f=0;f<preview.pages[index].frames;f++) {
        const page=new Uint16Array(w*h),offset=f*preview.width*preview.height;
        for(let y=0;y<h;y++)for(let x=0;x<w;x++)page[y*w+x]=original[offset+Math.floor(y*preview.height/h)*preview.width+Math.floor(x*preview.width/w)];
        pages.set(index*preview.page_frames+f,page);
      }
    }
  }
  return {preview:result,pages};
}
function maskPaint(e, frame, page) {
  if (!e.player || !$('#mask-preview')) return;
  const {width:w, height:h, page_frames:n} = e.preview;
  const canvas = $('#mask-preview');
  if (canvas.width !== w || canvas.height !== h) {canvas.width=w; canvas.height=h;}
  const context = canvas.getContext('2d');
  // Cache preparation can reduce preview dimensions after source playback starts.
  // ImageData retains its own row stride even when the canvas is resized.
  if (!e.maskPixels || e.maskPixels.width !== w || e.maskPixels.height !== h)
    e.maskPixels = context.createImageData(w,h);
  const pixels = e.maskPixels;
  const offset = (frame % n)*w*h, data = pixels.data, color=selectedMaskColor().slice(1).match(/../g).map(v=>parseInt(v,16));
  for (let i=0;i<w*h;i++) {
    const p=i*4; data[p]=color[0]; data[p+1]=color[1]; data[p+2]=color[2];
    data[p+3]=page && page[offset+i] <= e.padding ? 140 : 0;
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
    const page=e.pages.get(index);
    if (e !== maskEditState || e.playbackAbort.signal.aborted || serial!==e.presentSerial) return;
    maskPaint(e,frame,page);
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
  e.loadedMask=maskShot().mask; e.requested=e.viewing==='anchor'?(e.sourceFrame??0):e.frame; e.seeking=false;
  $('#mask-loading').hidden=false; $('#mask-loading').textContent='Loading preview…'; $('#mask-loading').dataset.state='loading';
  $('#mask-preview-retry').hidden=true; $('#mask-play').disabled=true;
  const video=$('#mask-video'), timeline=$('#mask-frame');
  try {
    let result=savedImportPlayback(clip(),maskShot()); const deadline=Date.now()+180000;
    while(!result || result.state!=='ready') {
      result=await requestPlayback(`clips/${S.clip}/shots/${S.shot}/mask-playback`,{retry,...(e.trackId?{track_id:e.trackId}:{})},controller.signal);retry=false;
      if(!alive())return;
      if(result.state==='failed')throw Error(result.error);
      if(result.state!=='ready')await new Promise(resolve=>setTimeout(resolve,250));
      if(Date.now()>deadline)throw Error('Playback is still preparing. Retry in a moment.');
    }
    e.preview=result;
    maskCacheStatus(result.encoding==='none'?'':'Preparing mask…');
    if(result.encoding!=='none')void cachedMaskPreview(result).then(cache=>{
      if(!alive())return;
      e.preview=cache.preview;e.pages=cache.pages;maskCacheStatus('');void maskPresent(e);
    }).catch(error=>{if(alive()){maskCacheStatus(error.message);$('#mask-preview-retry').hidden=false;}});
    e.player=new H3Player.Atlas(video,$('#mask-source'),null,timeline,()=>24);
    e.player.onFrame(()=>{if(alive())maskPresent(e);});
    video.muted=!$('#mask-audio').checked; video.playbackRate=Number($('#mask-speed').value);
    video.loop=$('#mask-loop').checked;
    video.addEventListener('waiting',()=>{
      if(!alive()||!e.ready)return;
      $('#mask-loading').hidden=false;$('#mask-loading').dataset.state='loading';$('#mask-loading').textContent='Buffering preview…';
    },{signal:controller.signal});
    video.addEventListener('canplay',()=>{if(alive()&&e.ready)void maskPresent(e);},{signal:controller.signal});
    const loaded=e.player.event('loadeddata',e.player.epoch);
    video.src=asset(result.asset);video.load();await loaded;if(!alive())return;
    await e.player.seek(e.requested,e.end-e.start-1);if(!alive())return;
    e.ready=true; $('#mask-play').disabled=false;
    await maskPresent(e);
  } catch(error) {if(alive())maskPlaybackError(e,error);}
}

// Hosted masks are grayscale segment videos. Cached previews use the same
// Euclidean expansion as native masks.
function maskDistances(pixels,w,h) {
  const result=new Float64Array(w*h), f=new Float64Array(Math.max(w,h)),d=new Float64Array(f.length),v=new Int32Array(f.length),z=new Float64Array(f.length+1);
  for(let i=0;i<result.length;i++)result[i]=pixels[i*4]>127?0:1e12;
  function transform(n){let k=0;v[0]=0;z[0]=-Infinity;z[1]=Infinity;
    for(let q=1;q<n;q++){let s;do{s=((f[q]+q*q)-(f[v[k]]+v[k]*v[k]))/(2*q-2*v[k]);if(s<=z[k])k--;else break;}while(k>=0);k++;v[k]=q;z[k]=s;z[k+1]=Infinity;}
    k=0;for(let q=0;q<n;q++){while(z[k+1]<q)k++;d[q]=(q-v[k])**2+f[v[k]];}}
  for(let y=0;y<h;y++){for(let x=0;x<w;x++)f[x]=result[y*w+x];transform(w);for(let x=0;x<w;x++)result[y*w+x]=d[x];}
  const output=new Uint16Array(w*h);
  for(let x=0;x<w;x++){for(let y=0;y<h;y++)f[y]=result[y*w+x];transform(h);for(let y=0;y<h;y++)output[y*w+x]=Math.min(65535,Math.ceil(Math.sqrt(d[y])));}
  return output;
}
