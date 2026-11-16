import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
const settle=()=>new Promise(resolve=>setImmediate(resolve));
const source=await readFile(new URL('../static/generation-pricing.js',import.meta.url),'utf8');
function setup(generationPrice){
 let now=0;const clock={now:()=>now};
 const button={disabled:false,isConnected:true,badge:null,querySelector(){return this.badge},append(b){this.badge=b}};
 const c=vm.createContext({adapter:{generationPrice,identity:()=> 'owner'},S:{batch:'p'},Date:clock,Error,JSON,WeakMap,document:{createElement:()=>({})},window:{addEventListener(){}},lifecycle:{signal:undefined},button});vm.runInContext(source,c);
 return {button,advance:ms=>{now+=ms},run:s=>vm.runInContext(s,c)};
}
test('local controls have no pricing read, badge or expected price',()=>{
 const {button,run}=setup();run('generationPriceButton(button,{operation:"mask"})');assert.equal(button.badge,null);assert.equal(button.disabled,false);assert.equal(JSON.stringify(run('generationPriceInput(button)')),'{}');
});
test('pending price disables; stale quality response cannot overwrite current price',async()=>{
 const pending=[];const {button,run}=setup(()=>new Promise(resolve=>pending.push(resolve)));
 run('generationPriceButton(button,{operation:"guide",parameters:{quality:"medium"}},false)');assert.equal(button.disabled,true);
 run('generationPriceButton(button,{operation:"guide",parameters:{quality:"high"}},false)');
 pending[1](20);await settle();assert.equal(button.badge.textContent,'20 credits');assert.equal(button.disabled,false);
 pending[0](10);await settle();assert.equal(button.badge.textContent,'20 credits');assert.equal(run('generationPriceInput(button).expected_price'),20);
 run('generationPriceButton(button,{operation:"guide",parameters:{quality:"high"}},true)');assert.equal(button.disabled,true);
});
test('price failure never becomes zero or enables submission',async()=>{
 const {button,run}=setup(()=>Promise.reject(Error('offline')));
 run('generationPriceButton(button,{operation:"mask"})');await settle();assert.equal(button.disabled,true);assert.equal(button.badge.textContent,'Price unavailable');assert.throws(()=>run('generationPriceInput(button)'));
});

test('background expiry retains valid price and availability without duplicate requests',async()=>{
 const pending=[];const {button,run,advance}=setup(()=>new Promise((resolve,reject)=>pending.push({resolve,reject})));
 const paint=()=>run('generationPriceButton(button,{operation:"mask"},false)');
 paint();pending[0].resolve(1);await settle();const badge=button.badge;
 advance(60001);paint();assert.equal(pending.length,2);assert.equal(button.disabled,false);assert.equal(badge.textContent,'1 credit');
 advance(120000);paint();assert.equal(pending.length,2);assert.equal(button.disabled,false);assert.equal(run('generationPriceInput(button).expected_price'),1);
 pending[1].resolve(2);await settle();assert.equal(button.badge,badge);assert.equal(badge.textContent,'2 credits');assert.equal(button.disabled,false);
 advance(60001);paint();pending[2].reject(Error('offline'));await settle();assert.equal(button.disabled,true);assert.equal(badge.textContent,'Price unavailable');
});

test('range displays sum of independently rounded job costs and retains each quote price',async()=>{
 const {button,run}=setup(async ({seconds})=>Math.ceil(seconds*10));
 run('generationPriceButton(button,[{operation:"render",seconds:1.01},{operation:"render",seconds:2.01}],false)');await settle();
 assert.equal(button.badge.textContent,'32 credits');assert.equal(JSON.stringify(run('generationPriceInput(button).expected_prices')),'[11,21]');
});
