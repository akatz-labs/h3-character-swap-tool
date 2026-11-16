// View-specific selection draft. Never calls the take/settings restoration API.
async function openFinalAssembly(){
  await flushShotAutosave();const c=await api('clips/'+clip().id),s=c.shots.find(v=>v.id===shot().id);
  const draft={revision:c.revision,request_id:crypto.randomUUID(),take_ids:Object.fromEntries((s.segments||[]).map(seg=>[seg.id,(s.finals?.at(-1)?.segment_takes||[]).find(v=>v.segment_id===seg.id)?.take_id||seg.selected_take||(seg.takes.length===1?seg.takes[0].id:'')]))};
  for(const seg of s.segments||[])if(!seg.takes.some(t=>t.id===draft.take_ids[seg.id]))draft.take_ids[seg.id]='';
  function draw(){
    const scrolls=new Map($$('.h3-final-choices').map(el=>[el.dataset.segment,el.scrollTop]));
    const bodyScroll=$('.h3-final-body')?.scrollTop||0;
    const items=s.segments||[],warnings=[];
    for(const [i,seg] of items.entries()){
      const chosen=seg.takes.find(t=>t.id===draft.take_ids[seg.id]);
      if(chosen?.dependencies?.some(d=>draft.take_ids[d.segment_id]!==d.take_id))warnings.push(`Segment ${i+1} was generated from a different previous take.`);
    }
    const count=items.filter(seg=>seg.takes.some(t=>t.id===draft.take_ids[seg.id])).length;
    dialog('Create final',`<div class="h3-final-body"><div class="h3-final-summary"><span>${esc(shotName(c,s))} · ${((s.end-s.start)/24).toFixed(1)}s</span><span>Choose one take per segment</span></div><div class="h3-final-segments">${items.map((seg,i)=>`<section class="h3-final-segment" aria-label="Segment ${i+1} takes"><header><h3>Segment ${i+1}</h3><small>${segmentTime(seg.start)}–${segmentTime(seg.end)}</small></header><div class="h3-final-choices" data-segment="${esc(seg.id)}">${[...seg.takes].reverse().map(t=>outputCard({id:t.id,title:`Take ${seg.takes.indexOf(t)+1}`,take:t,state:'success',duration:((t.end-t.start)/24).toFixed(1)+'s'},c,s,true,draft.take_ids[seg.id]===t.id)).join('')||H3UI.empty('No takes yet','Generate this segment before creating a final.')}</div></section>`).join('')}</div></div><div class="h3-final-footer"><div>${warnings.length?`<label class="h3-final-warning"><input id="final-ack" type="checkbox">${esc(warnings.join(' '))} Use these takes anyway.</label>`:''}<span class="note">${count} of ${items.length} segments selected</span><p id="final-error" role="alert"></p></div><div class="h3-final-actions">${H3UI.button({label:'Cancel',action:'cancel-final'})}${H3UI.button({label:'Create final',action:'submit-final',primary:true,disabled:count!==items.length||!items.length||!!warnings.length})}</div></div>`);
    $('#dialog').classList.add('final-assembly-modal');
    $$('.h3-final-choices').forEach(el=>el.scrollTop=scrolls.get(el.dataset.segment)||0);
    $('.h3-final-body').scrollTop=bodyScroll;
    const submit=$('[data-ui-action="submit-final"]');if($('#final-ack'))$('#final-ack').onchange=e=>submit.disabled=!e.target.checked||count!==items.length;
    $('[data-ui-action="cancel-final"]').onclick=()=>$('#dialog').close();
    $$('[data-output-choose]').forEach(b=>b.onclick=()=>{const seg=items.find(s=>s.takes.some(t=>t.id===b.dataset.outputChoose));draft.take_ids[seg.id]=b.dataset.outputChoose;draw();$$('[data-output-choose]').find(el=>el.dataset.outputChoose===b.dataset.outputChoose)?.focus({preventScroll:true});});
    $$('[data-output-open]').forEach(b=>b.onclick=guarded(()=>{const seg=items.find(s=>s.takes.some(t=>t.id===b.dataset.outputOpen)),take=seg.takes.find(t=>t.id===b.dataset.outputOpen);return openOutput({title:`Segment ${items.indexOf(seg)+1} · Take ${seg.takes.indexOf(take)+1}`,take,state:'success'},c,s,$('#dialog'));}));
    submit.onclick=async()=>{
      submit.disabled=true;$('#final-error').textContent='';
      try{
        const response=await api(`clips/${c.id}/shots/${s.id}/segments/final`,'POST',{...draft,acknowledge_continuity:!!$('#final-ack')?.checked});
        mergeJob(response.job);$('#dialog').close();renderOutputs();toast('Creating final video');
      }catch(error){$('#final-error').textContent=error.message;submit.disabled=false;}
    };
  }
  draw();
}
