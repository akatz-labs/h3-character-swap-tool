// Ordinary playback uses display copies; asset IDs in saved edits stay original.
const videoPreviewLoads=new WeakMap();
function videoPreview(id,attributes='class="media-wide"'){
  return `<div class="video-preview"><video ${attributes} data-video-preview="${esc(id)}" controls playsinline preload="metadata" poster="${connected?'':asset(id)+'/thumbnail'}"></video><div class="video-preview-status" role="status"><span class="render-spinner" aria-hidden="true"></span><span>Preparing preview…</span></div><button type="button" class="video-preview-retry quiet" hidden>Retry preview</button></div>`;
}
async function loadVideoPreview(video,retry=false){
  const previous=videoPreviewLoads.get(video);if(previous&&!previous.signal.aborted&&!retry)return;
  previous?.abort();const controller=new AbortController();videoPreviewLoads.set(video,controller);
  const {signal}=controller,host=video.closest('.video-preview'),status=host.querySelector('.video-preview-status'),button=host.querySelector('.video-preview-retry');
  video.dataset.previewState='preparing';status.hidden=false;button.hidden=true;
  status.innerHTML='<span class="render-spinner" aria-hidden="true"></span><span>Preparing preview…</span>';
  button.onclick=()=>{void loadVideoPreview(video,true);};
  const fail=message=>{video.dataset.previewState='failed';status.textContent=message;status.hidden=false;button.hidden=false;};
  video.addEventListener('loadeddata',()=>{video.dataset.previewState='ready';status.hidden=true;},{once:true,signal});
  video.addEventListener('error',()=>fail('Preview could not play. Try again.'),{once:true,signal});
  try{
    if(connected){video.src=asset(video.dataset.videoPreview);video.load();return;}
    const deadline=Date.now()+180000;
    while(!signal.aborted&&video.isConnected){
      const result=await api('assets/'+encodeURIComponent(video.dataset.videoPreview)+'/video-preview','POST',{retry},signal);retry=false;
      if(signal.aborted||!video.isConnected)return;
      if(result.state==='failed')throw Error('Preview could not be prepared. Try again.');
      if(result.state==='ready'){
        status.querySelector('span:last-child').textContent='Loading preview…';
        video.src=asset(result.asset);video.load();return;
      }
      if(Date.now()>deadline)throw Error('Preview is still preparing. Try again shortly.');
      await new Promise(resolve=>setTimeout(resolve,400));
    }
  }catch(error){if(!signal.aborted&&video.isConnected)fail(error.message);}
}
function previewVideoNodes(node){
  return node instanceof Element?[...(node.matches('video[data-video-preview]')?[node]:[]),...node.querySelectorAll('video[data-video-preview]')]:[];
}
const videoPreviewWatcher=new MutationObserver(records=>{
  for(const record of records){
    for(const node of record.removedNodes)for(const video of previewVideoNodes(node))if(!video.isConnected){videoPreviewLoads.get(video)?.abort();video.pause();video.removeAttribute('src');video.load();}
    for(const node of record.addedNodes)for(const video of previewVideoNodes(node))void loadVideoPreview(video);
  }
});
videoPreviewWatcher.observe(document.body,{childList:true,subtree:true});
