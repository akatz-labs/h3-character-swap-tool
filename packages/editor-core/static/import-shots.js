// One upload at a time bounds network/CPU work; every selected file is visible immediately.
const shotImports = [], importLifetime = new AbortController();
let importingShots = false;
function importsPending() { return shotImports.some(item => !['failed','ready'].includes(item.phase)); }
function importRows(query) {
  return shotImports.filter(item => item.batch === S.batch && item.file.name.toLowerCase().includes(query)).map(item => ({
    key:item.id,
    html:`<div class="shot-import ${item.phase==='failed'?'shot-import-failed':''}" data-import-id="${item.id}" aria-label="${esc(item.file.name)}">
      <div class="shot-import-heading"><span class="shot-import-thumbnail" aria-hidden="true">${uiIcon('film')}</span><strong>${esc(item.file.name)}</strong></div>
      <div class="shot-import-status" role="status">${esc(item.error || item.phase)}${['Uploading','Preparing video','Preparing preview'].includes(item.phase)?` · ${Math.floor(item.percent)}%`:''}</div>
      ${item.phase==='failed'?`<div class="row">${item.retry?`<button class="quiet" data-import-retry="${item.id}">Retry upload</button>`:''}<button class="quiet" data-import-dismiss="${item.id}">Dismiss</button></div>`:
      `<progress aria-label="${esc(item.file.name)} upload" max="100" ${['Queued','Uploading','Preparing video','Preparing preview'].includes(item.phase)?`value="${item.percent}"`:''}></progress>`}
    </div>`
  }));
}
function bindImportRows() {
  $$('[data-import-retry]').forEach(button => button.onclick = () => {
    const item=shotImports.find(item=>item.id===button.dataset.importRetry);
    item.phase='Queued';item.error='';item.percent=0;sidebar();void drainShotImports();
  });
  $$('[data-import-dismiss]').forEach(button => button.onclick = () => {
    shotImports.splice(shotImports.findIndex(item=>item.id===button.dataset.importDismiss),1);sidebar();
  });
}
function uploadShotFile(file,batch,progress) {
  if(connected)return connectedImportVideo(file,batch,progress,importLifetime.signal);
  return new Promise((resolve,reject)=>{
    const xhr=new XMLHttpRequest(),data=new FormData();
    data.append('batch_id',batch);data.append('files',file);
    const abort=()=>xhr.abort(), finish=()=>importLifetime.signal.removeEventListener('abort',abort);
    xhr.open('POST','api/import');xhr.timeout=15*60*1000;
    xhr.upload.onprogress=event=>{if(event.lengthComputable)progress('Uploading',event.loaded,event.total);};
    xhr.upload.onload=()=>progress('Processing preview');
    xhr.onload=()=>{finish();try{const result=JSON.parse(xhr.responseText);if(xhr.status>=400)throw Error(result.detail||'Import failed');resolve(result);}catch(error){reject(error);}};
    xhr.onerror=()=>{finish();reject(Error('Upload connection failed'));};
    xhr.ontimeout=()=>{finish();reject(Error('Import timed out'));};
    xhr.onabort=()=>{finish();reject(new DOMException('Upload stopped','AbortError'));};
    importLifetime.signal.addEventListener('abort',abort,{once:true});
    progress('Uploading',0,file.size);xhr.send(data);
  });
}
async function drainShotImports() {
  if(importingShots)return;
  importingShots=true;
  try {
    while(!importLifetime.signal.aborted){
      const item=shotImports.find(item=>item.phase==='Queued');
      if(!item)break;
      try{
        const result=await uploadShotFile(item.file,item.batch,(phase,loaded,total)=>{
          item.phase=phase;item.percent=total?Math.min(100,loaded/total*100):100;
          if(phase==='Processing preview'||phase==='Adding shot')item.retry=false;
          if(!importLifetime.signal.aborted)sidebar();
        });
        if(!result.clips.length)throw Error(result.errors.map(e=>e.error).join('; ')||'No video could be imported');
        item.phase='ready';
        if(importLifetime.signal.aborted)return;
        // Preserve the current editor, selection and unsaved drafts. Ready neighbours
        // become clickable without remounting the active shot.
        const hadClip=!!clip();
        await refresh(false);
        shotImports.splice(shotImports.indexOf(item),1);
        sidebar();if(!hadClip&&S.batch===item.batch)draw();
        toast(`${item.file.name} is ready`);
      }catch(error){
        item.phase='failed';
        item.error=item.retry?error.message:`${error.message}. Refresh to check saved shots before importing again.`;
        if(!importLifetime.signal.aborted)sidebar();
      }
    }
  }finally{importingShots=false;}
}
function chooseShotFiles() {
  const input=document.createElement('input');
  input.type='file';input.multiple=true;input.accept='.mp4,.mov,.mkv,.webm,.avi';
  input.hidden=true;document.body.append(input);
  input.oncancel=()=>input.remove();
  input.onchange=async()=>{
    const files=Array.from(input.files);input.remove();if(!files.length)return;
    void importShotFiles(files);
  };
  input.click();
}
document.defaultView.addEventListener('beforeunload',event=>{
  if(importsPending()){event.preventDefault();event.returnValue='';}
},{signal:importLifetime.signal});

async function importShotFiles(files) {
  try{
    if(!S.batch)S.batch=(await api('batches','POST',{name:'New project'})).id;
    for(const file of files)shotImports.push({id:'import-'+crypto.randomUUID(),file,batch:S.batch,phase:'Queued',percent:0,error:'',retry:true});
    sidebar();void drainShotImports();
  }catch(error){reportError(error);}
}
bindFileDrop(document.body,{
  kind:'video',signal:importLifetime.signal,
  acceptsTarget:target=>!$('#dialog')?.open&&!!target.closest('.layout'),
  active:active=>$('#h3-clip-browser')?.classList.toggle('h3-video-drop-active',active),
  files:files=>void importShotFiles(files),
  invalid:()=>toast('Drop video files to import shots.'),
});
