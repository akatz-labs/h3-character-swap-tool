import {test} from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {characterMasks,applyCharacterMasks} from '../dist/domain/index.js';
import {readFile} from 'node:fs/promises';
const text=await readFile(new URL('../static/cast-ui.js',import.meta.url),'utf8');
test('mask toggles apply only their intent after earlier writes and retain other inputs',async()=>{
 const stale={id:'segment',cast_inputs:{characters:['first','second'],mask_tracks:['a'],mask_assignments:{}}};
 const current={id:'segment',cast_inputs:{characters:['first','second','new'],mask_tracks:['a','b'],mask_assignments:{first:['a'],second:['a','b']}}};
 const clip={id:'clip',revision:2,shots:[{id:'shot',segments:[current]}]},writes=[];
 const env={characterMasks,applyCharacterMasks,S:{clip:'clip',shot:'shot',data:{clips:[clip]}},structuredClone,segmentSelected:()=>stale,segmentPath:()=>'/segments',flushShotAutosave:async()=>{},writeQueue:Promise.resolve(),sameSettings:()=>false,mergeSegmentClip:()=>{},loadSegmentPlan:async()=>{},api:async(path,method,body)=>{writes.push(body);Object.assign(current,body.changes);return clip;}};
 vm.createContext(env);vm.runInContext(text.slice(text.indexOf('async function setSegmentCastInputs'),text.indexOf('function bindSegmentCast')),env);
 await Promise.all([env.setSegmentCastInputs(stale.cast_inputs,{characterId:'first',id:''}),env.setSegmentCastInputs(stale.cast_inputs,{characterId:'second',id:''})]);
 assert.deepEqual(Array.from(current.cast_inputs.characters),['first','second','new']);assert.deepEqual(Array.from(current.cast_inputs.mask_tracks),[]);
 assert.equal(writes[0].revision,2);assert.equal(writes[1].changes.use_mask,false);
 await assert.rejects(env.setSegmentCastInputs(stale.cast_inputs),/changed elsewhere/);
});
