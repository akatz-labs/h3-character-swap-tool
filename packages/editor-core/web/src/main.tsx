import {ToastViewport} from './ToastViewport.tsx';
import {reportError,isExpectedCancellation} from './notifications.ts';
import {useCallback,useEffect,useLayoutEffect,useMemo,useRef,useState,useSyncExternalStore} from 'react';
import {createPortal} from 'react-dom';
import {parseLocation,locationHash,type AppDefinition,type AppLocation} from '@akatz/app-kit';
import {Dialog,Icon,Notice} from '@akatz/app-kit/react';
import '@akatz/app-kit/styles.css';
import '../generated/legacy-scoped.css';
import './theme.css';
import './workspace.css';
import '../../static/guides.css';
import '../../static/select.css';
import './fixed-editor.css';
import {legacyTemplate} from '../generated/legacy-template.ts';
import {mountLegacy,type LegacyView} from '../generated/legacy-adapter.js';
import {domain,projectsFor,type DomainState} from './api.ts';
import {connected,selectEditorProject} from './adapter.ts';
import {adapter,registerDraftSave} from './adapter.ts';
import type {ClipHeader} from './legacy-lifecycle.ts';
import {useOutputNotifications} from './useOutputNotifications.ts';
import {ProjectSidebarFooter} from './ProjectSidebarFooter.tsx';
import {ProjectNavigation} from './ProjectNavigation.tsx';
import {ProjectWorkspaceContext,WorkspacePlaceholder,ShotListSkeleton} from './ProjectWorkspace.tsx';
import {ProjectAssets,type AssetsHandle,type CharacterListCache} from './ProjectAssets.tsx';
import './project-assets.css';
import './workspace-tabs.css';
import '../../static/ui-components.css';
import './outputs.css';
import './project-sidebar.css';
import {WorkspaceTabs} from './WorkspaceTabs.tsx';

const definition:AppDefinition={id:'h3-workbench',title:'H3 Character Swap',sharedLibrary:false,
  projectPages:[{id:'shots',label:'Shots',step:1},{id:'anchors',label:'Anchors',step:2},{id:'render',label:'Render',step:3},{id:'review',label:'Review',step:5},{id:'assets',label:'Assets'}]};
function Editors({projectId,projectName,page,instanceId,view,onError,onLocation,worker,onWorker,onAssets,initialState,onState}:{initialState:DomainState;onState:(value:DomainState)=>void;projectId:string;projectName:string;page:string;instanceId:string;view:React.RefObject<LegacyView|null>;onError:(value:unknown)=>void;onLocation:(value:{projectId:string;page:string})=>void;worker:string|null;onWorker:(worker:string)=>void;onAssets:()=>void}){
  const root=useRef<HTMLDivElement>(null),[clipsOpen,setClipsOpen]=useState(false),[clipHeader,setClipHeader]=useState<ClipHeader|null>(null);
  const [shotTab,setShotTab]=useState({id:'',value:'edit'});
  const [saveStatus,setSaveStatus]=useState<{clipId:string;shotId:string;message:string;failed:boolean}|null>(null);
  useEffect(()=>{
    const status=(event:Event)=>setSaveStatus((event as CustomEvent).detail);
    const output=(event:Event)=>setShotTab({id:(event as CustomEvent).detail.shotId,value:(event as CustomEvent).detail.view});
    window.addEventListener('h3-save-status',status);window.addEventListener('h3-shot-view',output);
    return()=>{window.removeEventListener('h3-save-status',status);window.removeEventListener('h3-shot-view',output);};
  },[]);
  const shotView=shotTab.id===clipHeader?.shot?.id?shotTab.value:'edit';
  const unreadOutputs=useOutputNotifications(instanceId,clipHeader,shotView==='outputs');
  const [footerHost,setFooterHost]=useState<HTMLElement|null>(null);
  const [navigationHost,setNavigationHost]=useState<HTMLElement|null>(null);
  useLayoutEffect(()=>{
    const element=root.current;if(!element)return;
    element.innerHTML=legacyTemplate;
    setNavigationHost(element.querySelector<HTMLElement>('#h3-project-navigation'));
    setFooterHost(element.querySelector<HTMLElement>('#h3-project-footer'));
    const mounted=mountLegacy(element,{projectId,page,instanceId,onLocation,onClip:setClipHeader,onWorker,initialState,onState});view.current=mounted;
    const beforeUnload=(event:BeforeUnloadEvent)=>{if(mounted.snapshot().dirty){event.preventDefault();event.returnValue='';}};
    window.addEventListener('beforeunload',beforeUnload);
    void mounted.ready.catch(onError);
    return()=>{window.removeEventListener('beforeunload',beforeUnload);mounted.dispose();if(view.current===mounted)view.current=null;};
    // Domain polling updates editors in place; navigation is a separate lifecycle operation.
  },[instanceId,view,onError,onLocation,onWorker]);
  useEffect(()=>{view.current?.update({projectId,page});},[projectId,page,view]);
  useEffect(()=>{void view.current?.ready.then(()=>{if(worker)view.current?.selectWorker(worker);}).catch(onError);},[worker,instanceId,view,onError]);
  return <section className="h3-editor-workspace" data-clips-open={clipsOpen} data-shot-view={clipHeader?.shot?shotView:undefined}>
    {footerHost&&createPortal(<ProjectSidebarFooter/>,footerHost)}
    {navigationHost&&createPortal(<ProjectNavigation name={projectName} mode="edit" onEdit={()=>{}} onAssets={onAssets}/>,navigationHost)}
    <div className="h3-project-tools" data-shot={!!clipHeader?.shot}>
      <button className="h3-clips-toggle" aria-expanded={clipsOpen} aria-controls="h3-clip-browser" onClick={()=>setClipsOpen(!clipsOpen)}><Icon name="film"/>Shots</button>
      {clipHeader?.shot&&<div className="h3-shot-navigation"><WorkspaceTabs label="Shot workspace" value={shotView} items={[{id:'edit',label:'Edit',icon:'pencil'},{id:'outputs',label:'Outputs',icon:'grid',indicator:(unreadOutputs||clipHeader.shot.generating)&&<span className="h3-output-badge" role="status" aria-label={unreadOutputs?(clipHeader.shot.generating?'New outputs, generation in progress':'New outputs'):'Generation in progress'}><span className={unreadOutputs?'h3-output-dot':'h3-output-spinner'} aria-hidden="true"/></span>}]} onSelect={tab=>{setShotTab({id:clipHeader.shot!.id,value:tab});root.current?.querySelectorAll('video').forEach(video=>video.pause());}}/></div>}
      {clipHeader&&<div className="h3-clip-heading" data-clip-id={clipHeader.id}>
        <h1 title={clipHeader.name}>{clipHeader.shot?clipHeader.shot.name:clipHeader.name}</h1>
        <div className="h3-clip-details">{saveStatus?.clipId===clipHeader.id&&saveStatus.shotId===clipHeader.shot?.id&&<span className="h3-save-indicator" title={saveStatus.message} data-failed={saveStatus.failed} role="status">{saveStatus.message}</span>}<span>{clipHeader.shot?`Frames ${clipHeader.shot.start}–${clipHeader.shot.end-1} · ${((clipHeader.shot.end-clipHeader.shot.start)/clipHeader.fps).toFixed(1)}s`:`${(clipHeader.frames/clipHeader.fps).toFixed(1)}s · ${clipHeader.width} × ${clipHeader.height}`} · {clipHeader.fps} fps</span><span className="h3-clip-status" data-status={clipHeader.status}>{clipHeader.status==='kept'?'Kept':clipHeader.status==='ready'?'Review ready':'In progress'}</span></div>
      </div>}
    </div><div className="h3-legacy" ref={root}/></section>;
}
export function Workbench(){
  const platform=adapter;
  const observedJobs=useRef<string|null>(null);
  const view=useRef<LegacyView|null>(null);
  const characterLists=useRef<CharacterListCache>(new Map());
  const assets=useRef<AssetsHandle|null>(null),editorPages=useRef(new Map<string,string>());
  const [location,setLocation]=useState<AppLocation>(()=>window.location.hash && window.location.hash!=='#/' ? parseLocation(window.location.hash,definition) : {scope:'home',page:'projects'});
  const navigationRequest=useRef(0);
  const currentLocation=useRef(location);currentLocation.current=location;
  const [state,setState]=useState<DomainState|null>(null),[instance,setInstance]=useState('');
  const [queueWorker,setQueueWorker]=useState<string|null>(null);
  const [newProjectOpen,setNewProjectOpen]=useState(false),[newProjectName,setNewProjectName]=useState(''),[creatingProject,setCreatingProject]=useState(false),[createError,setCreateError]=useState('');
  const projectNameInput=useRef<HTMLInputElement>(null);
  const rememberedProjectKey='h3-current-project:'+platform.identity();
  const [theme]=useState(()=>localStorage.getItem('h3-kit.theme')||'dark');
  useEffect(()=>registerDraftSave(async()=>{
    await assets.current?.saveDraft();
    await view.current?.saveDraft();
  }),[]);
  const projects=useMemo(()=>state?projectsFor(state):[],[state]);
  const onEditorLocation=useCallback((next:{projectId:string;page:string})=>{
    const page=next.page==='batch'?'review':next.page;
    if(!definition.projectPages.some(p=>p.id===page))return;
    editorPages.current.set(next.projectId,page);
    setLocation(previous=>{
      if(previous.scope==='project'&&previous.projectId===next.projectId&&previous.page===page)return previous;
      const updated:AppLocation={scope:'project',projectId:next.projectId,page};window.history.pushState({},'',locationHash(updated));return updated;
    });
  },[]);
  const acceptState=useCallback((next:DomainState)=>{
    const jobs=JSON.stringify(next.jobs.map(j=>[j.id,j.state]).sort((a,b)=>a[0]!.localeCompare(b[0]!)));
    if(observedJobs.current!==null&&observedJobs.current!==jobs)platform.jobsChanged();
    observedJobs.current=jobs;setState(next);
  },[platform]);
  const refresh=useCallback(async()=>{const next=await domain.state();acceptState(next);return next;},[acceptState]);
  useEffect(()=>{
    const abort=new AbortController();let live=true,pending=false;
    {
      selectEditorProject(location.scope==='project'?location.projectId:localStorage.getItem(rememberedProjectKey)||undefined);
    }
    void adapter.health().then(h=>{if(live)setInstance(h.instanceId);}).catch(e=>{if(live)reportError(e);});
    const poll=async()=>{
      if(pending||view.current)return;pending=true;
      try{const next=await domain.state(abort.signal);if(live){
        acceptState(next);
      }}
      catch(e){if(live)reportError(e);}
      finally{pending=false;}
    };
    void poll();const timer=window.setInterval(()=>{if(!document.hidden)void poll();},!connected?5000:30000);
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
    const request=++navigationRequest.current;
    const previous=currentLocation.current;
    const rejected=()=>window.history.replaceState({},'',locationHash(currentLocation.current));
    if(assets.current&&!(await assets.current.canLeave())){rejected();return;}
    if(view.current){
      if(next.scope==='project'&&next.page!=='assets'&&view.current.snapshot().projectId===next.projectId){if(!(await view.current.update({projectId:next.projectId,page:next.page}))){rejected();return;}}
      else if(!(await view.current.canLeave())){rejected();return;}
    }
    if(next.scope==='project'){
      const changed=selectEditorProject(next.projectId);
      try{
        if(changed){const nextState=await domain.state();if(request!==navigationRequest.current)return;acceptState(nextState);}
      }catch(e){
        if(request!==navigationRequest.current||isExpectedCancellation(e))return;
        selectEditorProject(previous.scope==='project'?previous.projectId:undefined);
        reportError(e);return;
      }
    }
    if(request!==navigationRequest.current)return;
    setLocation(next);const value=locationHash(next);if(window.location.hash!==value)window.history.pushState({},'',value);
  },[]);
  useEffect(()=>{const changed=()=>navigate(window.location.hash && window.location.hash!=='#/' ? parseLocation(window.location.hash,definition) : {scope:'home',page:'projects'});window.addEventListener('hashchange',changed);window.addEventListener('popstate',changed);
    return()=>{window.removeEventListener('hashchange',changed);window.removeEventListener('popstate',changed);};},[navigate]);
  const execution=useSyncExternalStore(adapter.subscribeExecution,adapter.execution);
  const selectedWorker=execution.worker;
  const selectWorker=useCallback((id:string)=>{view.current?.selectWorker(id);},[]);
  const openWorkerQueue=()=>{if(!selectedWorker)return;if(view.current){view.current.openQueue(selectedWorker);}else setQueueWorker(selectedWorker);};
  useEffect(()=>{
    if(!state)return;
    if(location.scope==='project'){
      if(projects.some(p=>p.id===location.projectId))localStorage.setItem(rememberedProjectKey,location.projectId);return;
    }
    const selected=projects.find(p=>p.id===localStorage.getItem(rememberedProjectKey))||projects[0];
    if(selected)void navigate({scope:'project',projectId:selected.id,page:'shots'});
  },[state,projects,location,navigate,rememberedProjectKey]);
  const projectId=location.scope==='project'?location.projectId:'';
  const projectClipIds=new Set(state?.clips.filter(c=>c.batch_id===projectId).map(c=>c.id));
  const projectJobs=(state?.jobs??[]).filter(j=>j.clip_id&&projectClipIds.has(j.clip_id));
  const openNewProject=()=>{setNewProjectName('');setCreateError('');setNewProjectOpen(true);};
  const createProject=async()=>{
    if(creatingProject||!newProjectName.trim())return;
    setCreatingProject(true);setCreateError('');
    try{
      if(assets.current&&!(await assets.current.canLeave()))throw Error('Save your character changes first.');
      if(view.current&&!(await view.current.canLeave()))throw Error('Finish your current edits before creating a project.');
      const result=await domain.create(newProjectName.trim());
      // Leave immediately after successful creation. Do not offer a duplicate create
      // if the subsequent metadata refresh fails.
      setNewProjectOpen(false);
      const next:AppLocation={scope:'project',projectId:result.id,page:'shots'};
      setLocation(next);window.history.replaceState({},'',locationHash(next));
      selectEditorProject(result.id);
      await refresh();
    }catch(e){setCreateError(e instanceof Error?e.message:String(e));reportError(e);}
    finally{setCreatingProject(false);}
  };
  const prepareProjectEdit=async()=>{
    if(assets.current&&!(await assets.current.canLeave()))throw Error('Your character changes could not be saved. Retry before editing the project.');
    if(view.current&&!(await view.current.canLeave()))throw Error('Your shot changes could not be saved. Retry before editing the project.');
  };
  const renameProject=async(id:string,name:string,expected:string)=>{await prepareProjectEdit();await domain.rename(id,name,expected);await refresh();};
  const deleteProject=async(id:string)=>{
    await prepareProjectEdit();await domain.remove(id);characterLists.current.delete(id);
    if(id===projectId){selectEditorProject(undefined);const next:AppLocation={scope:'home',page:'projects'};setLocation(next);window.history.replaceState({},'',locationHash(next));localStorage.removeItem(rememberedProjectKey);}
    await refresh();
  };
  const workspace={onRename:renameProject,onDelete:deleteProject,projects,selected:projectId,loading:!state,onSelect:(id:string)=>{void navigate({scope:'project',projectId:id,page:'shots'});},onNew:openNewProject,onQueue:openWorkerQueue,activeJobs:projectJobs.filter(j=>['queued','waiting','running','submitting','cancelling'].includes(j.state)).length};
  const placeholder=<WorkspacePlaceholder sidebar={<><ProjectNavigation name={state?'':'Loading projects…'} mode="edit" onEdit={()=>{}} onAssets={()=>{}}/>{!state?<ShotListSkeleton/>:<div className="h3-empty-shot-list">No shots yet</div>}<ProjectSidebarFooter/></>}>
    {!state||!instance||projects.length>0?<div className="h3-workspace-loading" role="status"><span className="h3-ui-busy" aria-hidden="true"/>Loading workspace…</div>:<div><h1>Create your first project</h1><button className="ak-primary" onClick={openNewProject}><Icon name="plus"/>New project</button></div>}
  </WorkspacePlaceholder>;
  return <ProjectWorkspaceContext.Provider value={workspace}><div className="h3-shell" data-scope={location.scope}>
    <main id="main" className="h3-main" tabIndex={-1}>
    <ToastViewport/>
    {(!state||!instance||!projects.length||location.scope!=='project')?placeholder:
      location.scope==='project'?(!instance||!state?<Notice>Opening saved workspace…</Notice>:!projects.some(p=>p.id===location.projectId)?<Notice>Project not found. <button onClick={()=>navigate({scope:'home',page:'projects'})}>Back to Projects</button></Notice>:location.page==='assets'?<ProjectAssets cache={characterLists.current} key={location.projectId} projectId={location.projectId} projectName={projects.find(p=>p.id===location.projectId)!.name} handle={assets} onEdit={()=>navigate({scope:'project',projectId:location.projectId,page:editorPages.current.get(location.projectId)||'shots'})} onOpenShot={use=>{
        localStorage.setItem(`h3-kit-${instance}-clip`,use.clip_id);localStorage.setItem(`h3-kit-${instance}-shot`,use.shot_id);
        void navigate({scope:'project',projectId:location.projectId,page:'render'});
      }}/>:<Editors key={location.projectId} initialState={state} onState={acceptState} projectId={location.projectId} projectName={projects.find(p=>p.id===location.projectId)!.name} page={location.page} instanceId={instance} view={view} onError={reportError} onLocation={onEditorLocation} worker={selectedWorker} onWorker={selectWorker} onAssets={()=>navigate({scope:'project',projectId:location.projectId,page:'assets'})}/>):
      placeholder}
    </main>
    {queueWorker&&<Dialog title={'Project queue'} onClose={()=>setQueueWorker(null)}>
      <p className="ak-muted">Jobs in this project.</p>
      {projectJobs.filter(j=>(queueWorker==='auto'||j.data.worker===queueWorker)).map(j=><div className="h3-home-job" key={j.id}><strong>{j.kind} · {state?.clips.find(c=>c.id===j.clip_id)?.name??j.clip_id}</strong><span>{j.state}</span><p>{j.data.phase}</p><button onClick={()=>{const c=state?.clips.find(c=>c.id===j.clip_id);if(c){setQueueWorker(null);navigate({scope:'project',projectId:c.batch_id,page:'render'});}}}>Open project</button></div>)}
      {!projectJobs.some(j=>(queueWorker==='auto'||j.data.worker===queueWorker))&&<p>No jobs for this worker.</p>}
    </Dialog>}
    {newProjectOpen&&<Dialog title="New project" busy={creatingProject} initialFocus={projectNameInput} onClose={()=>setNewProjectOpen(false)}>
      <form className="h3-new-project-form" onSubmit={e=>{e.preventDefault();void createProject();}}>
        <label htmlFor="new-project-name">Project name</label>
        <input id="new-project-name" ref={projectNameInput} maxLength={120} value={newProjectName} onChange={e=>setNewProjectName(e.target.value)} disabled={creatingProject} required/>
        {createError&&<p role="alert">{createError}</p>}
        <div><button type="button" disabled={creatingProject} onClick={()=>setNewProjectOpen(false)}>Cancel</button><button className="ak-primary" type="submit" disabled={creatingProject||!newProjectName.trim()}>{creatingProject?'Creating…':'Create'}</button></div>
      </form>
    </Dialog>}
  </div></ProjectWorkspaceContext.Provider>;
}
