import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {plan,adoptWorkflow,isCurrentWorkflow,WORKFLOW} from '../dist/domain/index.js';
const cases=JSON.parse(await readFile(new URL('./planner-golden.json',import.meta.url),'utf8'));
const env={now:()=>1700000000,uid:p=>p+'workflow'};
test('workflow cutover keeps original settings, guides, masks, manual text and takes; repeat is a no-op',()=>{
 const input=structuredClone(cases.find(c=>c.name==='guided input').input);
 const shot=input.clip.shots.find(s=>s.id===input.shotId);
 const seg=shot.segments[0];seg.render_mode='swap';seg.lora_id='none';seg.prompt_mode='manual';seg.performance_prompt='Keep this exact text';seg.takes.push({id:'saved-take',settings:{render_mode:'swap'},asset:'keep-video'});
 const before=structuredClone(shot);
 adoptWorkflow(shot,env);
 assert.ok(isCurrentWorkflow(shot));
 assert.deepEqual(shot.segment_layout_history.at(-1).segments,before.segments);
 assert.deepEqual(shot.segments[0].takes,before.segments[0].takes);
 assert.equal(shot.segments[0].performance_prompt,'Keep this exact text');
 assert.deepEqual(shot.guides,before.guides);assert.deepEqual(shot.cast_setup,before.cast_setup);
 const once=structuredClone(shot);adoptWorkflow(shot,env);assert.deepEqual(shot,once);
});
test('current workflow freezes video, character references and timed guides separately; hosted execution is server owned',()=>{
 const input=structuredClone(cases.find(c=>c.name==='guided input').input);
 const shot=input.clip.shots.find(s=>s.id===input.shotId);adoptWorkflow(shot,env);
 input.command.body.options={};
 input.command.body.options.model_offloading=true;
 const result=plan(input,env);
 assert.ok(result.guided);assert.equal(result.segment.render_mode,WORKFLOW.render_mode);
 assert.equal(result.guided.parameters.offloading,false);
 assert.ok(result.guided.guides.length);assert.ok(result.guided.references.length);
 assert.equal(result.guided.parameters.guideFrames,JSON.stringify(result.guided.guides.map(g=>g.frame-result.sourceRange.startFrame)).replaceAll(',',', '));
 assert.ok(result.guided.guides.every(g=>!result.guided.references.includes(g.asset)));
});

test('masked hosted plan freezes selected segment masks and ignores untracked drafts',()=>{
 const input=structuredClone(cases.find(c=>c.name==='guided input').input),shot=input.clip.shots.find(s=>s.id===input.shotId);adoptWorkflow(shot,env);input.command.body.options={};
 const seg=shot.segments[0];seg.use_mask=true;seg.cast_inputs.mask_tracks=['m'];
 shot.cast_setup.mask_tracks=[{id:'m',segment_id:seg.id,character_id:null,state:{mask:'mask-video',mask_base:null,mask_points:[],mask_applied_points:[],expansion:24}}];
 const result=plan(input,env);assert.equal(result.guided.masks[0].mask,'mask-video');assert.equal(result.guided.masks[0].expansion,24);assert(!result.guided.references.includes('mask-video'));
 shot.cast_setup.mask_tracks[0].state.mask_frames=[seg.start,seg.end];assert.equal(plan(input,env).guided.masks.length,1);
 shot.cast_setup.mask_tracks[0].state.mask_frames=[seg.start+1,seg.end];assert.throws(()=>plan(input,env),/Mask 1 doesn’t cover this whole segment/);
 delete shot.cast_setup.mask_tracks[0].state.mask_frames;
 shot.cast_setup.mask_tracks[0].state.mask=null;assert.equal(plan(input,env).guided.masks?.length||0,0);
});

 test('a character swap freezes references without requiring any guide image',()=>{
 const input=structuredClone(cases.find(c=>c.name==='guided input').input),shot=input.clip.shots.find(s=>s.id===input.shotId);adoptWorkflow(shot,env);input.command.body.options={};shot.guides=[];
 const result=plan(input,env);assert.equal(result.guided.guides.length,0);assert.equal(result.guided.parameters.guideFrames,'[]');assert.ok(result.guided.references.length);
 });

test('later segments freeze previous takes and preserve guide positions on the unpadded timeline',()=>{
 const input=structuredClone(cases.find(c=>c.name==='guided input').input),shot=input.clip.shots.find(s=>s.id===input.shotId);
 const first=shot.segments[0];first.start=0;first.end=24;
 const second={...structuredClone(first),id:'second',start:24,end:48,takes:[],selected_take:null};
 shot.segments=[first,second];shot.end=shot.start+48;adoptWorkflow(shot,env);
 const plan0=plan({...input,command:{...input.command,body:{options:{}}}},env);
 shot.segments[0].takes=[{id:'previous-take',master:'previous-video',start:0,end:24,dimensions:plan0.dimensions,signature:plan0.signature,dependencies:[]}];
 shot.segments[0].selected_take='previous-take';
 // Ask through the public domain API, including its saved-plan revision.
 const state=plan({operation:'segments',clip:input.clip,shotId:shot.id},env);
 input.command.body.options={segments:{mode:'segment',segment_id:'second',plan_revision:state.plan_revision}};
 shot.segments[1].cast_inputs={characters:[],mask_tracks:[],scene_anchors:[],mask_assignments:{}};
 shot.segments[1].use_mask=false;
 const fresh=plan({operation:'segments',clip:input.clip,shotId:shot.id},env);
 input.command.body.options.segments.plan_revision=fresh.plan_revision;
 const result=plan(input,env);
 assert.deepEqual(result.guided.references,[]);
 assert.equal(result.history[0].asset,'previous-video');
 assert.equal(result.guided.parameters.historyFrames,18);
 assert.equal(result.sourceRange.startFrame,shot.start+24);
 assert.equal(result.sourceRange.frameCount,24);
 assert.equal(result.sourceRange.prefixFrames,18);
 assert.equal(result.history[0].take_id,'previous-take');
 assert.equal(result.history[0].frames,18);
 assert.equal(result.guided.parameters.guideFrames,'[]');
 shot.segments[1].start=5;shot.segments[0].end=5;adoptWorkflow(shot,env);
 assert.equal(shot.segments[1].history_frames,1);
});

test('characters and new guides are optional; supplied guides must still be current',()=>{
 const input=structuredClone(cases.find(c=>c.name==='guided input').input),s=input.clip.shots.find(s=>s.id===input.shotId);
 adoptWorkflow(s,env);input.command.body.options={};
 const seg=s.segments[0];seg.cast_inputs={characters:[],mask_tracks:[],scene_anchors:[],mask_assignments:{}};seg.use_mask=false;
 const result=plan(input,env);assert.deepEqual(result.guided.references,[]);assert.equal(result.reference,null);assert.ok(result.guided.guides.length);
 const original=structuredClone(s.guides);
 for(const change of [g=>g.enabled=false,g=>g.approved=null,g=>g.frame=s.end]){
  s.guides=structuredClone(original);s.guides.forEach(change);assert.deepEqual(plan(input,env).guided.guides,[]);
 }
 s.guides=structuredClone(original);s.guides.forEach(g=>g.source='older-source');assert.throws(()=>plan(input,env),/another source or canvas/);
 s.guides=[];assert.deepEqual(plan(input,env).guided.guides,[]);
});

test('guide plans accept eight references and reject a ninth without counting the source',()=>{
 const input=structuredClone(cases.find(c=>c.name==='guide plan').input);
 const shot=input.clip.shots.find(s=>s.id===input.shotId);
 shot.guide_references=Array.from({length:9},(_,n)=>({asset:`reference-${n}`,name:`Reference ${n}`,kind:'Uploaded image'}));
 input.command.body.changes={...input.command.body.changes,references:shot.guide_references.slice(0,8).map(r=>r.asset)};
 const result=plan(input,env);
 assert.equal(result.references.length,8);assert(result.source);
 input.command.body.changes.references=shot.guide_references.map(r=>r.asset);
 assert.throws(()=>plan(input,env),/up to 8 reference/);
});

 test('model selection persists independently and changes the frozen signature',()=>{
 const input=structuredClone(cases.find(c=>c.name==='guided input').input),shot=input.clip.shots.find(s=>s.id===input.shotId);
 adoptWorkflow(shot,env);input.command.body.options={};
 shot.segments[0].lora_id='viggle-remake-swap';const viggle=plan(input,env);
 shot.segments[0].lora_id='v2-1000';adoptWorkflow(shot,env);const pika=plan(input,env);
 assert(isCurrentWorkflow(shot));assert.equal(pika.segment.lora_id,'v2-1000');assert.notEqual(pika.signature,viggle.signature);
 shot.segments[0].lora_id='viggle-remake-swap';assert.equal(plan(input,env).signature,viggle.signature);
 });
