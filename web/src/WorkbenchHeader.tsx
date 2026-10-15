import type {AppLocation,ProjectSummary,ResourceState} from '@akatz/app-kit';
import {Icon,SelectMenu,WorkerStatusIcon} from '@akatz/app-kit/react';

/** Workbench composition; shared controls retain their keyboard and focus behavior. */
export function WorkbenchHeader({location,projects,resources,selectedWorker,runningJobs,onNavigate,onSelectWorker,onQueue}:{
  location:AppLocation;projects:readonly ProjectSummary[];resources:ResourceState;selectedWorker:string;runningJobs:number;
  onNavigate:(location:AppLocation)=>void;onSelectWorker:(id:string)=>void;onQueue:()=>void;
}){
  const worker=resources.registry?.executors.find(w=>w.id===(selectedWorker==='auto'?resources.workerId:selectedWorker));
  const status=!worker?'Checking':!worker.online?'Offline':resources.error?'Needs attention':!resources.snapshot?'Checking':!worker.ready||!resources.snapshot.inventory.summary.ready?'Needs setup':'Connected';
  const homePages=[['projects','Projects'],['shared-library','Shared Library'],['resources','Resources'],['settings','Settings']] as const;
  return <>
    <a className="ak-skip" href="#main" onClick={event=>{event.preventDefault();document.getElementById('main')?.focus();}}>Skip to content</a>
    <header className="ak-header h3-header">
      <button className="ak-brand" aria-label="H3 Character Swap home" onClick={()=>onNavigate({scope:'home',page:'projects'})}>
        <span className="ak-brand-mark h3-mark" aria-hidden="true">H3</span><span className="ak-brand-copy"><strong>H3 Character Swap</strong><small>Character performance</small></span>
      </button>
      <div className="h3-project-picker">
        <button className="ak-link" onClick={()=>onNavigate({scope:'home',page:'projects'})}><Icon name="folder"/>Projects</button>
        {location.scope==='project'&&<><span aria-hidden="true">/</span><SelectMenu label="Current project" value={location.projectId} placeholder="Choose a project" options={projects.map(p=>({value:p.id,label:p.name}))} onChange={projectId=>onNavigate({scope:'project',projectId,page:'shots'})}/></>}
      </div>
      <div className="ak-worker">
        <button className="h3-worker-queue" onClick={onQueue} disabled={!worker&&selectedWorker!=='auto'} aria-label={`${selectedWorker==='auto'?'All local workers':worker?.name||'Worker'} job queue${runningJobs?`, ${runningJobs} running`:''}`} title={`Open ${selectedWorker==='auto'?'local workers':worker?.name||'worker'} job queue`} aria-haspopup="dialog">
          <WorkerStatusIcon status={status}/>{runningJobs>0&&<span className="h3-job-badge" aria-hidden="true">{runningJobs}</span>}
        </button>
        <SelectMenu className="ak-worker-picker" label="Generation worker" value={selectedWorker} displayValue={selectedWorker==='auto'?'Auto · local':worker?.name??'Select worker'} placeholder="Select worker" disabled={resources.pending||!resources.registry?.executors.length} options={[{value:'auto',label:'Auto · local workers only'},...(resources.registry?.executors??[]).map(w=>({value:w.id,label:`${w.name} · ${!w.online?'Offline':!w.ready?'Needs setup':'Connected'}`}))]} onChange={onSelectWorker}/>
        <SelectMenu className="h3-app-menu" label="Character Swap menu" value="" displayValue="Menu" placeholder="Menu" options={homePages.map(([value,label])=>({value,label}))} onChange={value=>{const item=homePages.find(([page])=>page===value);if(item)onNavigate({scope:'home',page:item[0]});}}/>
      </div>
    </header>
    {location.scope==='home'&&<nav className="h3-home-nav" aria-label="Workspace">{homePages.map(([page,label])=><button key={page} aria-current={location.page===page?'page':undefined} onClick={()=>onNavigate({scope:'home',page})}><Icon name={page==='projects'?'folder':page==='shared-library'?'library':page==='resources'?'boxes':'settings'}/>{label}</button>)}</nav>}
  </>;
}
