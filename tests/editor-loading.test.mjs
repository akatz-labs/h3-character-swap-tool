import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const WORKFLOW=JSON.parse(readFileSync(new URL('../packages/editor-core/workflow.json',import.meta.url),'utf8'));
const segmentSource=readFileSync(new URL('../packages/editor-core/static/segments.js',import.meta.url),'utf8');
const playbackSource=readFileSync(new URL('../packages/editor-core/static/mask-playback.js',import.meta.url),'utf8');
function segmentHarness(){
  const calls=[],rendered=[],timelineStates=[],controls={innerHTML:'Loading segments…'},retry={};
  const context=vm.createContext({WORKFLOW,AbortController,AbortSignal,clearTimeout,document:{addEventListener(){}},
    S:{tab:'render'},clip:()=>({id:'clip',revision:1}),shot:()=>({id:'shot'}),
    $:selector=>selector==='#segment-load-retry'?retry:controls,
    api:(...args)=>new Promise((resolve,reject)=>calls.push({args,resolve,reject})),
    autosaveDirty:()=>false,autosaveStatus:()=>{},rendered,timelineStates});
  vm.runInContext(segmentSource+`
    segmentEditor={clipId:'clip',shotId:'shot',serial:0,fields:[],plan:null};
    segmentTimelineStatus=state=>timelineStates.push(state);
    renderSegmentStrip=()=>rendered.push(segmentEditor.plan);
    renderSegmentControls=()=>{};updateSegmentHistory=()=>{};mergeSegmentClip=()=>{};
  `,context);
  return {calls,rendered,controls,retry,timelineStates,run:code=>vm.runInContext(code,context)};
}
const plan=revision=>({revision,segments:[{id:'segment',render_mode:WORKFLOW.render_mode,lora_id:WORKFLOW.lora_id,history_frames:WORKFLOW.history_frames,connection:'start'}],states:[]});

test('slow segment response survives repeated background polls',async()=>{
  const h=segmentHarness(),first=h.run('loadSegmentPlan(true)');
  for(let i=0;i<5;i++)await h.run('loadSegmentPlan()');
  assert.equal(h.calls.length,1);assert.deepEqual(h.timelineStates,['loading']);
  h.calls[0].resolve(plan(1));await first;
  assert.equal(h.rendered.length,1);assert.deepEqual(h.timelineStates,['loading','ready']);
  await h.run('loadSegmentPlan()');assert.equal(h.calls.length,1);
});

test('explicit refresh cancels stale read and ignores a late response',async()=>{
  const h=segmentHarness(),first=h.run('loadSegmentPlan(true)'),second=h.run('loadSegmentPlan(true)');
  assert.equal(h.calls[0].args[3].aborted,true);
  h.calls[1].resolve(plan(2));await second;
  h.calls[0].resolve(plan(1));await first;
  assert.deepEqual(h.rendered.map(p=>p.revision),[2]);
});

test('leaving editor aborts pending read without touching replacement UI',async()=>{
  const h=segmentHarness(),first=h.run('loadSegmentPlan(true)');
  h.run('disposeSegmentEditor()');assert.equal(h.calls[0].args[3].aborted,true);
  h.calls[0].reject(new Error('closed'));await first;
  assert.equal(h.controls.innerHTML,'Loading segments…');assert.equal(h.rendered.length,0);
});

test('initial failure is retryable and refresh failure preserves editable controls',async()=>{
  const h=segmentHarness(),first=h.run('loadSegmentPlan()');
  h.calls[0].reject(new DOMException('signal timed out','TimeoutError'));await first;
  assert.match(h.controls.innerHTML,/Retry now/);
  const retry=h.retry.onclick();h.calls[1].resolve(plan(1));await retry;
  h.controls.innerHTML='Existing edited prompt';
  const refresh=h.run('loadSegmentPlan(true)');h.calls[2].reject(new Error('offline'));await refresh;
  assert.equal(h.controls.innerHTML,'Existing edited prompt');
  assert.deepEqual(h.timelineStates,['loading','error','loading','ready']);
});

function playbackHarness(api){
  const context=vm.createContext({api,AbortController,AbortSignal,TypeError,Error,
    setTimeout:callback=>{callback();return 0;}});
  vm.runInContext(playbackSource,context);
  return vm.runInContext('requestPlayback',context);
}
test('preview timeout recovers without repeating cache invalidation',async()=>{
  const bodies=[];const request=playbackHarness(async(path,method,body)=>{
    bodies.push(body);if(bodies.length===1)throw new DOMException('signal timed out','TimeoutError');
    return {state:'ready'};
  });
  assert.equal((await request('preview',{retry:true,track_id:'mask'},new AbortController().signal)).state,'ready');
  assert.deepEqual(bodies.map(b=>b.retry),[true,false]);assert.equal(bodies[1].track_id,'mask');
});

test('preview retries are bounded and cancellation or server errors are not retried',async()=>{
  let calls=0;
  const request=playbackHarness(async()=>{calls++;throw new TypeError('Failed to fetch');});
  await assert.rejects(request('preview',{},new AbortController().signal),/Retry preview/);assert.equal(calls,3);
  const controller=new AbortController();controller.abort();calls=0;
  await assert.rejects(request('preview',{},controller.signal),{name:'AbortError'});assert.equal(calls,0);
  const denied=playbackHarness(async()=>{calls++;throw new Error('Not allowed');});
  await assert.rejects(denied('preview',{},new AbortController().signal),/Not allowed/);assert.equal(calls,1);
});

// An upgraded response must settle, rather than repeatedly rewriting the shot.
test('Pika upgrade settles after one workflow write',async()=>{
  const h=segmentHarness(),loading=h.run('loadSegmentPlan(true)');
  const old=plan(1);old.segments[0].lora_id='v2-1000';h.calls[0].resolve(old);
  await new Promise(setImmediate);
  assert.equal(h.calls[1].args[0],'clips/clip/shots/shot/segments/workflow');
  h.calls[1].resolve({});await new Promise(setImmediate);
  h.calls[2].resolve(plan(2));await loading;
  assert.equal(h.calls.length,3);assert.equal(h.rendered.length,1);
});
