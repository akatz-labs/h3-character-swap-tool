import {test} from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';
const guides=readFileSync(new URL('../static/guides.js',import.meta.url),'utf8');
const app=readFileSync(new URL('../static/app.js',import.meta.url),'utf8');
const conflict=()=>Object.assign(new Error('Shot changed; reload before saving again'),{status:409});
function harness(change=()=>{},failure=conflict()){
 const guide={id:'g',frame:0,source:'video',source_asset:'frame',dimensions:[512,512],prompt:'',quality:'medium',references:['ref'],helpers:[{id:'swap',text:'Swap'}],approved:null,versions:[]};
 const clip={id:'c',revision:1,source:'video',dimensions:[512,512],shots:[{id:'s',start:0,end:24,guides:[guide]}]};
 const calls=[];let writes=0;
 const env=vm.createContext({structuredClone,Error,S:{data:{clips:[clip]}},
  api:async(path,method,body)=>{calls.push({path,method,body});if(!method){const fresh=structuredClone(clip);fresh.revision=2;change(fresh);return fresh;}if(++writes===1)throw failure;return {revision:body.revision+1};},
  mergeSegmentClip:c=>env.S.data.clips[0]=c});
 vm.runInContext(app.slice(app.indexOf('function sameSettings'),app.indexOf('let writeQueue')),env);
 vm.runInContext(guides.slice(guides.indexOf('function guideRecord'),guides.indexOf('function guidesForSegment')),env);
 vm.runInContext(guides.slice(guides.indexOf('function guideSettings'),guides.indexOf('async function setGuideEnabled')),env);
 const session={cid:'c',sid:'s',id:'g',approved:null,preview:'candidate',draft:{base:env.guideSettings(guide),values:{...env.guideSettings(guide),prompt:'Replace the person',quality:'high'}}};
 return {env,calls,session,save:approve=>env.saveGuideDraft(session,'clips/c/shots/s/guides',1,approve)};
}
test('unrelated shot revision refreshes once, preserving exact guide inputs and other changes',async()=>{
 const h=harness(c=>{c.review_notes='Keep this';c.shots[0].guides[0].versions.push({id:'finished',asset:'image'});});
 assert.equal((await h.save(false)).revision,3);
 assert.deepEqual(h.calls.map(c=>[c.method,c.body?.revision]),[['PATCH',1],[undefined,undefined],['PATCH',2]]);
 assert.deepEqual(h.calls[0].body.changes,h.calls[2].body.changes);
 assert.equal(h.env.S.data.clips[0].review_notes,'Keep this');
 assert.equal(h.session.draft.values.prompt,'Replace the person');
});
test('version approval recovers unrelated revision without changing the selected version',async()=>{
 const h=harness();await h.save(true);assert.equal(h.calls[2].path,'clips/c/shots/s/guides/g/save');assert.equal(h.calls[2].body.version_id,'candidate');
});
for(const [name,change] of [
 ['guide prompt',c=>c.shots[0].guides[0].prompt='Other editor'],
 ['guide reference',c=>c.shots[0].guides[0].references=['other']],
 ['source frame',c=>c.shots[0].guides[0].source_asset='replacement'],
 ['deleted guide',c=>c.shots[0].guides=[]],
 ['canvas',c=>c.dimensions=[1024,512]],
])test(`${name} conflict never overwrites newer data or loses the browser draft`,async()=>{
 const h=harness(change);await assert.rejects(h.save(false),/draft is preserved/);assert.equal(h.calls.length,2);assert.equal(h.session.draft.values.prompt,'Replace the person');
});
test('changed approval blocks approval retry, but not a separate candidate generation',async()=>{
 const change=c=>c.shots[0].guides[0].approved='other';const h=harness(change);await assert.rejects(h.save(true),/changed elsewhere/);assert.equal(h.calls.length,2);await harness(change).save(false);
});
test('JSONB helper ordering is not treated as an input change',async()=>{
 const h=harness(c=>c.shots[0].guides[0].helpers=[{text:'Swap',id:'swap'}]);await h.save(false);assert.equal(h.calls.length,3);
});
test('network errors and a second revision conflict are never replayed',async()=>{
 const h=harness(()=>{},new TypeError('Failed to fetch'));await assert.rejects(h.save(false),/Failed to fetch/);assert.equal(h.calls.length,1);
 const c=harness();const original=c.env.api;c.env.api=(path,method,body)=>c.calls.length===2?Promise.reject(conflict()):original(path,method,body);await assert.rejects(c.save(false),/Shot changed/);assert.equal(c.calls.length,2);
});
