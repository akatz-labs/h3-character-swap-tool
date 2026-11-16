import {useRef,useState} from 'react';
import {Dialog,Icon} from '@akatz/app-kit/react';
import {useProjectWorkspace} from './ProjectWorkspace.tsx';

export function ProjectNavigation({name,mode,onEdit,onAssets}:{name:string;mode:'edit'|'assets';onEdit:()=>void;onAssets:()=>void}){
  const workspace=useProjectWorkspace(),menu=useRef<HTMLDetailsElement>(null);
  const [editing,setEditing]=useState<{id:string;name:string}|null>(null),[title,setTitle]=useState(''),[error,setError]=useState(''),[busy,setBusy]=useState(false),[confirmDelete,setConfirmDelete]=useState(false);
  const close=()=>{if(menu.current)menu.current.open=false;};
  const edit=(project:{id:string;name:string})=>{close();setEditing(project);setTitle(project.name);setError('');setConfirmDelete(false);};
  const apply=async(remove=false)=>{
    if(!workspace||!editing||busy)return;
    setBusy(true);setError('');
    try{if(remove)await workspace.onDelete(editing.id);else await workspace.onRename(editing.id,title.trim(),editing.name);setEditing(null);}
    catch(e){setError(e instanceof Error?e.message:String(e));}finally{setBusy(false);}
  };
  return <div className="h3-project-navigation">
    <span className="h3-project-eyebrow">Project</span>
    {workspace?<details className="h3-project-dropdown" ref={menu} onKeyDown={e=>{if(e.key==='Escape'){e.preventDefault();close();menu.current?.querySelector('summary')?.focus();}}} onBlur={e=>{if(!e.currentTarget.contains(e.relatedTarget))close();}}>
      <summary aria-label="Current project" aria-disabled={workspace.loading}>{name||(workspace.loading?'Loading projects…':'Choose a project')}<span aria-hidden="true">⌄</span></summary>
      <div className="h3-project-options" aria-label="Projects">
        {workspace.projects.map(p=><div className="h3-project-option" key={p.id}><button aria-current={p.id===workspace.selected?'true':undefined} onClick={()=>{close();workspace.onSelect(p.id);}}>{p.name}</button><button className="h3-project-edit" aria-label={'Edit project '+p.name} title={'Edit '+p.name} onClick={()=>edit(p)}><Icon name="pencil"/></button></div>)}
        <button className="h3-project-new" onClick={()=>{close();workspace.onNew();}}><Icon name="plus"/>New project</button>
      </div>
    </details>:<h2 title={name}>{name}</h2>}
    <nav className="h3-project-modes" aria-label="Project workspace">
      <button aria-current={mode==='edit'?'page':undefined} disabled={workspace?workspace.loading||!workspace.selected:false} onClick={onEdit}><Icon name="film"/>Shots</button>
      <button aria-current={mode==='assets'?'page':undefined} disabled={workspace?workspace.loading||!workspace.selected:false} onClick={onAssets}><Icon name="image"/>Assets</button>
    </nav>
    {editing&&<Dialog title="Edit project" onClose={()=>{if(!busy)setEditing(null);}}><form className="h3-project-edit-form" onSubmit={e=>{e.preventDefault();void apply();}}>
      <label htmlFor="project-edit-name">Project name</label><input id="project-edit-name" autoFocus maxLength={160} required value={title} disabled={busy} onChange={e=>setTitle(e.target.value)}/>
      {error&&<p role="alert">{error}</p>}
      {confirmDelete?<section role="alert"><strong>Delete {editing.name}?</strong><p>This permanently removes its shots, character assets, guides and outputs. This cannot be undone.</p><div className="h3-project-form-actions"><button type="button" disabled={busy} onClick={()=>setConfirmDelete(false)}>Keep project</button><button type="button" className="danger" disabled={busy} onClick={()=>void apply(true)}>{busy?'Deleting…':'Delete project permanently'}</button></div></section>:<div className="h3-project-form-actions"><button type="button" className="danger" disabled={busy} onClick={()=>setConfirmDelete(true)}>Delete project…</button><button type="button" disabled={busy} onClick={()=>setEditing(null)}>Cancel</button><button className="ak-primary" disabled={busy||!title.trim()}>{busy?'Saving…':'Save'}</button></div>}
    </form></Dialog>}
  </div>;
}
