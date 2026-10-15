// Final assembled clip previews share the report Atlas transport and one decoder.
let reviewPlayer=null;
const reviewPositions=new Map();
function reviewPreviewKey(c){return JSON.stringify([c.result,c.source_pair,c.source,c.raw_decoded,c.frames,c.dimensions,...(!c.result?c.shots.map(s=>s.selected_render):[])])}
function disposeReviewPlayer(){
  document.querySelector("#review-draft-video")?.pause();
  if(!reviewPlayer)return;
  const e=reviewPlayer;reviewPositions.set(e.clip.id,{frame:e.comparison.frame,mode:e.root.querySelector('[id$="compare-mode"]').value});
  e.comparison.dispose();reviewPlayer=null;
}
function finalReviewHtml(c){
  if(!c.result){
    const s=c.shots.find(s=>s.renders.some(r=>r.id===s.selected_render)),r=s?.renders.find(r=>r.id===s.selected_render);
    return r?`<section class="panel draft-review"><div class="panel-title"><strong>${esc(shotName(c,s))} · ${takeLabel(s,r)}</strong><span class="note">Selected take preview</span></div><video id="review-draft-video" controls playsinline preload="metadata" poster="${asset(r.asset)}/thumbnail" src="${asset(r.asset)}"></video><p class="note">Assemble your selected takes to view and download the full clip comparison.</p></section>`:'<div class="empty review-empty"><span aria-hidden="true">▷</span><strong>Your final clip starts here</strong><p>Generate takes for your shots, choose a take for each, then assemble your clip.</p></div>';
  }
  const shot={start:0,end:c.frames,renders:[{id:'result',asset:c.result}],selected_render:'result'};
  return `<section class="panel final-review"><div class="panel-title"><strong>Final comparison</strong><span class="note">Fast previews</span></div><div class="panel-body">${comparisonHtml(c,shot,{id:'result',asset:c.result},'review-')}<p class="note review-quality">Preview quality · Downloads retain full resolution.</p></div></section>`;
}
function bindFinalReview(c){
  disposeReviewPlayer();if(!c.result)return;
  const root=document.querySelector('#review-pair-host'),mode=root.querySelector('#review-compare-mode'),position=reviewPositions.get(c.id)||{};
  root.querySelector('.comparison').setAttribute('aria-label','Final clip comparison');
  mode.innerHTML=`<option value="side">Side by side</option><option value="report16">16:9 · Source / Reference / Result</option><option value="reportFit">Fitted · Source / Reference / Result</option><option value="wipe">Wipe</option><option value="right">Result only</option><option value="left">Source only</option>`;
  const layout=m=>m==='report16'?'16x9':m==='reportFit'?'fit':'pair';
  const identity=(c,m)=>m==='report16'?c.comparison:m==='reportFit'?c.comparison_fit:JSON.stringify([c.result,c.source_pair||c.source]);
  const e={clip:c,root,comparison:null,warmed:new Set()};reviewPlayer=e;
  const available=()=>{mode.querySelector('[value="report16"]').disabled=!e.clip.comparison;mode.querySelector('[value="reportFit"]').disabled=!e.clip.comparison_fit};available();
  const initial=position.mode&& !mode.querySelector(`[value="${position.mode}"]`)?.disabled?position.mode:c.comparison?'report16':'side';
  e.comparison=bindRenderComparison(c,{start:0,end:c.frames,renders:[]},{id:'result'}, {
    root,prefix:'review-',inline:true,frame:Math.min(position.frame||0,c.frames-1),mode:initial,
    labels:[c.raw_decoded===false?'Matched source pair':'Original source','Selected result'],
    requestKey:m=>layout(m)+':'+identity(e.clip,m),
    indices:pair=>pair.columns[0]==='report'?[0,0]:[pair.columns.indexOf('source'),pair.columns.indexOf('result')],
    prepare:({mode,retry,signal})=>api(`clips/${e.clip.id}/review-preview`,'POST',{layout:layout(mode),retry},signal),
    onReady:()=>{
      for(const [format,asset] of [['16x9',e.clip.comparison],['fit',e.clip.comparison_fit]]){
        if(!asset||e.warmed.has(asset))continue;e.warmed.add(asset);
        api(`clips/${e.clip.id}/review-preview`,'POST',{layout:format}).catch(()=>e.warmed.delete(asset));
      }
    }
  });
  e.refresh=c=>{const old=identity(e.clip,mode.value);e.clip=c;available();if(old!==identity(c,mode.value))mode.dispatchEvent(new Event('change'))};
  e.pause=()=>e.comparison.pause();
}
