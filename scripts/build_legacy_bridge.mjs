/** Compile app-owned editors into one scoped mount; shared UI is imported. */
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import postcss from 'postcss';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {Icon} from '@akatz/app-kit/react';
const root=fileURLToPath(new URL('../',import.meta.url));
async function writeChanged(path, content) {
  try { if (await readFile(path, 'utf8') === content) return; }
  catch (error) { if (error.code !== 'ENOENT') throw error; }
  await writeFile(path, content);
}
// Render from the pinned shared component; legacy pages never maintain a copy
// of the upstream icon paths or depend on an external icon service.
const refresh=renderToStaticMarkup(createElement(Icon,{name:'refresh'})).replace('ak-icon','h3-icon');
await writeChanged(join(root,'static/vendor/app-kit-icons.js'),'// Generated from @akatz/app-kit. See app-kit-icons.LICENSE.txt.\nconst appKitIcons = '+JSON.stringify({refresh})+';\n');
await writeChanged(join(root,'static/vendor/app-kit-icons.LICENSE.txt'),(await readFile(new URL('../THIRD_PARTY_NOTICES.md',import.meta.resolve('@akatz/app-kit/react')),'utf8')).trimEnd()+'\n');
const names=['vendor/app-kit-icons.js','ui-icons.js','comparison.js','review-player.js','mask-editor.js','mask-playback.js','shots-editor.js','finish-review.js','refine-editor.js','workspace-ui.js','shot-autosave.js','app.js'];
const source=await Promise.all(names.map(name=>readFile(join(root,'static',name),'utf8')));
let app=source.at(-1);
const opening='const $ = (s) => document.querySelector(s),\n  $$ = (s) => [...document.querySelectorAll(s)];';
if(!app.startsWith(opening))throw new Error('Legacy editor entry changed; review its scope adapter');
app=app.slice(opening.length);
app=app.replace('const S = {','const S = {').replace('tab: "anchors",','tab: options.page || "shots",').replace('batch: localStorage.h3batch || null,','batch: options.projectId || localStorage.h3batch || null,');
app=app.replace('    signal,\n    headers:',"    signal: signal ? AbortSignal.any([signal, lifecycle.signal]) : lifecycle.signal,\n    headers:");
const startup='guarded(async () => {\n  await refresh();\n  poll();\n  workerPoll();\n})();';
if(!app.includes(startup))throw new Error('Legacy startup changed; review its lifecycle adapter');
app=app.replace(startup,'const ready = (async () => { await refresh(); poll(); workerPoll(); })();');
app=app.replace('S.worker = e.target.value;', 'S.worker = e.target.value; options.onWorker?.(S.worker);');
app=app.replace('function draw() {','function draw() {\n  syncClipHeader();\n  if(S.batch)options.onLocation?.({projectId:S.batch,page:S.tab});');
app=app.replace('function syncWorkspace() {','function syncWorkspace() {\n  syncClipHeader();');
const prefix=`import {createLegacyScope,createLegacyController} from '../src/legacy-lifecycle.ts';
export function mountLegacy(root, options) {
  const scope=createLegacyScope(root,options.instanceId);
  const {document,localStorage,$,$$,setTimeout,MutationObserver,lifecycle}=scope;
  let clipHeaderKey='';
  function syncClipHeader(){
    const c=clip(),s=shot(),inShot=shotPage(),summary=c?{id:c.id,name:c.name,frames:c.frames,fps:c.fps,width:c.width,height:c.height,status:c.result?'ready':'in-progress',shot:inShot&&s?{id:s.id,name:shotName(c,s),start:s.start,end:s.end}:null}:null;
    const key=JSON.stringify(summary);
    if(key!==clipHeaderKey){clipHeaderKey=key;options.onClip?.(summary);}
  }
`;
const suffix=`
  return createLegacyController(scope,root,{ready,
    prepareLeave:prepareWorkspaceLeave,
    snapshot(){return {projectId:S.batch,clipId:S.clip,shotId:S.shot,page:S.tab,dirty:!!S.dirty || maskHasDraft()}},
    setLocation(next){
      if(next.projectId && next.projectId!==S.batch){S.batch=next.projectId;S.clip=S.data?.clips.find(c=>c.batch_id===S.batch)?.id;S.shot=null;}
      if(next.page)S.tab=next.page==='refine'?'render':next.page;
      S.dirty=false;if(S.data){sidebar();draw()}
    },
    openQueue(worker){S.queueWorker=worker==='auto'?null:worker;dialog((worker==='auto'?'All local workers':workerDisplayName(worker))+' · Job queue',queueHtml());updateQueueDialog();},
    selectWorker(worker){if(worker==='auto'||S.data?.workers.includes(worker)){S.worker=worker;const select=$('#worker');if(select)select.value=worker;}},
    disposeEditors(){disposeMaskPlayback();disposeReviewPlayer();disposeRefineEditor();disposeShotEditor();disposeRenderComparison();}
  });
}
`;
const directory=join(root,'web/generated');await mkdir(directory,{recursive:true});
await writeChanged(join(directory,'legacy-adapter.js'),prefix+source.slice(0,-1).join('\n')+'\n'+app+suffix);
await writeChanged(join(directory,'legacy-adapter.d.ts'),`import type {ClipHeader} from '../src/legacy-lifecycle.ts';
export interface LegacyOptions {projectId:string;page:string;instanceId:string;onLocation?:(value:{projectId:string;page:string})=>void;onClip?:(value:ClipHeader|null)=>void;onWorker?:(worker:string)=>void}
export interface LegacyView {ready:Promise<void>;snapshot():{projectId:string;clipId:string|null;shotId:string|null;page:string;dirty:boolean};update(value:Partial<LegacyOptions>):Promise<boolean>;canLeave():Promise<boolean>;openQueue(worker:string):void;selectWorker(worker:string):void;dispose():void}
export function mountLegacy(root:HTMLElement,options:LegacyOptions):LegacyView;\n`);
const html=await readFile(join(root,'static/index.html'),'utf8');
const body=html.split('<body>')[1].split('<script')[0].replace('<main id="main">','<main id="h3-content">').replace('<aside>','<aside id="h3-clip-browser" aria-label="Clips">');
await writeChanged(join(directory,'legacy-template.ts'),'export const legacyTemplate = '+JSON.stringify(body)+';\n');
let css=await readFile(join(root,'static/style.css'),'utf8');
css=css.replaceAll(':root','&').replace(/(^|\n)body\s*\{/g,'$1& {');
const parsed=postcss.parse(css),globalRules=[];
parsed.walkRules(rule=>{if(rule.selector?.startsWith('html[data-input-mode=')){globalRules.push(rule.toString());rule.remove();}});
const palette={'#fff':'var(--ak-surface)','#ffffff':'var(--ak-surface)','#f6f7f9':'var(--ak-bg)','#202936':'var(--ak-text)',
 '#344052':'var(--ak-text)','#293a53':'var(--ak-text)','#e1e5eb':'var(--ak-border)','#d9dee5':'var(--ak-border)',
 '#e6eaf0':'var(--ak-border)','#747e8b':'var(--ak-muted)','#657184':'var(--ak-muted)','#5e6c7d':'var(--ak-muted)',
 '#7b8795':'var(--ak-muted)','#95a0af':'var(--ak-muted)','#7b8390':'var(--ak-muted)','#808b9b':'var(--ak-muted)',
 '#f0f4fa':'var(--ak-raised)','#edf3ff':'var(--ak-selected)','#edf1f7':'var(--ak-raised)','#e9ebef':'var(--ak-raised)',
 '#2866d8':'var(--ak-accent)'};
parsed.walkDecls(decl=>{if(!decl.value.includes('url('))decl.value=decl.value.replace(/#[a-fA-F0-9]{3,8}\b|\bwhite\b/g,value=>value==='white'?'var(--ak-surface)':palette[value.toLowerCase()]||value);});
parsed.walkAtRules('keyframes',rule=>{globalRules.push(rule.toString());rule.remove();});
await writeChanged(join(directory,'legacy-scoped.css'),'.h3-legacy {\n'+parsed.toString()+'\n}\n'+globalRules.join('\n')+'\n.h3-legacy > header {display:none}.h3-legacy .tabs {display:none}.h3-legacy .layout {min-height:60vh;height:auto}.h3-legacy #h3-content{min-width:0;padding:18px}.h3-legacy {min-width:0}\n');
