/** Register H3 with the per-user executor; accepted work outlives this CLI. */
import {parseArgs} from 'node:util';
import {readFile,stat} from 'node:fs/promises';
import {resolve,join} from 'node:path';
import {readInstallation} from '@akatz/app-config/ecosystem';
import {ensureSharedExecutor} from '@akatz/app-config/local-services';
import {ExecutorClient} from '@akatz/app-config/executor-client';
import {z} from 'zod';
const {values}=parseArgs({options:{root:{type:'string'},port:{type:'string'}}});
const saved=await readInstallation('h3-workbench');
const selected=values.root||saved?.root;
if(!selected)throw Error('Install the worker first: npm run source:setup');
const root=resolve(selected),port=z.coerce.number().int().min(1).max(65535).parse(values.port||saved?.executorPort||process.env.AKATZ_EXECUTOR_PORT||8841);
const ecosystem=await stat(join(root,'ecosystem-installation.json')).then(s=>s.isFile()).catch(error=>{if(error.code==='ENOENT')return false;throw error;});
if(!ecosystem){
 // Retain the original launcher for explicitly selected pre-ecosystem installations.
 process.env.H3_WORKER_ROOT=root;process.env.H3_WORKER_STATE=join(root,'state');process.env.H3_WORKER_PORT=String(port);process.env.H3_WORKER_SETTINGS=join(root,'settings.json');
 await import('./container/worker.js');
}else{
 const service=await ensureSharedExecutor({port,requiredCapabilities:['managed-runtime-v1','accepted-attempt-v1']});
 const client=new ExecutorClient({kind:'local',port});
 const installation=JSON.parse(await readFile(join(root,'binding/installation.json'),'utf8'));
 if(installation.appId!=='h3-workbench')throw Error('The selected runtime belongs to another app');
 const plan=z.object({id:z.string(),digest:z.string().regex(/^[a-f0-9]{64}$/)}).parse(await client.json('/v1/plans',installation));
 await client.json('/v1/install',{planId:plan.id,digest:plan.digest});
 await client.json('/v1/resources',JSON.parse(await readFile(join(root,'resource-catalog.json'),'utf8')));
 await client.json('/v1/resources/h3-workbench/settings',JSON.parse(await readFile(join(root,'settings.json'),'utf8')));
 console.log(JSON.stringify({phase:'ready',workerRoot:root,executorId:service.id,executorUrl:service.url,reused:service.reused,shared:true}));
}
