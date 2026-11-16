import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const root=new URL('../packages/editor-core/static/',import.meta.url);
const app=readFileSync(new URL('app.js',root),'utf8');
const compare=app.slice(app.indexOf('function sameSettings'),app.indexOf('let writeQueue'));
test('settings comparison tolerates JSONB object ordering without dropping real edits',()=>{
 const c=vm.createContext({});vm.runInContext(compare,c);
 assert(c.sameSettings({cast_inputs:{characters:['a'],mask_tracks:['m']},points:[{x:.2,y:.3,label:1}]},{points:[{label:1,y:.3,x:.2}],cast_inputs:{mask_tracks:['m'],characters:['a']}}));
 assert(!c.sameSettings({points:[{x:.2}]},{points:[{x:.3}]}));
 assert(!c.sameSettings({characters:['a','b']},{characters:['b','a']}));
});
test('hosted distance decoding preserves exact native-pixel expansion',()=>{
 const source=readFileSync(new URL('mask-playback.js',root),'utf8');
 const code=source.slice(source.indexOf('function maskDistances'),source.indexOf('async function maskVideoPage'));
 const c=vm.createContext({});vm.runInContext(code,c);
 const pixels=new Uint8ClampedArray(7*7*4);pixels[(3*7+3)*4]=255;
 const d=c.maskDistances(pixels,7,7);
 for(let y=0;y<7;y++)for(let x=0;x<7;x++)assert.equal(d[y*7+x],Math.ceil(Math.hypot(x-3,y-3)));
 assert([...c.maskDistances(new Uint8ClampedArray(196),7,7)].every(v=>v===65535));
});
