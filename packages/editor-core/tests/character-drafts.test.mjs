import {test} from 'node:test';
import assert from 'node:assert/strict';
import {restoreCharacterDraft,retainCharacterDraft} from '../web/src/character-drafts.ts';

test('character drafts retain typed changes during uploads without crossing account/character boundaries',()=>{
  const values=new Map(),storage={getItem:k=>values.get(k)||null,setItem:(k,v)=>values.set(k,v),removeItem:k=>values.delete(k)};
  const base={name:'Character',description:''},draft={...base,description:'Still typing during upload'};
  assert.deepEqual(restoreCharacterDraft(storage,'new',{...base,id:'c',reference:'asset',revision:1}),base);
  assert(retainCharacterDraft(storage,'owner:project:character',base,draft));
  assert.deepEqual(restoreCharacterDraft(storage,'owner:project:character',{...base,name:'Updated name'}),{...draft,name:'Updated name'});
  assert.deepEqual(restoreCharacterDraft(storage,'other:project:character',base),base);
  assert.deepEqual(restoreCharacterDraft(storage,'owner:project:other',base),base);
  retainCharacterDraft(storage,'owner:project:character',draft,draft);
  assert.equal(values.size,0);
  assert.equal(retainCharacterDraft({setItem(){throw Error('quota')}},'key',base,draft),false);
});
