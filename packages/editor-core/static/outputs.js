// Output orchestration. Presentation primitives live in ui-components.js.
function shotTakeOutputs(c,s){
  const rows=(s.segments||[]).flatMap((seg,n)=>seg.takes.map((take,i)=>({id:take.id,title:`Segment ${n+1} · Take ${i+1}`,segment:seg.id,take,state:'success',order:take.created||0,duration:((take.end-take.start)/24).toFixed(1)+'s'})));
  for(const [i,take] of (s.finals||[]).entries())rows.push({id:take.id,title:`Final · Version ${i+1}`,take,final:true,state:'success',order:take.created||0,duration:((take.end-take.start)/24).toFixed(1)+'s'});
  for(const [i,take] of s.renders.entries())rows.push({id:take.id,title:take.segment_chain?`Earlier final ${i+1}`:`Whole-shot take ${i+1}`,take,final:!!take.segment_chain,state:'success',order:take.created||0,duration:((s.end-s.start)/24).toFixed(1)+'s'});
  return rows;
}
function shotOutputs(c,s){
  const rows=shotTakeOutputs(c,s);
  for(const job of S.data.jobs.filter(j=>j.clip_id===c.id&&j.shot_id===s.id&&['render','guide_render','shot_final','segment_assemble'].includes(j.kind)&&!['success','quoted'].includes(j.state))){
    const scope=segmentJobScope(job),unfinished=scope.filter(v=>segmentJobPending(job,v.id));
    if(scope.length&&!unfinished.length)continue;
    const seg=unfinished[0],index=s.segments?.findIndex(v=>v.id===seg?.id);
    rows.push({id:job.id,title:job.kind==='shot_final'?'Create final':index>=0?`Segment ${index+1} · Generation`:'Shot generation',job,segment:seg?.id,state:job.state,order:job.created||0,duration:seg?((seg.end-seg.start)/24).toFixed(1)+'s':''});
  }
  return rows.sort((a,b)=>b.order-a.order);
}
function outputCard(row,c,s,selectable=false,selected=false){
  return H3UI.mediaCard({id:row.id,title:row.title,poster:row.take?.poster?imageAsset(row.take.poster,640):row.take?.asset&&!connected?asset(row.take.asset)+'/thumbnail':imageAsset(s.source_frame||c.poster,640),duration:row.duration,download:!selectable&&row.take?.asset?asset(row.take.asset,true):null,state:row.state,label:row.final?'Final':row.job?renderStatus(row.job):'',selectable,selected});
}
function drawOutputToolbar(){
  const panel=$('.render-results');
  panel.querySelector('.panel-title').innerHTML=`<div class="h3-output-title"><h2>Outputs <span id="render-count" class="note"></span></h2>${H3UI.button({label:'Create final',action:'create-final',primary:true})}</div><div class="h3-output-filters">${H3UI.select({label:'Filter by segment',id:'output-segment',value:'all',options:[{value:'all',label:'All segments'},{value:'final',label:'Finals'},...(shot().segments||[]).map((s,i)=>({value:s.id,label:`Segment ${i+1}`}))]})}${H3UI.select({label:'Filter by status',id:'render-filter',value:'all',options:[{value:'all',label:'All statuses'},{value:'completed',label:'Completed'},{value:'active',label:'In progress'},{value:'failed',label:'Failed / cancelled'}]})}<span class="note">Newest first</span></div>`;
  $('#output-segment').onchange=$('#render-filter').onchange=()=>renderOutputs();
  $('[data-ui-action="create-final"]').onclick=guarded(openFinalAssembly);
}
function renderOutputs(){
  const c=clip(),s=shot(),host=$('#render-history-cards');if(!host)return;
  const filter=$('#output-segment');
  if(filter){const value=filter.value,signature=(s.segments||[]).map(v=>v.id).join();if(filter.dataset.segments!==signature){filter.innerHTML='<option value="all">All segments</option><option value="final">Finals</option>'+(s.segments||[]).map((seg,i)=>`<option value="${esc(seg.id)}">Segment ${i+1}</option>`).join('');filter.value=[...filter.options].some(o=>o.value===value)?value:'all';filter.dataset.segments=signature;}}
  const rows=shotOutputs(c,s),segment=$('#output-segment')?.value||'all',status=$('#render-filter')?.value||'all';
  const busy=['submitting','queued','waiting','running','cancelling'];
  const visible=rows.filter(r=>(segment==='all'||(segment==='final'?r.final:r.segment===segment))&&(status==='all'||(status==='completed'?r.state==='success':status==='active'?busy.includes(r.state):!busy.includes(r.state)&&r.state!=='success')));
  $('#render-count').textContent=String(rows.length);
  reconcileItems(host,visible.length?visible.map(row=>({key:row.id,html:outputCard(row,c,s)})):[{key:'empty',html:H3UI.empty(rows.length?'No outputs in this view':'No outputs yet',rows.length?'Choose another filter to see your generations.':'Generate a segment to see its takes here.')}]);
  host.querySelectorAll('[data-output-open]').forEach(button=>button.onclick=guarded(()=>openOutput(rows.find(r=>r.id===button.dataset.outputOpen),c,s)));
}
function outputImages(items,label){
  return items.length?`<h3>${esc(label)}</h3><div class="h3-ui-inspector-images">${items.map(v=>`<figure><img src="${imageAsset(v.asset,256)}" alt="${esc(v.label)}" loading="lazy"><figcaption>${esc(v.label)}</figcaption></figure>`).join('')}</div>`:'';
}
async function openOutput(row,c=clip(),s=shot(),previewParent=null){
  const take=row.take;let data=take;
  if(row.job){
    const detail=await api(`clips/${c.id}/shots/${s.id}/segments/attempts/${row.job.id}`);
    const seg=detail.segments.find(v=>v.id===row.segment)||detail.segments[0];
    data={...detail.shot,...detail.generation,...detail.render_spec,...seg,settings:seg||{},prompt:seg?.performance_prompt??detail.shot?.prompt,model_offloading:detail.options?.model_offloading,cast_inputs_snapshot:seg?.cast_inputs_snapshot,guide_inputs_snapshot:seg?.guide_inputs_snapshot};
  }
  const failed=row.job&&['failed','interrupted','unconfirmed'].includes(row.state),capacity=row.job?.data.capacity_failure;
  const error=row.job?.data.error||row.job?.data.phase||'The generation could not finish.';
  const memoryFailure=capacity||/out of memory|cuda.*memory|allocation/i.test(error);
  const failure=failed?H3UI.failure({message:memoryFailure?'The worker ran out of GPU memory.':error.split('\n')[0].slice(0,240),suggestion:memoryFailure&&capacity?.offloading===false?'Enable model offloading in Render settings before generating again.':memoryFailure?'Review the resolution and segment length in Render settings before generating again.':'Review the saved parameters and technical details before generating again.',details:error}):'';
  const chars=data?.cast_inputs_snapshot?.characters||[],guides=data?.guide_inputs_snapshot?.guides||[];
  const mode=data?.render_mode||data?.settings?.render_mode;
  let inspector=failure+(row.job&&!failed?`<p role="status">${esc(renderStatus(row.job))} · ${esc(row.job.data.phase||'')}</p>`:'');
  if(row.final){
    inspector+='<h3>Assembly</h3>'+H3UI.parameters((data.segment_takes||[]).map((v,i)=>[`Segment ${i+1}`,v.label||(()=>{const seg=(s.segments||[]).find(s=>s.id===v.segment_id),index=seg?.takes.findIndex(t=>t.id===v.take_id);return index>=0?'Take '+(index+1):'Saved take';})()]));
  }else{
    inspector+='<h3>Generation parameters</h3>'+H3UI.parameters([
      ['Method','Character swap'],
      ['Model',data?.recipe?.model],['LoRA',mode==='guides'?'H3 Ripple V1':row.job&&mode==='swap_pika'?'Pika · Character swap V1.5':data?renderLoRALabel(data):null],['Seed',data?.seed],
      ['Resolution',data?.dimensions?.join(' × ')],['Frames',data?.end!==undefined?data.end-data.start:null],['Steps',data?.recipe?.steps],['Sampler',data?.recipe?.sampler],
      ['LoRA strength',data?.recipe?.edit_strength],['History frames',data?.history_frames],
      ['Source audio',data?.use_source_audio===undefined?'Not recorded':data.use_source_audio?'On':'Off'],
      ['Mask',mode==='guides'?'None':data?.cast_inputs_snapshot?(n=>n?`${n} mask${n===1?'':'s'}`:'None')(data.cast_inputs_snapshot.mask_tracks?.length||0):data?.use_mask===undefined?'Not recorded':data.use_mask?'On':'Off'],
      ['Model offloading',data?.model_offloading===undefined?'Not recorded':data.model_offloading?'On':'Off']]);
    inspector+=outputImages(chars.map(v=>({asset:v.reference,label:v.name})),'Characters');
    if(data?.prompt)inspector+=`<h3>Prompt</h3><pre>${esc(data.prompt)}</pre>`;
    inspector+=outputImages(guides.map(v=>({asset:v.asset,label:'F'+(v.frame-(data.guide_inputs_snapshot.shot_start||s.start))})),'Guides');
  }
  const versions=take?[{value:take.asset,label:'Video'},...(take.comparison?[{value:take.comparison,label:'Side by side'}]:[]),...(take.comparison_reference?[{value:take.comparison_reference,label:'With character references'}]:[])]:[];
  const footer=(take?`<a class="h3-ui-button" id="output-download" href="${asset(take.asset,true)}">${uiIcon('download')}Download video</a>`:'')+(previewParent?H3UI.button({label:'Back to selection',action:'back-selection'}):'');
  const player=take?`${versions.length>1?`<div class="h3-output-view-mode">${H3UI.select({label:'Video version',id:'output-version',options:versions,value:take.asset})}</div>`:''}<div id="output-video">${videoPreview(take.asset,'class="segment-take-player"')}</div>`:`<div class="h3-ui-source-preview"><img src="${imageAsset(s.source_frame||c.poster,960)}" alt="Source preview"><small>Source preview</small><strong>${failed?'No output generated':esc(renderStatus(row.job))}</strong></div>`;
  const html=H3UI.viewer({media:player,inspector,footer});
  let root;
  if(previewParent){
    root=openTakePreview(row.title,html,previewParent);
    root.querySelector('[data-ui-action="back-selection"]').onclick=()=>root.close();
  }else{
    dialog(row.title,html);root=$('#dialog');root.classList.add('output-modal');
  }
  const version=root.querySelector('#output-version');
  if(version)version.onchange=e=>{root.querySelector('#output-video video')?.pause();root.querySelector('#output-video').innerHTML=videoPreview(e.target.value,'class="segment-take-player"');root.querySelector('#output-download').href=asset(e.target.value,true);};
}

// A take preview owns its dismissal; the assembly DOM and selection draft stay mounted.
function openTakePreview(title,html,parent){
  const trigger=document.activeElement,viewer=document.createElement('dialog');
  viewer.className='h3-output-preview output-modal';
  viewer.setAttribute('aria-labelledby','take-preview-title');
  viewer.innerHTML=`<div class="dialog-head"><h2 id="take-preview-title">${esc(title)}</h2><button type="button" class="h3-icon-button quiet" aria-label="Close preview">${uiIcon('close')}</button></div><div class="h3-output-preview-body">${html}</div>`;
  const closeWithParent=()=>viewer.close();
  parent.addEventListener('close',closeWithParent);
  viewer.addEventListener('close',event=>{
    event.stopPropagation();viewer.querySelectorAll('video').forEach(video=>video.pause());
    parent.removeEventListener('close',closeWithParent);viewer.remove();
    if(parent.open&&trigger?.isConnected)trigger.focus({preventScroll:true});
  },{once:true});
  viewer.addEventListener('cancel',event=>{event.preventDefault();event.stopPropagation();viewer.close();});
  viewer.addEventListener('click',event=>{if(event.target===viewer)viewer.close();});
  viewer.querySelector('[aria-label="Close preview"]').onclick=()=>viewer.close();
  parent.after(viewer);viewer.showModal();return viewer;
}
