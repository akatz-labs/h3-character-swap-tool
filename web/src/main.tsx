import {useCallback,useEffect,useLayoutEffect,useMemo,useRef,useState} from 'react';
import {createRoot} from 'react-dom/client';
import {ResourceController,createSetupResourceAdapter,createLibraryAdapter,setupClient,parseLocation,locationHash,type AppDefinition,type AppLocation} from '@akatz/app-kit';
import {Dialog,Icon,ProjectsPage,ResourcesPage,WorkerSetup,SharedLibraryBrowser,useResources,Notice} from '@akatz/app-kit/react';
import '@akatz/app-kit/styles.css';
import '../generated/legacy-scoped.css';
import './theme.css';
import './workspace.css';
import {legacyTemplate} from '../generated/legacy-template.ts';
import {mountLegacy,type LegacyView} from '../generated/legacy-adapter.js';
import {domain,http,projectsFor,type DomainState} from './api.ts';
import {WorkbenchHeader} from './WorkbenchHeader.tsx';
import type {ClipHeader} from './legacy-lifecycle.ts';

const definition:AppDefinition={id:'h3-workbench',title:'H3 Character Swap',subtitle:'Character performance' ,sharedLibrary:true,
  projectPages:[{id:'shots',label:'Shots',step:1},{id:'anchors',label:'Anchors',step:2},{id:'render',label:'Render',step:3},{id:'review',label:'Review',step:5}]};
const controller=new ResourceController(createSetupResourceAdapter(http));
const setup=setupClient(http),library=createLibraryAdapter(http);
function Editors({projectId,page,instanceId,view,onError,onLocation,worker,onWorker}:{projectId:string;page:string;instanceId:string;view:React.RefObject<LegacyView|null>;onError:(value:string)=>void;onLocation:(value:{projectId:string;page:string})=>void;worker:string|null;onWorker:(worker:string)=>void}){
  const tabs=useRef<HTMLElement>(null);
  const root=useRef<HTMLDivElement>(null),[clipsOpen,setClipsOpen]=useState(false),[clipHeader,setClipHeader]=useState<ClipHeader|null>(null);
  useLayoutEffect(()=>{
    const element=root.current;if(!element)return;
    element.innerHTML=legacyTemplate;
    const mounted=mountLegacy(element,{projectId,page,instanceId,onLocation,onClip:setClipHeader,onWorker});view.current=mounted;
    const beforeUnload=(event:BeforeUnloadEvent)=>{if(mounted.snapshot().dirty){event.preventDefault();event.returnValue='';}};
    window.addEventListener('beforeunload',beforeUnload);
    void mounted.ready.catch(e=>onError(e instanceof Error?e.message:String(e)));
    return()=>{window.removeEventListener('beforeunload',beforeUnload);mounted.dispose();if(view.current===mounted)view.current=null;};
    // Domain polling updates editors in place; navigation is a separate lifecycle operation.
  },[instanceId,view,onError,onLocation,onWorker]);
  useEffect(()=>{view.current?.update({projectId,page});},[projectId,page,view]);
  useEffect(()=>{void view.current?.ready.then(()=>{if(worker)view.current?.selectWorker(worker);});},[worker,instanceId,view]);
  useEffect(()=>{tabs.current?.querySelector('[aria-current=page]')?.scrollIntoView({block:'nearest',inline:'nearest'});},[page]);
  return <section className="h3-editor-workspace" data-clips-open={clipsOpen}>
    <div className="h3-project-tools">
      <button className="h3-clips-toggle" aria-expanded={clipsOpen} aria-controls="h3-clip-browser" onClick={()=>setClipsOpen(!clipsOpen)}><Icon name="film"/>Clips</button>
      {clipHeader?.shot&&<button className="h3-back-clip" onClick={()=>view.current?.update({page:'shots'})}>← Clip overview</button>}
      <nav ref={tabs} className="h3-clip-tabs" aria-label="Clip sections">{definition.projectPages.filter(tab=>clipHeader?.shot?['anchors','render'].includes(tab.id):['shots','review'].includes(tab.id)).map(tab=><button key={tab.id} data-page={tab.id} aria-current={page===tab.id?'page':undefined} onClick={()=>view.current?.update({page:tab.id})}><Icon name={tab.id==='shots'?'film':tab.id==='anchors'?'image':tab.id==='render'?'play':'grid'}/>{tab.label}</button>)}</nav>
      {clipHeader&&<div className="h3-clip-heading" data-clip-id={clipHeader.id}>
        <h1 title={clipHeader.name}>{clipHeader.shot?`${clipHeader.shot.name} · ${clipHeader.name}`:clipHeader.name}</h1>
        <div className="h3-clip-details"><span>{clipHeader.shot?`Frames ${clipHeader.shot.start}–${clipHeader.shot.end-1} · ${((clipHeader.shot.end-clipHeader.shot.start)/clipHeader.fps).toFixed(1)}s`:`${(clipHeader.frames/clipHeader.fps).toFixed(1)}s · ${clipHeader.width} × ${clipHeader.height}`} · {clipHeader.fps} fps</span><span className="h3-clip-status" data-status={clipHeader.status}>{clipHeader.status==='kept'?'Kept':clipHeader.status==='ready'?'Review ready':'In progress'}</span></div>
      </div>}
    </div><div className="h3-legacy" ref={root}/></section>;
}
function Workbench(){
  const resources=useResources(controller),view=useRef<LegacyView|null>(null);
  const setupJob=resources.snapshot?.jobs.find(job=>job.status==='running'||job.status==='queued');
  const latestSetupJob=resources.snapshot?.jobs[0];
  const failedSetupJob=!setupJob&&latestSetupJob?.status==='failed'?latestSetupJob:null;
  const [location,setLocation]=useState<AppLocation>(()=>window.location.hash && window.location.hash!=='#/' ? parseLocation(window.location.hash,definition) : {scope:'home',page:'resources'});
  const [state,setState]=useState<DomainState|null>(null),[error,setError]=useState(''),[instance,setInstance]=useState(''),[setupOpen,setSetupOpen]=useState(false);
  const [queueWorker,setQueueWorker]=useState<string|null>(null);
  const [theme,setTheme]=useState(()=>localStorage.getItem('h3-kit.theme')||'dark');
  const projects=useMemo(()=>state?projectsFor(state):[],[state]);
  const onEditorLocation=useCallback((next:{projectId:string;page:string})=>{
    const page=next.page==='batch'?'review':next.page;
    if(!definition.projectPages.some(p=>p.id===page))return;
    setLocation(previous=>{
      if(previous.scope==='project'&&previous.projectId===next.projectId&&previous.page===page)return previous;
      const updated:AppLocation={scope:'project',projectId:next.projectId,page};window.history.pushState({},'',locationHash(updated));return updated;
    });
  },[]);
  const refresh=useCallback(async()=>{const state=await domain.state();setState(state);setError('');},[]);
  useEffect(()=>{
    const abort=new AbortController();let live=true;
    void domain.health().then(h=>{if(live)setInstance(h.instanceId);}).catch(e=>{if(live)setError(String(e));});
    const poll=async()=>{try{const next=await domain.state(abort.signal);if(live){setState(next);setError('');}}catch(e){if(live)setError(e instanceof Error?e.message:String(e));}};
    void poll();const timer=window.setInterval(()=>{if(!document.hidden)void poll();},5000);
    return()=>{live=false;abort.abort();clearInterval(timer);};
  },[]);
  useEffect(()=>{
    const pointer=()=>{document.documentElement.dataset.inputMode='pointer';};
    const keyboard=(event:KeyboardEvent)=>{if(!event.metaKey&&!event.ctrlKey&&!event.altKey)document.documentElement.dataset.inputMode='keyboard';};
    document.addEventListener('pointerdown',pointer,true);document.addEventListener('keydown',keyboard,true);
    return()=>{document.removeEventListener('pointerdown',pointer,true);document.removeEventListener('keydown',keyboard,true);};
  },[]);
  useEffect(()=>{document.documentElement.dataset.theme=theme;localStorage.setItem('h3-kit.theme',theme);},[theme]);
  const navigate=useCallback(async (next:AppLocation)=>{
    if(view.current){
      if(next.scope==='project'){if(!(await view.current.update({projectId:next.projectId,page:next.page})))return;}
      else if(!(await view.current.canLeave()))return;
    }
    setLocation(next);const value=locationHash(next);if(window.location.hash!==value)window.history.pushState({},'',value);
  },[]);
  useEffect(()=>{const changed=()=>navigate(window.location.hash && window.location.hash!=='#/' ? parseLocation(window.location.hash,definition) : {scope:'home',page:'resources'});window.addEventListener('hashchange',changed);window.addEventListener('popstate',changed);
    return()=>{window.removeEventListener('hashchange',changed);window.removeEventListener('popstate',changed);};},[navigate]);
  const [selectedWorker,setSelectedWorker]=useState('auto');
  const selectWorker=useCallback((id:string)=>{setSelectedWorker(id);if(id!=='auto')controller.select(id);view.current?.selectWorker(id);},[]);
  const workerJobs=(state?.jobs??[]).filter(j=>(selectedWorker==='auto'||j.data.worker===selectedWorker)&&['mask','mask_edit','render','refine_extract','refine_mask','refine_face','backplate'].includes(j.kind));
  const runningJobs=workerJobs.filter(j=>['running','cancelling'].includes(j.state)).length;
  const openWorkerQueue=()=>{if(!selectedWorker)return;if(view.current){view.current.openQueue(selectedWorker);}else setQueueWorker(selectedWorker);};
  return <div className="h3-shell" data-scope={location.scope}>
    <WorkbenchHeader location={location} projects={projects} resources={resources} selectedWorker={selectedWorker} runningJobs={runningJobs} onNavigate={navigate} onSelectWorker={selectWorker} onQueue={openWorkerQueue}/>
    <main id="main" className="h3-main" tabIndex={-1}>
    {error&&<Notice>{error}</Notice>}
    {setupOpen?<WorkerSetup state={resources} controller={controller} api={setup} onContinue={page=>{setSetupOpen(false);navigate({scope:'home',page});}} onBrowse={()=>{setSetupOpen(false);navigate({scope:'home',page:'projects'});}}/>:
      location.scope==='project'?(!instance?<Notice>Opening saved workspace…</Notice>:<Editors projectId={location.projectId} page={location.page} instanceId={instance} view={view} onError={setError} onLocation={onEditorLocation} worker={selectedWorker} onWorker={selectWorker}/>):
      location.page==='projects'?<ProjectsPage projects={projects} loading={!state&&!error} error={error} countLabel={{singular:'clip',plural:'clips'}} onOpen={id=>navigate({scope:'project',projectId:id,page:'shots'})}
        onCreate={async name=>{const result=await domain.create(name);await refresh();navigate({scope:'project',projectId:result.id,page:'shots'});}}
        onRename={async(id,name)=>{const current=projects.find(p=>p.id===id);if(!current)throw new Error('Refresh projects before renaming');await domain.rename(id,name,current.name);await refresh();}}/>:
      location.page==='resources'?<><section className="h3-resource-welcome"><div><h2>Set up your models</h2><p>Install the required models, including both character-swap LoRAs, or scan to reuse existing files. Whisper transcription is optional and can be installed below.</p><details className="h3-storage-help"><summary>Use an existing ComfyUI folder</summary><p>Set <code>H3_MODELS_DIR</code> and include <code>compose.models.yaml</code> when starting Docker. Keep the storage location below at <code>/models</code>, then choose <strong>Scan</strong>.</p></details></div><div className="h3-resource-actions">{failedSetupJob&&<p className="h3-setup-problem" role="alert">{failedSetupJob.kind==='download'?'Model download stopped. Completed files were kept. Choose Install required to retry missing downloads.':'Model setup needs attention. Check the resource job below for details, then run Scan to recheck your files.'}</p>}{setupJob&&<div className="h3-setup-progress" role="status" aria-live="polite"><strong>{setupJob.kind==='download'?'Installing models':'Setting up models'}{setupJob.progress!==null?` · ${Math.round(setupJob.progress*100)}%`:''}</strong><progress aria-label="Model setup progress" max={1} value={setupJob.progress??undefined}/><span>{setupJob.message}</span>{setupJob.totalBytes!==null&&setupJob.totalBytes>0&&<small>{(setupJob.completedBytes/1024**3).toFixed(1)} / {(setupJob.totalBytes/1024**3).toFixed(1)} GiB</small>}</div>}<button className="ak-primary" disabled={!resources.snapshot?.inventory.summary.ready} onClick={()=>navigate({scope:'home',page:'projects'})}>Continue to Projects →</button></div></section><ResourcesPage state={resources} controller={controller}/></>:
      location.page==='shared-library'?<SharedLibraryBrowser adapter={library}/>:
      <section className="ak-settings"><h1>Settings</h1><label>Theme <select value={theme} onChange={e=>setTheme(e.target.value)}><option value="dark">Dark</option><option value="light">Light</option></select></label>
        <p>Manage your generation worker and model storage.</p><button onClick={()=>setSetupOpen(true)}><Icon name="settings"/>Worker setup</button>
        </section>}
    </main>
    {queueWorker&&<Dialog title={`${queueWorker==='auto'?'Local workers':resources.registry?.executors.find(w=>w.id===queueWorker)?.name??queueWorker} · Job queue`} onClose={()=>setQueueWorker(null)}>
      <p className="ak-muted">Jobs for this worker. Open its project to manage processing.</p>
      {(state?.jobs??[]).filter(j=>(queueWorker==='auto'||j.data.worker===queueWorker)).map(j=><div className="h3-home-job" key={j.id}><strong>{j.kind} · {state?.clips.find(c=>c.id===j.clip_id)?.name??j.clip_id}</strong><span>{j.state}</span><p>{j.data.phase}</p><button onClick={()=>{const c=state?.clips.find(c=>c.id===j.clip_id);if(c){setQueueWorker(null);navigate({scope:'project',projectId:c.batch_id,page:'render'});}}}>Open project</button></div>)}
      {!(state?.jobs??[]).some(j=>(queueWorker==='auto'||j.data.worker===queueWorker))&&<p>No jobs for this worker.</p>}
    </Dialog>}
  </div>;
}
const root=document.getElementById('root');if(!root)throw new Error('H3 mount is missing');createRoot(root).render(<Workbench/>);
