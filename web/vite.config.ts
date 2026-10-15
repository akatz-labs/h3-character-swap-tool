import {defineConfig,type Plugin} from 'vite';
import react from '@vitejs/plugin-react';
import {fileURLToPath} from 'node:url';
import {resolve,sep} from 'node:path';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';

const root=fileURLToPath(new URL('.',import.meta.url)),repo=resolve(root,'..');
const exec=promisify(execFile);
function legacyEditorDev():Plugin {
  return {name:'h3-legacy-editor-dev',apply:'serve',async configureServer(server){
    const script=resolve(repo,'scripts/build_legacy_bridge.mjs'),staticRoot=resolve(repo,'static');
    const generated=new Set(['app-kit-icons.js','app-kit-icons.LICENSE.txt'].map(name=>resolve(staticRoot,'vendor',name)));
    const rebuild=()=>exec(process.execPath,[script],{cwd:repo});
    await rebuild();
    let timer:ReturnType<typeof setTimeout>|undefined,closed=false,pending=Promise.resolve();
    const changed=(file:string)=>{
      if(generated.has(file)||(file!==script&&!file.startsWith(staticRoot+sep)))return;
      clearTimeout(timer);
      timer=setTimeout(()=>{pending=pending.then(async()=>{
        if(closed)return;
        try{await rebuild();server.moduleGraph.invalidateAll();server.ws.send({type:'full-reload',path:'*'});}
        catch(error){server.config.logger.error(String(error));server.ws.send({type:'error',err:{message:String(error),stack:''}});}
      });},150);
    };
    server.watcher.add([staticRoot,script]);
    server.watcher.on('add',changed).on('change',changed).on('unlink',changed);
    server.httpServer?.once('close',()=>{closed=true;clearTimeout(timer);server.watcher.off('add',changed).off('change',changed).off('unlink',changed);});
    // Vite serves the repository static directory directly; exports need no symlinks.
    server.middlewares.use((request,_response,next)=>{
      if(request.url?.startsWith('/static/')) request.url='/@fs/'+staticRoot+request.url.slice('/static'.length);
      next();
    });
    server.middlewares.use('/__h3/dev',(_request,response)=>{
      response.setHeader('Content-Type','application/json');response.end(JSON.stringify({frontend:'vite',legacyEditorWatch:true}));
    });
  }};
}

export default defineConfig(()=>{
  const backend=process.env.H3_DEV_API_ORIGIN||'http://127.0.0.1:8081';
  const publicUrl=process.env.H3_DEV_PUBLIC_ORIGIN?new URL(process.env.H3_DEV_PUBLIC_ORIGIN):null;
  return {root,publicDir:false as const,plugins:[react(),legacyEditorDev()],
    cacheDir:process.env.H3_DEV_CACHE||resolve(repo,'node_modules/.vite'),
    build:{outDir:'../dist/web',emptyOutDir:true},
    server:{host:process.env.H3_DEV_HOST||'127.0.0.1',port:Number(process.env.H3_DEV_PORT||5173),strictPort:true,
      allowedHosts:publicUrl?[publicUrl.hostname]:[],
      ...(publicUrl?{ws:{host:publicUrl.hostname,protocol:publicUrl.protocol==='https:'?'wss':'ws',clientPort:Number(publicUrl.port||(publicUrl.protocol==='https:'?443:80))}}:{}),
      watch:{ignored:/\/web\/generated(?:\/|$)/},
      fs:{allow:[repo]},
      proxy:Object.fromEntries(['/api','/kit-api','/kit-media','/__akatz'].map(prefix=>[prefix,{target:backend,ws:true,changeOrigin:false}]))
    }};
});
