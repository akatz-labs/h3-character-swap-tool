/** Development-only frontend over the existing packaged CPU backend. */
import {spawn} from 'node:child_process';
import {resolve} from 'node:path';
import {createServer} from 'vite';

const port=Number(process.env.H3_DEV_BACKEND_PORT||8081);
if(!Number.isInteger(port)||port<1||port>65535)throw Error('Invalid development backend port');
process.env.H3_DEV_API_ORIGIN=`http://127.0.0.1:${port}`;
process.env.H3_DEV_HOST||='0.0.0.0';
process.env.H3_DEV_PORT||='8080';
process.env.H3_DEV_CACHE||='/data/frontend-dev/vite';
const backend=spawn(process.execPath,[resolve('dist/runtime/container/app.js')],{
  stdio:'inherit',env:{...process.env,H3_CONTAINER_EDGE_PORT:String(port),H3_CONTAINER_EDGE_HOST:'127.0.0.1'}
});
let vite,stopping=false,backendExited=false;
const exited=new Promise(resolve=>{
  backend.once('exit',(code,signal)=>{backendExited=true;resolve({code,signal});});
  backend.once('error',()=>{backendExited=true;resolve({code:1});});
});
async function stop(code=0){
  if(stopping)return;stopping=true;
  await vite?.close();
  if(!backendExited)backend.kill('SIGTERM');
  await exited;process.exitCode=code;
}
backend.once('error',error=>{console.error(error);void stop(1);});
backend.once('exit',code=>{if(!stopping){console.error('Development backend exited',code);void stop(code||1);}});
for(const signal of ['SIGINT','SIGTERM'])process.once(signal,()=>{void stop();});
try{
  let ready=false;
  for(let attempt=0;attempt<60&&!stopping;attempt++){
    if(backendExited)throw Error('Development backend exited before readiness');
    try{const response=await fetch(`${process.env.H3_DEV_API_ORIGIN}/api/health`,{signal:AbortSignal.timeout(2000)});if(response.ok){ready=true;break;}}catch{}
    await new Promise(resolve=>setTimeout(resolve,500));
  }
  if(!ready)throw Error('Development backend did not become ready');
  vite=await createServer({configFile:resolve('web/vite.config.ts'),configLoader:'runner'});
  if(stopping){await vite.close();}else{await vite.listen();console.log(JSON.stringify({frontend:'vite',port:Number(process.env.H3_DEV_PORT),backendPort:port}));}
}catch(error){console.error(error);await stop(1);}
