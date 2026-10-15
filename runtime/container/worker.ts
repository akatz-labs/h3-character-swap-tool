/** The existing executor is the only admission owner inside a fresh worker. */
import {createExecutorServer} from '@akatz/app-config/executor-server';
import {ExecutorClient} from '@akatz/app-config/executor-client';
import {readFile,mkdir,writeFile,rename,unlink} from 'node:fs/promises';
import {join} from 'node:path';
import {z} from 'zod';
const root=process.env.H3_WORKER_ROOT||'/opt/h3-worker',state=process.env.H3_WORKER_STATE||'/var/lib/akatz';
const port=z.coerce.number().int().min(1).max(65535).parse(process.env.H3_WORKER_PORT||8841);
await mkdir(state,{recursive:true});
await unlink(join(state,'worker-ready.json')).catch(error=>{if(error.code!=='ENOENT')throw error;});
const installation=JSON.parse(await readFile(join(root,'binding/installation.json'),'utf8'));
const catalog=JSON.parse(await readFile(join(root,'resource-catalog.json'),'utf8'));
const service=createExecutorServer({root:join(state,'executor')});
await new Promise<void>((done,fail)=>{service.server.once('error',fail);service.server.listen(port,'127.0.0.1',done);});
const client=new ExecutorClient({kind:'local',port});
try{
 const plan=z.object({id:z.string(),digest:z.string().regex(/^[a-f0-9]{64}$/)}).parse(await client.json('/v1/plans',installation));
 await client.json('/v1/install',{planId:plan.id,digest:plan.digest});
 await client.json('/v1/resources',catalog);
 const settings=process.env.H3_WORKER_SETTINGS?JSON.parse(await readFile(process.env.H3_WORKER_SETTINGS,'utf8')):
  {workspace:root+'/workspace',modelsRoot:'/models',pythonExecutable:'/usr/local/bin/python',cgExecutable:'/usr/local/bin/cg',environment:'h3-docker',comfyUrl:'http://127.0.0.1:8188'};
 await client.json('/v1/resources/h3-workbench/settings',settings);
 const health=await client.health();
 const ready={schemaVersion:1,executorId:health.executorId,installationId:installation.id,pid:process.pid};
 await writeFile(join(state,'worker-ready.pending'),JSON.stringify(ready)+'\n',{mode:0o600});
 await rename(join(state,'worker-ready.pending'),join(state,'worker-ready.json'));
 console.log(JSON.stringify({...ready,phase:'ready',modelsIncluded:false}));
}catch(error){await service.close();throw error;}
let stopping=false;
for(const signal of ['SIGTERM','SIGINT'])process.once(signal,()=>{void(async()=>{
 if(stopping)return;stopping=true;
 await unlink(join(state,'worker-ready.json')).catch(()=>{});
 // The durable executor ledger survives this process/container. Interrupted
 // attempts are reconciled or fenced by the SDK; startup never creates a retry.
 await service.close();
})().catch(error=>{console.error(error.message);process.exitCode=1;});});
