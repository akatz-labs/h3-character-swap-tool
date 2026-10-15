/** Container edge plus the existing SDK-owned CPU app lifecycle. */
import {createServer,request as upstreamRequest} from 'node:http';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {planDesktopApp,registerDesktopApp,listDesktopApps} from '@akatz/app-config/desktop-apps';
import {startDesktopApp,stopDesktopApp} from '@akatz/app-config/desktop-runtime';
import {createMediaService} from '@akatz/app-config/media';
import {MediaClient} from '@akatz/app-config/media-client';
import {z} from 'zod';

const root=process.env.H3_CONTAINER_PAYLOAD||'/opt/h3-workbench';
const data=process.env.H3_CONTAINER_DATA||'/data/projects';
const edgePort=z.coerce.number().int().min(1).max(65535).parse(process.env.H3_CONTAINER_EDGE_PORT||8080);
const edgeHost=z.enum(['127.0.0.1','0.0.0.0']).parse(process.env.H3_CONTAINER_EDGE_HOST||'0.0.0.0');
process.env.H3_WORKBENCH_CONTAINER_EDGE='1';
process.env.H3_WORKBENCH_MODELS_ROOT=process.env.H3_WORKBENCH_MODELS_ROOT||'/models';
// Reuse the SDK library API. Its catalog and blobs belong to this app volume,
// rather than depending on a development machine's media daemon.
process.env.AKATZ_MEDIA_URL='http://127.0.0.1:8840';
const media=await createMediaService({root:'/data/shared-library'});
await new Promise<void>((done,fail)=>{media.server.once('error',fail);media.server.listen(8840,'127.0.0.1',done);});
const library=new MediaClient(process.env.AKATZ_MEDIA_URL);
await mkdir(data,{recursive:true});
const writeInitial=async(name:string,value:unknown)=>{
 try{await writeFile(join(data,name),JSON.stringify(value,null,2)+'\n',{flag:'wx',mode:0o600});}
 catch(error){if((error as NodeJS.ErrnoException).code!=='EEXIST')throw error;}
};
await writeInitial('worker-connection.json',{registryTarget:{kind:'local',port:8841,name:'GPU worker'}});
await writeInitial('workers.json',{local:{target:{kind:'local',port:8841},home:'/home/h3',shared:'/var/lib/akatz',port:8188}});
const manifest=JSON.parse(await readFile(join(root,'payload-manifest.json'),'utf8'));
if(manifest.appId!=='h3-workbench')throw new Error('Container payload belongs to another app');
const plan=await planDesktopApp({packageRoot:root,dataRoot:data});
if(typeof plan.digest!=='string')throw new Error('Registration did not return a review digest');
await registerDesktopApp(plan,plan.digest);
const opened=await startDesktopApp('h3-workbench');
z.object({url:z.string().url()}).parse(opened);
const ready=z.object({schemaVersion:z.literal(1),appId:z.literal('h3-workbench'),instanceId:z.string().min(1),revision:z.literal(manifest.revision),url:z.string().url()})
 .parse(JSON.parse(await readFile(join(data,'container-app-host.json'),'utf8')));
const target=new URL(ready.url);
if(target.hostname!=='127.0.0.1'||target.protocol!=='http:')throw new Error('Owned CPU host did not report loopback readiness');
const identity=z.object({appId:z.literal('h3-workbench'),instanceId:z.literal(ready.instanceId),revision:z.literal(ready.revision)});
async function verifyTarget(){
 const response=await fetch(new URL('/__akatz/app-host',target),{signal:AbortSignal.timeout(3000)});
 if(!response.ok||!identity.safeParse(await response.json()).success)throw new Error('Owned CPU app identity is unavailable');
}
await verifyTarget();
const projects=z.object({batches:z.array(z.object({id:z.string(),name:z.string(),created:z.number()}))});
async function syncLibraryRegistry(){
 const response=await fetch(new URL('/api/state',target),{signal:AbortSignal.timeout(5000)});
 if(!response.ok)throw new Error('Project registry is unavailable');
 const state=projects.parse(await response.json());
 await library.registerApp({appId:'h3-workbench',instanceId:ready.instanceId,name:'H3 Character Swap',dataRoot:data});
 for(const project of state.batches)await library.registerProject({appId:'h3-workbench',instanceId:ready.instanceId,projectId:project.id,name:project.name.slice(0,180),createdAt:new Date(project.created*1000).toISOString()});
}
await syncLibraryRegistry();
// Only this fixed edge binds the container interface. The SDK, Python domain,
// and App Kit app host keep their existing loopback/origin checks.
const edge=createServer(async(incoming,response)=>{
 try{await verifyTarget();if(incoming.url?.split('?')[0]==='/kit-media/registry')await syncLibraryRegistry();}catch{response.writeHead(503);response.end('The owned application is unavailable');return;}
 const request=upstreamRequest({hostname:target.hostname,port:target.port,path:incoming.url,method:incoming.method,headers:incoming.headers},reply=>{
  response.writeHead(reply.statusCode||502,reply.headers);reply.pipe(response);
 });
 request.on('error',()=>{if(!response.headersSent)response.writeHead(502);response.end('The application is temporarily unavailable');});
 response.on('close',()=>request.destroy());incoming.pipe(request);
});
edge.on('upgrade',async(incoming,socket,head)=>{
 try{await verifyTarget();}catch{socket.destroy();return;}
 const request=upstreamRequest({hostname:target.hostname,port:target.port,path:incoming.url,headers:incoming.headers});
 request.on('upgrade',(reply,upstream,bytes)=>{
  socket.write('HTTP/1.1 101 Switching Protocols\r\n'+Object.entries(reply.headers).flatMap(([key,value])=>Array.isArray(value)?value.map(v=>key+': '+v):[key+': '+value]).join('\r\n')+'\r\n\r\n');
  if(head.length)upstream.write(head);if(bytes.length)socket.write(bytes);
  upstream.on('error',()=>socket.destroy());socket.on('error',()=>upstream.destroy());upstream.pipe(socket);socket.pipe(upstream);
 });
 request.on('response',()=>socket.destroy());request.on('error',()=>socket.destroy());request.end();
});
await new Promise<void>((done,fail)=>{edge.once('error',fail);edge.listen(edgePort,edgeHost,done);});
console.log(JSON.stringify({appId:'h3-workbench',phase:'ready',revision:manifest.revision,containerPort:edgePort}));
let stopping=false;
for(const signal of ['SIGTERM','SIGINT'])process.once(signal,()=>{void(async()=>{
 if(stopping)return;stopping=true;
 await new Promise<void>(done=>edge.close(()=>done()));
 const saved=(await listDesktopApps()).find(app=>app.appId==='h3-workbench');
 if(saved)await stopDesktopApp(saved);
 await media.close();
})().catch(error=>{console.error(error.message);process.exitCode=1;});});
