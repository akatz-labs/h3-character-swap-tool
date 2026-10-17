import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,readFile,rm,mkdir,cp} from 'node:fs/promises';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createServer} from 'node:http';
import {createHash} from 'node:crypto';
import {createExecutorServer} from '@akatz/app-config/executor-server';
import {bridge} from '../dist/runtime/bridge.js';
import {h3Input,validateGraph,transportGraph,requiredGraphModels,run as runH3} from '../dist/runtime/h3-adapter.js';

const hash=v=>createHash('sha256').update(v).digest('hex');
test('the MVP model profile covers the active graph without disabled face tracking',async()=>{
 const graph=JSON.parse(await readFile(new URL('../templates/render.json',import.meta.url),'utf8'));
 graph['850'].inputs.lora_name='h3-character-swap-v1/h3_character_swap_pro4500_1000.safetensors';
 const profile=JSON.parse(await readFile(new URL('../docker/models-v1.json',import.meta.url),'utf8'));
 const names=profile.models.map(m=>m.relativePath),required=requiredGraphModels(graph,names);
 assert(!('802' in graph));
 assert(!Object.values(graph).some(n=>/Face|LTXVideo/.test(n.class_type)));
 assert(!names.some(name=>/DWPose|ltx|v2-1000|B-step/.test(name)));
 assert.equal(names.length,7);
 assert(required.includes('loras/h3-character-swap-v1/h3_character_swap_pro4500_1000.safetensors'));
 assert.equal(required.length,6);
});
test('a prebuilt worker installs without models while generation remains unavailable',async t=>{
 const root=await mkdtemp(join(tmpdir(),'h3-empty-models-'));
 const adapter=join(root,'adapter.mjs'),model=join(root,'sam.safetensors');
 await writeFile(adapter,'export async function run(){}');
 const service=createExecutorServer({root:join(root,'worker'),dispatch:false});
 t.after(async()=>{await service.close();await rm(root,{recursive:true,force:true});});
 const installation={id:'empty-models',appId:'h3-workbench',adapter,revision:hash('export async function run(){}'),
  features:['runtime-installed','sam-frame'],probes:[{kind:'file',path:adapter},{kind:'file',path:model,expectedSize:4,features:['sam-frame']}]};
 const plan=service.executor.plan(installation);await service.executor.install(plan.id,plan.digest);
 const missing=(await service.executor.readiness('h3-workbench'))[0];
 assert.deepEqual(missing.features,['runtime-installed']);assert.equal(missing.installation.id,installation.id);
 await writeFile(model,'abcd');
 const available=await service.executor.inspectInstallation(missing.installation);
 assert.deepEqual(available.features,['runtime-installed','sam-frame']);
 await writeFile(model,'wrong-size');
 assert.deepEqual((await service.executor.inspectInstallation(missing.installation)).features,['runtime-installed']);
});
test('packaged bridge CLI runs from a decoded path containing spaces',async t=>{
  await mkdir(new URL('../.data',import.meta.url),{recursive:true});
  const directory=await mkdtemp(fileURLToPath(new URL('../.data/bridge path ',import.meta.url)));
  t.after(()=>rm(directory,{recursive:true,force:true}));
  await cp(fileURLToPath(new URL('../dist/runtime',import.meta.url)),directory,{recursive:true});
  const child=spawnSync(process.execPath,[join(directory,'bridge.js')],{input:JSON.stringify({action:'unrecognized'}),encoding:'utf8'});
  assert.equal(child.status,1);assert.equal(JSON.parse(child.stdout).value.message,'Unknown H3 bridge operation');
});
const canonical=v=>Array.isArray(v)?'['+v.map(canonical).join(',')+']':v&&typeof v==='object'?'{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+canonical(v[k])).join(',')+'}':JSON.stringify(v);
function inputs(revision='a'.repeat(64),graphDigest='b'.repeat(64)) {
  return {version:1,operation:'sam-frame',operationRevision:revision,graphFile:'captured-api.json',graphDigest,
    family:{label:'SAM',key:'c'.repeat(64),loras:[]},profile:'full',frames:1,dimensions:[352,256],
    runtimeCompatibility:{recipeRevision:'d'.repeat(64),adapterRevision:revision,modelDigest:'d'.repeat(64),
      modelConfigurationDigest:'c'.repeat(64),precision:'pinned',residency:'full',allocator:'default'}};
}
test('captured operation validates dimensions, model chain and named node requirements',()=>{
  const input=inputs();assert.equal(h3Input(input).operation,'sam-frame');
  for(const operation of ['alpha-extract','face-track','person-remove']) assert.throws(()=>h3Input({...input,operation}));
  assert.throws(()=>h3Input({...input,dimensions:[351,256]}),/aligned/);
  assert.throws(()=>h3Input({...input,runtimeCompatibility:{...input.runtimeCompatibility,modelConfigurationDigest:'f'.repeat(64)}}),/Model chain/);
  const graph={'1':{class_type:'H3RelayRefineMask',inputs:{}},'2':{class_type:'H3RelaySaveMask',inputs:{}}};
  assert.deepEqual(validateGraph(graph,h3Input(input),['H3RelayRefineMask','H3RelaySaveMask']),graph);
  assert.throws(()=>validateGraph({...graph,'3':{class_type:'UnreviewedExec',inputs:{}}},h3Input(input),['H3RelayRefineMask','H3RelaySaveMask']),/undeclared/);
  assert.throws(()=>validateGraph({'1':graph['1']},h3Input(input),['H3RelayRefineMask']),/named operation/);
});
test('two SDK stages cannot share mutable worker scratch input names',()=>{
  const graph={'1':{class_type:'LoadImage',inputs:{image:'frame.png'}},'2':{class_type:'H3RelayRefineMask',
    inputs:{prior_file:'prior.npz',prompt:'frame.png',text:'prior.npz',model:'sam3.safetensors'}}};
  const original=structuredClone(graph);
  const first=transportGraph(graph,'sdk-job-a',['frame.png','prior.npz']),next=transportGraph(graph,'sdk-job-b',['frame.png','prior.npz']);
  assert.equal(first['1'].inputs.image,'sdk-job-a-frame.png');assert.equal(next['1'].inputs.image,'sdk-job-b-frame.png');
  assert.notEqual(first['2'].inputs.prior_file,next['2'].inputs.prior_file);
  assert.equal(first['2'].inputs.prompt,graph['2'].inputs.prompt);assert.equal(first['2'].inputs.model,graph['2'].inputs.model);
  assert.equal(first['2'].inputs.text,graph['2'].inputs.text);
  assert.deepEqual(graph,original);assert.throws(()=>transportGraph(graph,'../foreign',['frame.png']),/identifier/);
});
test('segments require the explicit single-pass worker operation',()=>{
  const input=h3Input({...inputs(),operation:'h3-render-segments-v1'});
  const graph=Object.fromEntries(['StartLoop','EndLoop','SamplerCustomAdvanced','SaveVideo','H3RelayFinishWindows','H3RelayWindowPlan'].map((class_type,i)=>[String(i),{class_type,inputs:class_type==='H3RelayWindowPlan'?{schedule:'segment'}:{}}]));
  const nodes=Object.values(graph).map(n=>n.class_type);
  assert.deepEqual(validateGraph(graph,input,nodes),graph);
  assert.throws(()=>validateGraph({...graph,'5':{...graph['5'],inputs:{}}},input,nodes));
});
test('a mismatched named graph is terminal before any runtime enqueue',async t=>{
 const root=await mkdtemp(join(tmpdir(),'h3-invalid-operation-'));t.after(()=>rm(root,{recursive:true,force:true}));
 const graph=JSON.stringify({'1':{class_type:'H3WorkbenchTrackFace',inputs:{}}}),file=join(root,'graph.json');await writeFile(file,graph);
 const controller=join(root,'controller.py'),contract=join(root,'contract.json');await writeFile(controller,'must not execute');await writeFile(contract,'{}');
 const configuration={python:process.execPath,controller,controllerRevision:hash('must not execute'),contract,contractRevision:hash('{}'),
  cgExecutable:join(root,'cg'),environmentPath:join(root,'environment'),mediaRoot:join(root,'media'),ownerStateDirectory:join(root,'owner'),port:8345,allowedNodes:['H3WorkbenchTrackFace']};
 const input={...inputs(),operation:'h3-render',graphDigest:hash(graph)};
 const descriptor={version:1,driver:join(root,'adapter.mjs'),driverRevision:input.operationRevision,recipeRevision:input.runtimeCompatibility.recipeRevision,environmentId:'fixture',idlePolicy:{unloadSeconds:300,stopSeconds:900},configuration};
 const context={job:{id:'invalid-stage',feature:'h3-render',sourceJobId:'fixture',inputs:input,files:[]},installation:{revision:input.operationRevision},directory:root,
  runtimeSession:{version:1,id:'fixture',owner:'fixture',installationId:'fixture',descriptor,directory:join(root,'session'),generation:1,processAllocator:'default',status:null},
  checkpoint:()=>assert.fail('No runtime admission is allowed'),isCancellationRequested:()=>false,inputFile:()=>file,outputFile:n=>join(root,n)};
 await assert.rejects(runH3(context),error=>error.terminal===true&&/named operation contract/.test(error.message));
 await assert.rejects(readFile(join(root,'runtime-admission.json')),{code:'ENOENT'});
});
test('a named stage only requires its selected verified models, including optional LoRA variants',()=>{
  const catalog=['checkpoints/sam.safetensors','diffusion_models/render.safetensors','loras/v1/swap.safetensors','loras/v2/swap.safetensors','aux/pose.onnx'];
  const graph={'1':{inputs:{model:'sam.safetensors'}}};
  assert.deepEqual(requiredGraphModels(graph,catalog),['checkpoints/sam.safetensors']);
  assert.deepEqual(requiredGraphModels({'1':{inputs:{model:'render.safetensors',lora:'v2/swap.safetensors'}}},catalog,['aux/pose.onnx']),['aux/pose.onnx','diffusion_models/render.safetensors','loras/v2/swap.safetensors']);
  assert.throws(()=>requiredGraphModels({'1':{inputs:{lora:'swap.safetensors'}}},catalog),/ambiguous/);
  assert.throws(()=>requiredGraphModels({'1':{inputs:{model:'unreviewed.gguf'}}},catalog),/undeclared/);
});
test('H3 Auto resolves exact warm compatibility before admission and freezes only an allowed worker',async t=>{
  const values=[],calls=[],chain=inputs().runtimeCompatibility;
  for(const worker of ['cold','warm']){
    const session={version:1,id:'session-'+worker,owner:worker,installationId:worker+'-install',recipeRevision:chain.recipeRevision,generation:1,
      phase:'warm_idle',processIdentity:{platform:'linux',pid:123,start:'1',boot:'fixture'},compatibility:chain,activeAttempt:null,
      idleSeconds:1,unloadSeconds:300,stopSeconds:900,modelEntries:1,cacheHits:1,observedAt:new Date().toISOString()};
    const status={executorId:worker,checkedAt:new Date().toISOString(),jobs:[],gpuCompute:[],gpuError:null,sessions:worker==='warm'?[{installationId:worker+'-install',status:session}]:[]};
    const service=createServer((req,res)=>{
      calls.push(req.method);res.setHeader('content-type','application/json');
      const body=req.url==='/v1/health'?{executorId:worker,apiVersion:1,capabilities:['managed-runtime-v1','accepted-attempt-v1','cancel-admission-v1','auto-placement-v1']}:
        req.url.startsWith('/v1/installations')?[{features:['sam-frame'],ready:true,installation:{id:worker+'-install',revision:chain.adapterRevision,runtime:{managed:{recipeRevision:chain.recipeRevision}}}}]:status;
      res.end(JSON.stringify(body));
    });
    await new Promise(done=>service.listen(0,'127.0.0.1',done));t.after(()=>new Promise(done=>service.close(done)));
    values.push({worker,target:{kind:'local',port:service.address().port},status,session});
  }
  const request={action:'bind-auto',feature:'sam-frame',candidates:values.map(({worker,target})=>({worker,target})),
    compatibility:{modelConfigurationDigest:chain.modelConfigurationDigest,precision:chain.precision,residency:chain.residency,allocator:chain.allocator}};
  const first=await bridge(request);assert.equal(first.worker,'warm');assert.equal(first.placement.reason,'compatible-warm');
  values[1].session.compatibility={...chain,residency:'offload'};
  assert.equal((await bridge(request)).worker,'cold');assert.equal(first.worker,'warm','the saved selection does not change with later observations');
  delete values[0].status.gpuCompute;
  const limited={...request,candidates:[request.candidates[0]]};await assert.rejects(bridge(limited),/lack verified Auto/);
  await assert.rejects(bridge({...request,compatibility:{...request.compatibility,precision:null}}),/precision/);
  assert(calls.every(method=>method==='GET'),'placement never admits or reorders GPU work');
});

async function fixture(t) {
  const root=await mkdtemp(join(tmpdir(),'h3-shared-bridge-'));
  const adapter=join(root,'adapter.mjs');
  const code=`import {writeFile} from 'node:fs/promises';export async function run(ctx){
    await writeFile(ctx.outputFile('cancelled.json'),'owned-stop-proof');
    throw Object.assign(new Error('Owned attempt cancelled'),{terminal:true,cancelled:true,
      files:[{name:'cancelled.json'}],evidence:{gpuReleased:true,attemptId:ctx.job.id+'-stage-1'}});}`;
  await writeFile(adapter,code);
  const service=createExecutorServer({root:join(root,'worker'),dispatch:false});
  await new Promise(resolve=>service.server.listen(0,'127.0.0.1',resolve));
  t.after(async()=>{await service.close();await rm(root,{recursive:true,force:true});});
  const target={kind:'local',port:service.server.address().port},revision=hash(code);
  const installation={id:'h3-test',appId:'h3-workbench',adapter,revision,features:['sam-frame'],probes:[{kind:'file',path:adapter}],capabilities:['cooperative-cancel-v1']};
  const plan=service.executor.plan(installation);await service.executor.install(plan.id,plan.digest);
  const directory=join(root,'domain');await mkdir(directory);
  const graph=join(directory,'api.json');await writeFile(graph,'{}');
  const b={version:1,target,executorId:service.executor.identity,installationId:installation.id,revision,recipeRevision:'d'.repeat(64),feature:'sam-frame'};
  const value={action:'execute',directory,binding:b,inputs:inputs(revision,hash('{}')),files:{'captured-api.json':graph},instanceId:'h3-test',sourceJobId:'one',targetId:'local'};
  return {service,directory,value};
}
test('lost acceptance followed by cancellation prevents a later duplicate from starting',async t=>{
  const {service,directory,value}=await fixture(t);
  // An upload completed and a request marker was written, but no response was
  // received. Cancellation is reconciled using that exact admission identity.
  const graphHash=hash('{}');
  await service.executor.storage.putObject('input:'+graphHash,Buffer.from('{}'),{expectedHash:graphHash});
  const request={appId:'h3-workbench',instanceId:value.instanceId,sourceJobId:value.sourceJobId,
    executorId:service.executor.identity,installationId:'h3-test',feature:'sam-frame',inputs:value.inputs,
    files:[{name:'captured-api.json',hash:graphHash,size:2}]};
  await writeFile(join(directory,'shared-request.json'),JSON.stringify(request));
  await writeFile(join(directory,'bridge-cancel'),'requested');
  const result=await bridge(value);assert.equal(result.phase,'cancelled');
  const duplicate=await service.executor.submit(request);assert.equal(duplicate.id,result.jobId);
  await service.executor.tick();assert.equal(service.executor.getJob(result.jobId).phase,'cancelled');
  assert.equal(service.executor.jobs().length,1);
  await assert.rejects(bridge({...value,inputs:{...value.inputs,frames:2}}),/inputs changed/);
});
test('terminal cancelled diagnostics download through verified retained-file transport',async t=>{
  const {service,directory,value}=await fixture(t),graphHash=hash('{}');
  await service.executor.storage.putObject('input:'+graphHash,Buffer.from('{}'),{expectedHash:graphHash});
  const request={appId:'h3-workbench',instanceId:value.instanceId,sourceJobId:value.sourceJobId,
    executorId:service.executor.identity,installationId:'h3-test',feature:'sam-frame',inputs:value.inputs,
    files:[{name:'captured-api.json',hash:graphHash,size:2}]};
  const job=await service.executor.submit(request);await writeFile(join(directory,'shared-request.json'),JSON.stringify(request));
  await writeFile(join(directory,'shared-accepted.json'),JSON.stringify({version:1,executorId:service.executor.identity,jobId:job.id,requestDigest:hash(canonical(request))}));
  const outputs=join(service.executor.root,'jobs',job.id,'outputs');await mkdir(outputs,{recursive:true});
  await writeFile(join(outputs,'cancelled.json'),'owned-stop-proof');
  service.executor.save({...job,phase:'cancelled',diagnostics:{files:[{name:'cancelled.json',hash:hash('owned-stop-proof'),size:16}],evidence:{gpuReleased:true}}});
  const result=await bridge(value);assert.equal(result.phase,'cancelled');assert.equal(result.diagnostics.evidence.gpuReleased,true);
  assert.equal(await readFile(join(directory,'runtime/cancelled.json'),'utf8'),'owned-stop-proof');
});
