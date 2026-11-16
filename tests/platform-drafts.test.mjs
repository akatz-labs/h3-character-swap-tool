import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import {transform} from 'esbuild';

// Exercise the actual bridge save callback, with persistence boundaries controlled.
const bridge=readFileSync(new URL('../packages/editor-core/scripts/build_legacy_bridge.mjs',import.meta.url),'utf8');
const body=bridge.match(/async saveDraft\(\)\{([\s\S]*?)\n    \},\n    snapshot/)[1];
function draft(overrides={}){
  const calls=[];
  const context=vm.createContext({
    guideSession:null,$:()=>({open:true}),importsPending:()=>false,
    flushShotAutosave:async()=>{calls.push('settings');},
    saveMaskEdits:async()=>{calls.push('masks');},
    autosaveDirty:()=>false,segmentDirty:()=>false,maskHasDraft:()=>false,
    ...overrides,
  });
  return {calls,save:vm.runInContext(`(async()=>{${body}})`,context)};
}
test('redirect save waits for settings, then masks; never starts tracking or generation',async()=>{
  let finish;
  const h=draft({flushShotAutosave:()=>new Promise(resolve=>{finish=resolve;})});
  let done=false;const save=h.save().then(()=>{done=true;});
  await Promise.resolve();assert.equal(done,false);assert.deepEqual(h.calls,[]);
  finish();await save;assert.deepEqual(h.calls,['masks']);
});
test('pending imports block an account redirect before it can change upload ownership',async()=>{
  const h=draft({importsPending:()=>true});await assert.rejects(h.save(),/imports to finish/);assert.deepEqual(h.calls,[]);
});
test('persistence failure blocks redirect without a discard path',async()=>{
  const h=draft({flushShotAutosave:async()=>{throw Error('Version conflict');}});
  await assert.rejects(h.save(),/Version conflict/);assert.deepEqual(h.calls,[]);
  await assert.rejects(draft({saveMaskEdits:async()=>{throw Error('offline');}}).save(),/offline/);
});
test('uncommitted or busy guides block redirect and preserve the candidate',async()=>{
  for(const guideSession of [
    {busy:false,draft:{values:{prompt:'edited'},base:{prompt:'original'}}},
    {busy:true,draft:{values:{},base:{}}},
  ]){
    const h=draft({guideSession});await assert.rejects(h.save(),/guide draft/);
    assert.deepEqual(h.calls,[]);
  }
  const h=draft({guideSession:{busy:false,draft:{values:{prompt:'saved'},base:{prompt:'saved'}}}});
  await h.save();assert.deepEqual(h.calls,['settings','masks']);
});
test('edits arriving during save still block redirect',async()=>{
  for(const key of ['autosaveDirty','segmentDirty','maskHasDraft']){
    await assert.rejects(draft({[key]:()=>true}).save(),/Finish saving/);
  }
});
const lifecycle=await transform(readFileSync(new URL('../packages/editor-core/web/src/legacy-lifecycle.ts',import.meta.url),'utf8'),{loader:'ts',format:'esm'});
const {createLegacyController}=await import('data:text/javascript;base64,'+Buffer.from(lifecycle.code).toString('base64'));
test('controller waits for mounted editor and rejects a disposed editor',async()=>{
  let ready,calls=0;
  const scope={disposed:false};
  const controller=createLegacyController(scope,{}, {ready:new Promise(resolve=>{ready=resolve;}),saveDraft:async()=>{calls++;}});
  const pending=controller.saveDraft();assert.equal(calls,0);ready();await pending;assert.equal(calls,1);
  scope.disposed=true;await assert.rejects(controller.saveDraft(),/Editor closed/);assert.equal(calls,1);
});
