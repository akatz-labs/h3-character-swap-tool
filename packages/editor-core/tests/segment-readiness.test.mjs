import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {plan,adoptWorkflow} from '../dist/domain/index.js';
const fixtures=JSON.parse(await readFile(new URL('./planner-golden.json',import.meta.url),'utf8'));
function scene(){
 const input=structuredClone(fixtures.find(c=>c.name==='guided input').input),s=input.clip.shots[0];
 const first=s.segments[0];s.end=s.start+72;s.guides=[];
 s.segments=[0,1,2].map(i=>({...structuredClone(first),id:`segment-${i}`,start:i*24,end:(i+1)*24,takes:[],selected_take:null}));
 adoptWorkflow(s,{now:()=>1,uid:p=>p+crypto.randomUUID()});return {input,s};
}
function view(input){return plan({operation:'segments',clip:input.clip,shotId:input.shotId});}
function render(input,s,i){return plan({...input,command:{...input.command,body:{options:{segments:{mode:'segment',segment_id:s.segments[i].id,plan_revision:view(input).plan_revision}}}}});}
function complete(input,s,i){const p=render(input,s,i);s.segments[i].takes.push({id:`take-${i}-${s.segments[i].takes.length}`,master:`video-${i}`,signature:p.signature,start:i*24,end:(i+1)*24,dimensions:p.dimensions,dependencies:p.history.map(({segment_id,take_id,frames})=>({segment_id,take_id,frames}))});}
test('unselected completed takes become current and upstream edits invalidate all descendants without deleting outputs',()=>{
 const {input,s}=scene();for(let i=0;i<3;i++)complete(input,s,i);
 assert.deepEqual(view(input).states.map(x=>x.current),[true,true,true]);
 s.segments[0].seed++;
 assert.deepEqual(view(input).states.map(x=>x.current),[false,false,false]);
 assert.deepEqual(view(input).states.map(x=>x.blocked),[false,true,true]);
 assert.equal(render(input,s,1).history[0].asset,null);
 assert(s.segments.every(x=>x.takes.length===1));
 s.segments[0].seed--;
 assert(view(input).states.every(x=>x.current));
 complete(input,s,0);
 assert.deepEqual(view(input).states.map(x=>x.current),[true,false,false]);
});
test('pending selected mask corrections invalidate takes while cosmetic mask naming does not',()=>{
 const {input,s}=scene(),seg=s.segments[0];seg.use_mask=true;seg.cast_inputs.mask_tracks=['mask'];
 s.cast_setup.mask_tracks=[{id:'mask',segment_id:seg.id,character_id:null,name:'Mask',state:{mask:'mask-media',expansion:24,mask_points:[],mask_applied_points:[]}}];
 complete(input,s,0);assert(view(input).states[0].current);
 s.cast_setup.mask_tracks[0].name='Long renamed mask';assert(view(input).states[0].current);
 s.cast_setup.mask_tracks[0].state.mask_points=[{id:'point',frame:0,x:.5,y:.5,label:1}];
 assert(!view(input).states[0].current);
 assert.throws(()=>render(input,s,0),/pending corrections/);
});
