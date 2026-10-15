/** Foreground source launcher; the shared executor remains a separate process. */
import {spawn,type ChildProcess} from 'node:child_process';
import {createHash} from 'node:crypto';
import {readFile,mkdir,writeFile,stat} from 'node:fs/promises';
import {resolve,join} from 'node:path';
import {parseArgs} from 'node:util';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {createMediaService} from '@akatz/app-config/media';
import {ecosystemPaths,readInstallation} from '@akatz/app-config/ecosystem';
import {ensureSharedMedia} from '@akatz/app-config/local-services';
import {MediaClient} from '@akatz/app-config/media-client';
import {z} from 'zod';

const {values}=parseArgs({options:{data:{type:'string'},port:{type:'string',default:'8898'},'worker-port':{type:'string'},python:{type:'string'}}});
if(process.platform!=='linux')throw Error('The source launcher is qualified for Linux only');
const paths=await ecosystemPaths('h3-workbench'),installation=await readInstallation('h3-workbench');
const number=z.coerce.number().int().min(1).max(65535),port=number.parse(values.port);
const root=resolve(fileURLToPath(new URL('../..',import.meta.url))),data=resolve(values.data||process.env.H3_WORKBENCH_STATE||paths.appRoot);
let savedWorkerPort:number|undefined;
try{const saved=JSON.parse(await readFile(join(data,'source-workers.json'),'utf8'));savedWorkerPort=number.parse(saved.local.target.port);}
catch(error){if((error as NodeJS.ErrnoException).code!=='ENOENT')throw error;}
const workerPort=number.parse(values['worker-port']||savedWorkerPort||installation?.executorPort||process.env.AKATZ_EXECUTOR_PORT||8841);
const python=resolve(values.python||join(root,'.venv/bin/python'));
const children:ChildProcess[]=[];
const revision=createHash('sha256').update(await readFile(join(root,'dist/web/index.html'))).update(await readFile(join(root,'dist/runtime/app-host.js'))).digest('hex');
const {captureProcessIdentity}=await import(pathToFileURL(join(root,'node_modules/@akatz/app-config/dist/process-identity.js')).href);
const parent=captureProcessIdentity(process.pid);
if(!parent)throw Error('Cannot establish source launcher process identity');
await mkdir(data,{recursive:true});
const workers={local:{target:{kind:'local',port:workerPort}}},workersFile=join(data,'source-workers.json');
try{await writeFile(workersFile,JSON.stringify(workers),{flag:'wx',mode:0o600});}
catch(error){if((error as NodeJS.ErrnoException).code!=='EEXIST')throw error;
 if(JSON.stringify(JSON.parse(await readFile(workersFile,'utf8')))!==JSON.stringify(workers))throw Error('Saved worker differs; use its original port or review source-workers.json before changing it');}
// Existing app-local catalogs remain authoritative until an explicit migration.
const legacyMedia=await stat(join(data,'shared-library/catalog.sqlite')).then(s=>s.isFile()).catch(error=>{if(error.code==='ENOENT')return false;throw error;});
const media=legacyMedia?await createMediaService({root:join(data,'shared-library')}):null;
let stopping=false;
async function stop(){
 if(stopping)return;stopping=true;
 for(const child of [...children].reverse())if(child.exitCode===null)child.kill('SIGTERM');
 await Promise.all(children.map(child=>child.exitCode!==null||child.signalCode!==null?Promise.resolve():new Promise<void>(done=>{
  const timer=setTimeout(()=>{child.kill('SIGKILL');},8000);child.once('exit',()=>{clearTimeout(timer);done();});
 })));
 await media?.close();
}
for(const signal of ['SIGINT','SIGTERM'] as const)process.once(signal,()=>{void stop();});
async function start(executable:string,args:string[],env:NodeJS.ProcessEnv,node=false):Promise<string>{
 const child=spawn(executable,args,{cwd:root,env:{...process.env,...env},stdio:node?['ignore','pipe','pipe','ipc']:['ignore','pipe','pipe']});children.push(child);
 child.stderr?.on('data',bytes=>process.stderr.write(bytes));
 child.once('exit',()=>{if(!stopping){process.exitCode=1;void stop();}});
 return new Promise((done,fail)=>{
  let buffer='',settled=false;
  const timer=setTimeout(()=>finish(new Error('Source child did not become ready')),30000);
  function finish(error:Error|null,url?:string){if(settled)return;settled=true;clearTimeout(timer);if(error)fail(error);else done(z.string().url().parse(url));}
  child.once('error',error=>finish(error));child.once('exit',code=>finish(new Error('Source child exited ('+code+')')));
  child.on('message',message=>{const value=z.object({type:z.literal('akatz-listening'),url:z.string().url()}).safeParse(message);if(value.success)finish(null,value.data.url);});
  child.stdout?.on('data',(bytes:Buffer)=>{
   process.stdout.write(bytes);if(settled)return;buffer+=bytes.toString();
   if(buffer.length>65536){finish(new Error('Startup output exceeded its bound'));return;}
   let index;while((index=buffer.indexOf('\n'))>=0){const line=buffer.slice(0,index);buffer=buffer.slice(index+1);
    if(line.startsWith('AKATZ_APP_READY ')){try{finish(null,z.object({url:z.string().url()}).parse(JSON.parse(line.slice(16))).url);}catch{finish(new Error('Invalid domain readiness'));}}}
  });
 });
}
try{
 let mediaUrl:string;
 if(media){
  await new Promise<void>((done,fail)=>{media.server.once('error',fail);media.server.listen(0,'127.0.0.1',done);});
  const address=media.server.address();if(!address||typeof address==='string')throw Error('Library did not bind');
  mediaUrl='http://127.0.0.1:'+address.port;
 }else mediaUrl=(await ensureSharedMedia()).url;
 const env:NodeJS.ProcessEnv={H3_WORKBENCH_PORTABLE:'1',H3_WORKBENCH_STATE:data,H3_WORKBENCH_MEDIA:join(data,'media'),
  H3_WORKBENCH_MODELS_ROOT:process.env.H3_WORKBENCH_MODELS_ROOT||installation?.modelsRoot||paths.modelsRoot,
  H3_WORKBENCH_BASE_PATH:'',H3_WORKBENCH_PACKAGE_REVISION:revision,H3_WORKBENCH_NODE:process.execPath,H3_WORKBENCH_PYTHON:python,
  H3_WORKBENCH_WEB:join(root,'dist/web'),PYTHONPATH:root,PYTHONDONTWRITEBYTECODE:'1',PYTHONNOUSERSITE:'1',AKATZ_APP_PARENT:JSON.stringify(parent),
  H3_WORKBENCH_WORKERS:workersFile,H3_WORKBENCH_REGISTRY_TARGET:JSON.stringify({kind:'local',port:workerPort,name:'Local source worker'}),AKATZ_MEDIA_URL:mediaUrl};
 const origin=await start(python,[join(root,'node_modules/@akatz/app-kit/desktop/python/owned_domain.py'),
  '--module','h3workbench.app:app','--app-id','h3-workbench','--data-root',data,'--state-env','H3_WORKBENCH_STATE'],env);
 const health=z.object({appId:z.literal('h3-workbench'),instanceId:z.string(),revision:z.literal(revision)}).parse(await fetch(new URL('/api/health',origin)).then(r=>r.json()));
 const url=await start(process.execPath,['--import',join(root,'node_modules/@akatz/app-config/src/desktop-child.mjs'),join(root,'dist/runtime/app-host.js')],
  {...env,H3_WORKBENCH_DOMAIN_ORIGIN:origin.replace(/\/$/,''),H3_WORKBENCH_HOST_PORT:String(port)},true);
 const library=new MediaClient(mediaUrl);
 await library.registerApp({appId:'h3-workbench',instanceId:health.instanceId,name:'H3 Character Swap',dataRoot:data});
 const sync=async()=>{
  const state=z.object({batches:z.array(z.object({id:z.string(),name:z.string(),created:z.number()}))}).parse(await fetch(new URL('/api/state',origin)).then(r=>r.json()));
  for(const p of state.batches)await library.registerProject({appId:'h3-workbench',instanceId:health.instanceId,projectId:p.id,name:p.name.slice(0,180),createdAt:new Date(p.created*1000).toISOString()});
 };
 await sync();
 let syncing=false;const timer=setInterval(()=>{if(!syncing&&!stopping){syncing=true;void sync().catch(error=>console.error('Library project sync:',error.message)).finally(()=>{syncing=false;});}},5000);
 timer.unref();
 console.log(JSON.stringify({phase:'ready',url,data,workerPort,revision,mediaUrl,sharedLibrary:!legacyMedia,modelsRoot:env.H3_WORKBENCH_MODELS_ROOT}));
}catch(error){await stop();throw error;}
