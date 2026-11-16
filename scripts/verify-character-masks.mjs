// Real local metadata writes in a disposable project; no GPU jobs.
import {readFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
const required=k=>{if(!process.env[k])throw Error('Set '+k);return process.env[k]};
const {chromium}=await import(required('H3_PLAYWRIGHT_MODULE'));
const base=required('H3_TEST_ORIGIN'),browser=await chromium.launch({headless:true,executablePath:required('H3_CHROMIUM')});
const context=await browser.newContext({viewport:{width:1440,height:900},extraHTTPHeaders:{Origin:new URL(base).origin,'x-akatz-host':'1'}}),page=await context.newPage(),errors=[];
page.on('pageerror',e=>errors.push(e.message));page.setDefaultTimeout(30000);
const request=async(path,method='GET',data)=>{const response=await page.request.fetch(base+'api/'+path,{method,data});assert(response.ok(),await response.text());return response.json();};
let project;
try{
 project=await request('batches','POST',{name:'Character masks canary '+Date.now()});
 const response=await page.request.post(base+'api/import',{multipart:{batch_id:project.id,files:{name:'masks.mp4',mimeType:'video/mp4',buffer:await readFile(required('H3_TEST_VIDEO'))}},timeout:120000});const imported=await response.json();assert(!imported.errors.length);
 let clip=(await request('state')).clips.find(c=>c.batch_id===project.id),shot=clip.shots[0],basePath=`clips/${clip.id}/shots/${shot.id}`;
 for(const name of ['First actor','Second actor']){
  const character=await request(`batches/${project.id}/characters`,'POST',{name});
  const upload=await page.request.post(base+`api/batches/${project.id}/characters/${character.id}/image`,{multipart:{revision:String(character.revision),file:{name:'ref.png',mimeType:'image/png',buffer:await readFile(required('H3_TEST_IMAGE'))}}});assert(upload.ok(),await upload.text());
  clip=await request(basePath+'/cast/characters','POST',{revision:clip.revision,segment_id:shot.segments[0].id,character_ids:[character.id],create_mask:false});
 }
 for(let i=0;i<2;i++)clip=await request(basePath+'/cast/masks','POST',{revision:clip.revision,segment_id:shot.segments[0].id});
 const tracks=clip.shots[0].cast_setup.mask_tracks;assert.equal(clip.shots[0].segments[0].use_mask,false);
 await page.goto(base+'#/project/'+project.id+'/shots');const fields=page.locator('[data-character-mask]');await fields.nth(1).waitFor();assert.equal(await page.locator('.segment-mask-region').count(),0);
 assert.equal(await fields.nth(0).inputValue(),'');assert.equal(await fields.nth(1).inputValue(),'');
 for(const [index,mask] of [[0,tracks[0].id],[1,tracks[0].id],[0,tracks[1].id]]){
  await fields.nth(index).selectOption(mask);await page.waitForTimeout(550);
 }
 clip=await request('clips/'+clip.id);assert.deepEqual(new Set(clip.shots[0].segments[0].cast_inputs.mask_tracks),new Set(tracks.map(t=>t.id)));
 await page.reload();await fields.nth(1).waitFor();assert.equal(await fields.nth(0).inputValue(),tracks[1].id);assert.equal(await fields.nth(1).inputValue(),tracks[0].id);
 await fields.nth(0).click();await page.screenshot({path:'/tmp/h3-character-mask-open.png'});await page.keyboard.press('Escape');
 await page.screenshot({path:'/tmp/h3-character-masks.png'});
 for(const size of [{width:1280,height:720},{width:390,height:844}]){
  await page.setViewportSize(size);await fields.nth(0).scrollIntoViewIfNeeded();
  const rect=await fields.nth(0).boundingBox(),card=await page.locator('.segment-character-row').first().boundingBox();assert(rect.x>=card.x&&rect.x+rect.width<=card.x+card.width);
 }
 await page.setViewportSize({width:1440,height:900});
 await fields.nth(0).selectOption('');await page.waitForTimeout(500);await fields.nth(1).selectOption('');await page.waitForTimeout(500);
 clip=await request('clips/'+clip.id);assert.deepEqual(clip.shots[0].segments[0].cast_inputs.mask_tracks,[]);assert.equal(clip.shots[0].segments[0].use_mask,false);
 assert(!errors.length,errors.join('\n'));assert(!((await page.locator('#toast').allTextContents()).join('')).includes('changed elsewhere'));
 console.log('PASS real local mask defaults, sharing, union, persistence, responsive dropdowns and clearing masks');
}finally{if(project)await page.request.delete(base+'api/batches/'+project.id);await browser.close();}
