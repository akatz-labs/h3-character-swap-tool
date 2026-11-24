import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {characterMasks,applyCharacterMasks,validateCastSelection as validateSelection} from '../dist/domain/index.js';
import {plan,adoptWorkflow} from '../dist/domain/index.js';
const cases=JSON.parse(readFileSync(new URL('./planner-golden.json',import.meta.url)));
const env={now:()=>1700000000,uid:p=>p+'test'};
test('character choices union masks once, allow sharing, and clear the last selection',()=>{
 const selected={characters:['a','b'],mask_tracks:[],scene_anchors:[],mask_assignments:{}};
 const lib={characters:[{id:'a'},{id:'b'}],mask_tracks:[{id:'m1'},{id:'m2'}],scene_anchors:[]};
 applyCharacterMasks(selected,{a:['m1'],b:['m1']});validateSelection(lib,selected);assert.deepEqual(selected.mask_tracks,['m1']);
 applyCharacterMasks(selected,{a:['m1'],b:['m2']});assert.deepEqual(selected.mask_tracks,['m1','m2']);
 applyCharacterMasks(selected,{a:[],b:['m1']});assert.deepEqual(selected.mask_tracks,['m1']);
 applyCharacterMasks(selected,{a:[],b:[]});assert.deepEqual(selected.mask_tracks,[]);
 assert.throws(()=>validateSelection(lib,{...selected,mask_assignments:{a:['foreign']}}),/selected masks/);
});
test('saved combined regions and old string associations remain visible without changing inputs',()=>{
 const selected={characters:['a','b'],mask_tracks:['x','y','z'],mask_assignments:{b:'z'}};
 const before=structuredClone(selected);assert.deepEqual(characterMasks(selected),{a:['x','y'],b:['z']});assert.deepEqual(selected,before);
});
test('rendering keeps references separate and emits two edit masks without character targets',()=>{
 const input=structuredClone(cases.find(c=>c.name==='guided input').input),s=input.clip.shots.find(s=>s.id===input.shotId);adoptWorkflow(s,env);input.command.body.options={};s.guides=[];
 const seg=s.segments[0],a=s.cast_setup.characters[0],b={...a,id:'second',reference:'second-image'};s.cast_setup.characters.push(b);
 seg.cast_inputs.characters=[a.id,b.id];s.cast_setup.mask_tracks=['x','y'].map(id=>({id,segment_id:seg.id,character_id:null,state:{mask:`video-${id}`,mask_points:[],mask_applied_points:[],expansion:0}}));
 applyCharacterMasks(seg.cast_inputs,{[a.id]:['x'],[b.id]:['y']});seg.use_mask=true;
 let output=plan(input,env);assert.deepEqual(output.guided.masks.map(m=>m.mask),['video-x','video-y']);assert.deepEqual(output.guided.references,[a.reference,'second-image']);assert.equal(output.guided.guides.length,0);
 assert(output.guided.masks.every(t=>!t.character_id));
 applyCharacterMasks(seg.cast_inputs,{[a.id]:['x'],[b.id]:['x']});output=plan(input,env);assert.equal(output.guided.masks.length,2);
 applyCharacterMasks(seg.cast_inputs,{});seg.use_mask=false;output=plan(input,env);assert.equal(output.guided.masks?.length||0,0);
});
