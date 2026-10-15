/** Shared UI/control host. Python owns H3 data; the existing executor owns GPU work. */
import {resolve} from 'node:path';
import {spawn} from 'node:child_process';
import {z} from 'zod';
import {createAppHost,createSetupService,setupMiddleware,SetupStore,RejectedSetupAction,libraryMiddleware,type SetupBackend} from '@akatz/app-kit/server';
import {registrySchema,snapshotSchema,progressSchema,type Registry} from '@akatz/app-kit';
import {ExecutorClient,closeExecutorConnections,type ExecutorTarget} from '@akatz/app-config/executor-client';
import {AppExecutors} from '@akatz/app-config/app-executors';
import {mediaConfig} from '@akatz/app-config/media';

const appId='h3-workbench';
const targetSchema=z.object({id:z.string().optional(),name:z.string().optional(),kind:z.enum(['local','ssh']),host:z.string().optional(),port:z.number().int().min(1).max(65535).optional(),sshPort:z.number().int().optional()}).strict();
const configured=targetSchema.parse(JSON.parse(process.env.H3_WORKBENCH_REGISTRY_TARGET||'{"kind":"local","port":8841}'));
const target:ExecutorTarget={kind:configured.kind,...(configured.id!==undefined?{id:configured.id}:{}),...(configured.name!==undefined?{name:configured.name}:{}),
  ...(configured.host!==undefined?{host:configured.host}:{}),...(configured.port!==undefined?{port:configured.port}:{}),...(configured.sshPort!==undefined?{sshPort:configured.sshPort}:{})};
const executors=new AppExecutors(appId,{localClient:new ExecutorClient(target),autoBootstrap:false});
const unreachable=(error:unknown)=>error instanceof TypeError||(error instanceof Error&&/Executor is offline|SSH executor connection closed/.test(error.message));
const hasGeneration=(installation:{features:string[]})=>installation.features.some(feature=>feature!=='runtime-installed');
async function worker(id:string){
  const registry=await executors.list().catch(error=>{
    if(unreachable(error))throw new RejectedSetupAction('This worker is offline; saved projects remain available.');
    throw error;
  }),row=registry.items.find(r=>r.id===id);
  if(!row?.online||!row.executorId)throw new RejectedSetupAction('This worker is offline; saved projects remain available.');
  const bound:ExecutorTarget=id==='local'?{...target,id,name:target.name||row.name}:row;
  const client=new ExecutorClient(bound);
  if((await client.health()).executorId!==row.executorId)throw new RejectedSetupAction('Worker identity changed. Reconnect it before setup.');
  return {client,row};
}
async function registry():Promise<Registry>{
  let current;
  try{current=await executors.list();}
  catch(error){
    if(!unreachable(error))throw error;
    return registrySchema.parse({selectedExecutorId:null,defaultExecutorId:null,executors:[{
      id:'local',name:target.name||'Configured worker',kind:target.kind,online:false,ready:false,setupRequired:true}]});
  }
  return registrySchema.parse({selectedExecutorId:current.selectedExecutorId,defaultExecutorId:current.defaultExecutorId,
    executors:current.items.map(r=>({id:r.id,name:r.id==='local'?(target.name||r.name):r.name,kind:r.kind,online:r.online,
      ready:r.installations.some(hasGeneration),setupRequired:!r.installations.some(hasGeneration)}))});
}
const transcriptionId='transcription-whisper-medium-en';
const transcriptionSchema=z.object({model:z.object({id:z.literal(transcriptionId)}).passthrough(),jobs:z.array(z.unknown())});
async function transcription(path='',body?:unknown){
  const response=await fetch(origin+'/api/transcription-resource'+path,body===undefined?{}:{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body)});
  const value:unknown=await response.json();
  if(!response.ok)throw new RejectedSetupAction(z.object({detail:z.string()}).safeParse(value).success?z.object({detail:z.string()}).parse(value).detail:'Transcription resources unavailable');
  return value;
}
async function removeWorkerModel(client:ExecutorClient,path:string,body:unknown){
  try{return await client.json(path,body);}
  catch(error){const status=z.object({status:z.number()}).safeParse(error);
    if(path.endsWith('/uninstall-plan')||(status.success&&status.data.status>=400&&status.data.status<500))throw new RejectedSetupAction(error instanceof Error?error.message:'Model removal was rejected');
    throw error;
  }
}
async function jobs(id:string){const {client}=await worker(id);const value=z.object({items:z.array(z.unknown())}).parse(await client.json('/v1/resources/'+appId+'/jobs'));
  const local=transcriptionSchema.parse(await transcription());return progressSchema.parse({executorId:id,jobs:[...local.jobs,...value.items]});}
async function snapshot(id:string){
  const {client,row}=await worker(id);
  const [inventory,progress,status]=await Promise.all([client.json('/v1/resources/'+appId),jobs(id),client.json('/v1/status')]);
  const observed=z.object({executorId:z.string(),sessions:z.array(z.object({installationId:z.string(),status:z.object({phase:z.string()}).nullable()}))}).passthrough().parse(status);
  if(observed.executorId!==row.executorId)throw new Error('Worker status identity changed');
  const workerRaw=z.object({models:z.array(z.unknown()),features:z.array(z.object({id:z.string(),label:z.string(),optional:z.boolean()})),runtimes:z.array(z.object({id:z.string(),name:z.string(),installed:z.boolean()}).passthrough()),
    summary:z.record(z.string(),z.unknown())}).passthrough().parse(inventory);
  const local=transcriptionSchema.parse(await transcription());
  const raw={...workerRaw,models:[...workerRaw.models,local.model]};
  const selected=row.installations.find(r=>r.installation.runtime?.managed);
  if(selected){
    const session=observed.sessions.find(s=>s.installationId===selected.installation.id&&s.status?.phase!=='stopped')?.status;
    const featureSet=new Set(selected.features);
    const runtimeInstalled=featureSet.has('runtime-installed')||selected.ready;
    const modelsReady=raw.summary.requiredInstalled===raw.summary.requiredTotal;
    const requiredFeaturesReady=raw.features.filter(f=>!f.optional).every(f=>featureSet.has(f.id));
    return snapshotSchema.parse({executorId:id,executorName:id==='local'?(target.name||row.name):row.name,jobs:progress.jobs,
      inventory:{...raw,runtimeState:session?.phase==='starting'?'starting':runtimeInstalled?'ready':'needs-repair',runtimeActions:[],
        features:raw.features.map(f=>({...f,ready:featureSet.has(f.id)})),summary:{...raw.summary,ready:runtimeInstalled&&modelsReady&&requiredFeaturesReady},
        runtimes:raw.runtimes.map(r=>r.id==='comfyui'||r.id==='source-pipeline'?{...r,installed:featureSet.has('runtime-installed')||selected.ready,detail:session?'Executor owns the active session':'The executor starts this installed runtime when a generation needs it.'}:r)}});
  }
  return snapshotSchema.parse({executorId:id,executorName:row.name,inventory:raw,jobs:progress.jobs});
}
const backend:SetupBackend={registry,snapshot,jobs,
  async resource(id,kind,fields){
    const {client}=await worker(id),base='/v1/resources/'+appId;
    if(kind==='download'&&Array.isArray(fields.resourceIds)&&fields.resourceIds.includes(transcriptionId)){
      if(fields.resourceIds.length!==1)throw new RejectedSetupAction('Install the optional transcription model separately.');
      return transcription('/install',{});
    }
    if(kind==='uninstall-plan'){
      const resourceId=z.string().parse(fields.resourceId);
      const plan=resourceId===transcriptionId?await transcription('/uninstall-plan',{}):await removeWorkerModel(client,base+'/uninstall-plan',{resourceId});
      return plan;
    }
    if(kind==='uninstall'){
      const input=z.object({planId:z.string(),digest:z.string(),confirm:z.string()}).parse(fields);
      return input.confirm===transcriptionId?transcription('/uninstall',input):removeWorkerModel(client,base+'/uninstall',input);
    }
    if(kind==='settings'){await client.json(base+'/settings',fields.settings);return {complete:true};}
    if(kind==='storage')return client.json(base+'/storage',{path:fields.path});
    if(kind==='scan')return client.json(base+'/scan',{});
    if(kind==='download')return client.json(base+'/download',{resourceIds:fields.resourceIds});
    if(kind==='setup-required'){
      const inventory=z.object({models:z.array(z.object({id:z.string(),requirement:z.string(),state:z.string()}))}).parse(await client.json(base));
      const resourceIds=inventory.models.filter(m=>m.requirement==='required'&&m.state!=='installed').map(m=>m.id);
      if(!resourceIds.length)return {complete:true};
      return client.json(base+'/download',{resourceIds});
    }
    if(kind==='setup-confirm')return client.json(base+'/setup',{planId:fields.planId,digest:fields.digest,resourceIds:fields.resourceIds});
    throw new RejectedSetupAction('Install the pinned H3 worker recipe first. The executor starts its managed session on generation.');
  },
  connectionPlan:input=>executors.operation('connect-plan',input),
  async connect(id,digest,workerId){
    const saved=z.object({appId:z.literal(appId),kind:z.literal('connect'),plan:z.object({digest:z.string(),target:z.object({id:z.string()})})}).parse(await executors.local.json('/v1/management-plans/'+encodeURIComponent(id)));
    if(saved.plan.digest!==digest||saved.plan.target.id!==workerId)throw new RejectedSetupAction('Connection review changed');
    await executors.operation('connect-confirm',{id,digest});
  },
};
const data=resolve(process.env.H3_WORKBENCH_STATE||'.data');
const trustedOrigins=(process.env.H3_WORKBENCH_TRUSTED_ORIGINS||'').split(',').filter(Boolean);
const origin=process.env.H3_WORKBENCH_DOMAIN_ORIGIN||'http://127.0.0.1:8330';
const revision=z.string().regex(/^[a-f0-9]{64}$/).parse(process.env.H3_WORKBENCH_PACKAGE_REVISION);
const health=z.object({appId:z.literal(appId),instanceId:z.string().min(1),revision:z.literal(revision)}).passthrough();
const identity=health.parse(await fetch(origin+'/api/health',{signal:AbortSignal.timeout(3000)}).then(r=>{if(!r.ok)throw new Error('Domain service is unavailable');return r.json();}));
const python=process.env.H3_WORKBENCH_PYTHON||'python3';
function thumbnail(bytes:Uint8Array):Promise<Uint8Array>{
  return new Promise((done,fail)=>{
    const script='from PIL import Image,ImageOps\nimport sys\nImage.MAX_IMAGE_PIXELS=50000000\nim=ImageOps.exif_transpose(Image.open(sys.stdin.buffer))\nim.thumbnail((512,512))\nim.convert("RGB").save(sys.stdout.buffer,format="WEBP",quality=82)';
    const child=spawn(python,['-c',script],{stdio:['pipe','pipe','pipe']}),chunks:Buffer[]=[];let length=0;
    const timer=setTimeout(()=>{child.kill();fail(new Error('Image preview timed out'));},10000);
    child.on('error',fail);child.stderr.resume();child.stdout.on('data',(data:Buffer)=>{length+=data.length;if(length>2*1024**2){child.kill();fail(new Error('Image preview is too large'));}else chunks.push(data);});
    child.on('close',code=>{clearTimeout(timer);if(code===0)done(Buffer.concat(chunks));else fail(new Error('Image preview is unavailable'));});child.stdin.on('error',()=>{});child.stdin.end(bytes);
  });
}
const media=await mediaConfig();
const sharedLibrary=libraryMiddleware({origin:media.url,thumbnail,trustedOrigins});
const host=createAppHost({identity:{appId,instanceId:identity.instanceId,revision},assetsDirectory:resolve(process.env.H3_WORKBENCH_WEB||'dist/web'),trustedOrigins,
  middleware:[setupMiddleware(createSetupService(backend,new SetupStore(resolve(data,'app-kit-setup'))),{trustedOrigins}),
    async(request,response,next)=>{await sharedLibrary(request,response,next);}],
  domain:{origin,prefixes:['/api','/static'],healthPath:'/api/health',verifyHealth:v=>health.safeParse(v).success&&health.parse(v).instanceId===identity.instanceId,websocket:true}});
const listening=await host.listen(Number(process.env.H3_WORKBENCH_HOST_PORT||'8350'));
process.send?.({type:'akatz-listening',url:listening});
console.log(JSON.stringify({appId,instanceId:identity.instanceId,revision,phase:'ready'}));
let stopping=false;async function stop(){if(stopping)return;stopping=true;await host.close();await closeExecutorConnections();}
process.once('SIGTERM',()=>{void stop();});process.once('SIGINT',()=>{void stop();});
