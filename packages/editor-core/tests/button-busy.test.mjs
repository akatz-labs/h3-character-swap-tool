import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
const app=await readFile(new URL('../static/app.js',import.meta.url),'utf8');
const source=app.slice(app.indexOf('function buttonBusy('),app.indexOf('function guarded('));
class Button {
 textContent='Generate guide10 credits';disabled=false;childElementCount=2;
 classList={add(){},remove(){}};setAttribute(){}removeAttribute(){}
}
const context=vm.createContext({HTMLButtonElement:Button});vm.runInContext(source,context);
test('busy indication preserves structured label and credit content',()=>{
 const button=new Button(),finish=context.buttonBusy(button);
 assert.equal(button.textContent,'Generate guide10 credits');assert.equal(button.disabled,true);
 finish();assert.equal(button.disabled,false);
});
test('accepted generation stays disabled after its handler changes the label',()=>{
 const button=new Button(),finish=context.buttonBusy(button);
 button.textContent='Generating…10 credits';button.disabled=true;
 finish();assert.equal(button.disabled,true);
});
