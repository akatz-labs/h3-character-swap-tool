import {test} from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFile} from 'node:fs/promises';
const source=await readFile(new URL('../static/guides.js',import.meta.url),'utf8');
function cache(){
 const env={adapter:{identity:()=>env.identity},identity:'one',S:{batch:'project'},Date:{now:()=>env.time},time:1000};
 vm.createContext(env);vm.runInContext(source.slice(source.indexOf('const guideModalCache'),source.indexOf('const GUIDE_PROPAGATION')),env);return env;
}
test('guide cache reuses data, invalidates revisions and expires after five minutes',()=>{
 const c=cache(),key=c.guideCacheKey('clip','shot'),data={guides:[]};
 c.rememberGuideData(key,1,data);assert.equal(c.cachedGuideData(key,1),data);
 assert.equal(c.cachedGuideData(key,2),null);
 c.rememberGuideData(key,2,data);c.time+=300001;assert.equal(c.cachedGuideData(key,2),null);
});
test('guide cache bounds entries by recency and separates accounts/projects',()=>{
 const c=cache();for(let n=0;n<8;n++)c.rememberGuideData(c.guideCacheKey('clip',String(n)),1,n);
 assert.equal(c.cachedGuideData(c.guideCacheKey('clip','0'),1),0);
 c.rememberGuideData(c.guideCacheKey('clip','8'),1,8);
 assert.equal(c.cachedGuideData(c.guideCacheKey('clip','1'),1),null);
 assert.equal(c.cachedGuideData(c.guideCacheKey('clip','0'),1),0);
 c.S.batch='other';assert.equal(c.cachedGuideData(c.guideCacheKey('clip','0'),1),null);
 c.S.batch='project';c.identity='two';c.guideCacheKey('clip','0');c.identity='one';
 assert.equal(c.cachedGuideData(c.guideCacheKey('clip','0'),1),null);
});
