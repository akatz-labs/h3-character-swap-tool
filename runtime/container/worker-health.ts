import {readFile} from 'node:fs/promises';
try{
 const ready=JSON.parse(await readFile('/var/lib/akatz/worker-ready.json','utf8'));
 const response=await fetch('http://127.0.0.1:8841/v1/health',{signal:AbortSignal.timeout(3000)});
 const health=await response.json() as {executorId?:string};
 if(!response.ok||health.executorId!==ready.executorId)process.exitCode=1;
}catch{process.exitCode=1;}
