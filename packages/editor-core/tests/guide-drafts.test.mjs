import {test} from 'node:test';
import assert from 'node:assert/strict';
import {restoreGuideDraft,retainGuideDraft} from '../web/src/guide-drafts.ts';
const initial={prompt:'',quality:'medium',references:[],helpers:[],edit_source_asset:'source'};
function storage(){const values=new Map();return {getItem:k=>values.get(k)||null,setItem:(k,v)=>values.set(k,v),removeItem:k=>values.delete(k)}}
test('guide drafts survive reopening and stay separate by guide and account',()=>{
 const s=storage(),draft=restoreGuideDraft(s,'owner:guide1',initial);
 Object.assign(draft.values,{prompt:'Keep this edit',quality:'high',references:['ref'],helpers:[{id:'scene',text:'Keep motion'}],edit_source_asset:'edited'});
 assert(retainGuideDraft(s,'owner:guide1',draft));
 assert.deepEqual(restoreGuideDraft(s,'owner:guide1',initial).values,draft.values);
 assert.deepEqual(restoreGuideDraft(s,'owner:guide2',initial).values,initial);
 assert.deepEqual(restoreGuideDraft(s,'other:guide1',initial).values,initial);
});
test('untouched fields follow server updates, saved drafts clear, invalid storage is ignored',()=>{
 const s=storage(),draft=restoreGuideDraft(s,'g',initial);draft.values.prompt='local';retainGuideDraft(s,'g',draft);
 assert.equal(restoreGuideDraft(s,'g',{...initial,quality:'low'}).values.quality,'low');
 assert.equal(restoreGuideDraft(s,'g',{...initial,prompt:'remote'}).values.prompt,'local');
 retainGuideDraft(s,'g',{base:draft.values,values:draft.values});assert.equal(s.getItem('g'),null);
 s.setItem('g','{"values":{"prompt":4}}');assert.deepEqual(restoreGuideDraft(s,'g',initial).values,initial);
 assert.equal(retainGuideDraft({setItem(){throw Error('full')}},'g',draft),false);
});
test('local guide model choice survives reopening and is isolated per guide',()=>{
 const s=storage(),current={...initial,edit_model:'gpt-image'};
 const draft=restoreGuideDraft(s,'local:guide',current);draft.values.edit_model='h3-image';draft.values.quality='high';
 retainGuideDraft(s,'local:guide',draft);
 assert.equal(restoreGuideDraft(s,'local:guide',current).values.edit_model,'h3-image');
 assert.equal(restoreGuideDraft(s,'local:other',current).values.edit_model,'gpt-image');
});
