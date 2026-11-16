import {useCallback,useEffect,useRef,useState,type RefObject} from 'react';
import {Dialog,Icon,Notice} from '@akatz/app-kit/react';
import {characters,type ProjectCharacter,type CharacterUsage} from './api.ts';
import {ProjectSidebarFooter} from './ProjectSidebarFooter.tsx';
import {ProjectNavigation} from './ProjectNavigation.tsx';

export type CharacterListCache=Map<string,ProjectCharacter[]>;
export interface AssetsHandle {canLeave:()=>Promise<boolean>;saveDraft:()=>Promise<void>}
const message=(error:unknown)=>error instanceof Error?error.message:String(error);
import {adapter,editorAsset} from './adapter.ts';
import {restoreCharacterDraft,retainCharacterDraft} from './character-drafts.ts';
const preview=editorAsset;
function usageText(c:ProjectCharacter){
  const shots=c.usage.length,clips=new Set(c.usage.map(u=>u.clip_id)).size;
  return shots?`${shots} shot${shots===1?'':'s'} · ${clips} clip${clips===1?'':'s'}`:'Not used yet';
}

function CharacterEditor({projectId,character,onChange,onBack,onOpenShot,handle}:{projectId:string;character:ProjectCharacter;onChange:(c:ProjectCharacter)=>void;onBack:()=>void;onOpenShot:(use:CharacterUsage)=>void;handle:RefObject<AssetsHandle|null>}){
  const draftKey=`h3-character-draft:${adapter.identity()}:${projectId}:${character.id}`;
  const saved=useRef(character);
  const [initialDraft]=useState(()=>restoreCharacterDraft(localStorage,draftKey,character));
  const draft=useRef(initialDraft);
  const [draftError,setDraftError]=useState('');
  const retain=()=>setDraftError(retainCharacterDraft(localStorage,draftKey,saved.current,draft.current)?'':'Browser draft could not be saved. Keep this page open until the server save finishes.');
  const [form,setForm]=useState(draft.current),[status,setStatus]=useState('Saved'),[error,setError]=useState(''),[uploading,setUploading]=useState(false);
  const fileInput=useRef<HTMLInputElement>(null);
  const [dragging,setDragging]=useState(false);
  const failedUpload=useRef<File|null>(null),[uploadError,setUploadError]=useState('');
  const timer=useRef<ReturnType<typeof setTimeout>|null>(null),pending=useRef<Promise<void>|null>(null),alive=useRef(true);
  const notify=useRef(onChange);notify.current=onChange;
  const dirty=()=>draft.current.name!==saved.current.name||draft.current.description!==saved.current.description;
  const save=useCallback(async()=>{
    if(timer.current)clearTimeout(timer.current);
    while(pending.current)await pending.current;
    const task=(async()=>{
      while(dirty()){
        const changes={...draft.current};
        if(!changes.name.trim())throw new Error('Give this character a name before leaving.');
        setStatus('Saving…');setError('');
        const next=await characters.edit(projectId,saved.current,changes);
        saved.current=next;retain();notify.current(next);
      }
    })();
    pending.current=task;
    try{await task;}finally{pending.current=null;}
    if(alive.current){setStatus(failedUpload.current?'Reference not uploaded':'Saved');setError('');}
  },[projectId,draftKey]);
  const report=useCallback((e:unknown)=>{if(alive.current){setStatus('Not saved');setError(message(e));}},[]);
  useEffect(()=>{
    alive.current=true;
    const guard={
      canLeave:async()=>{try{await save();return true;}catch(e){report(e);return false;}},
      saveDraft:async()=>{
        await save();
        if(failedUpload.current)throw new Error('Retry the failed character reference upload before signing in.');
      },
    };handle.current=guard;
    const beforeUnload=(event:BeforeUnloadEvent)=>{if(dirty()||pending.current){event.preventDefault();event.returnValue='';}};
    window.addEventListener('beforeunload',beforeUnload);
    if(dirty())void save().catch(report);
    return()=>{alive.current=false;if(timer.current)clearTimeout(timer.current);window.removeEventListener('beforeunload',beforeUnload);if(handle.current===guard)handle.current=null;};
  },[handle,save,report]);
  const edit=(key:'name'|'description',value:string)=>{
    draft.current={...draft.current,[key]:value};retain();setForm(draft.current);setStatus('Saving…');
    if(timer.current)clearTimeout(timer.current);
    timer.current=setTimeout(()=>{void save().catch(report);},650);
  };
  const leave=async(action:()=>void)=>{if(await handle.current?.canLeave())action();};
  const upload=async(file:File)=>{
    setUploading(true);setError('');
    try{await save();}catch(e){report(e);setUploading(false);return;}
    failedUpload.current=null;setUploadError('');
    try{
      setStatus('Uploading reference…');
      const task=characters.image(projectId,saved.current,file).then(next=>{saved.current=next;notify.current(next);});
      pending.current=task;
      try{await task;}finally{pending.current=null;}
    }catch(e){failedUpload.current=file;if(alive.current){setUploadError(message(e));setStatus('Reference not uploaded');}}finally{if(alive.current)setUploading(false);}
    // Text stays editable and browser-persisted during upload. Flush it against
    // the image receipt's revision, rather than racing two character writes.
    await save().catch(report);
  };
  return <>
    <div className="h3-assets-heading h3-character-heading"><div><button className="ak-link h3-assets-back" onClick={()=>void leave(onBack)}>← Back to characters</button><h1>{form.name||'New character'}</h1></div><span className="h3-character-save" role="status" data-saved={status==='Saved'}>{status==='Saved'&&<span aria-hidden="true">✓</span>}{status}</span></div>
    {draftError&&<p role="alert">{draftError}</p>}
    {error&&<div className="h3-character-error" role="alert"><p>{error}</p><button onClick={()=>void save().catch(report)}>Retry save</button><button onClick={async()=>{
      if(!window.confirm('Reload this character and discard your unsaved edits?'))return;
      try{const next=(await characters.list(projectId)).find(c=>c.id===character.id);if(!next)throw new Error('Character no longer exists');saved.current=next;draft.current={name:next.name,description:next.description};setForm(draft.current);retain();notify.current(next);setError('');setStatus('Saved');}catch(e){report(e);}
    }}>Reload character</button></div>}
    <div className="h3-character-editor">
      <section className="h3-character-reference" aria-label="Reference image">
        <button type="button" className="h3-character-image h3-character-dropzone" data-dragging={dragging} aria-busy={uploading} disabled={uploading} aria-label={character.reference?'Replace reference image':'Upload reference image'} onClick={()=>fileInput.current?.click()} onDragOver={e=>{e.preventDefault();if(!uploading)setDragging(true);}} onDragLeave={()=>setDragging(false)} onDrop={e=>{e.preventDefault();setDragging(false);const file=e.dataTransfer.files[0];if(file&&!uploading)void upload(file);}}>
          {character.reference?<img src={preview(character.reference,960)} alt={character.name+' reference'}/>:<div className="h3-character-placeholder"><Icon name="upload"/><strong className="h3-character-uploading">{uploading&&<span className="h3-output-spinner" aria-hidden="true"/>}{uploading?'Uploading…':'Add a reference image'}</strong><p>Drop an image here or click to browse.</p></div>}
          {character.reference&&<span className="h3-dropzone-caption h3-character-uploading" role="status">{uploading&&<span className="h3-output-spinner" aria-hidden="true"/>}{uploading?'Uploading…':'Drop or click to replace'}</span>}
        </button>
        <input ref={fileInput} hidden aria-label="Character reference image" type="file" accept="image/*" disabled={uploading} onChange={event=>{const file=event.target.files?.[0];event.target.value='';if(file)void upload(file);}}/>
        {character.reference&&<div className="h3-character-reference-actions"><a href={editorAsset(character.reference)} download><Icon name="download"/>Download</a></div>}
        {uploadError&&<div className="h3-upload-error" role="alert"><p>{uploadError}</p><button disabled={uploading} onClick={()=>{if(failedUpload.current)void upload(failedUpload.current);}}>Retry upload</button></div>}
      </section>
      <section className="h3-character-details"><fieldset><label htmlFor="character-name">Character name</label><input id="character-name" maxLength={120} value={form.name} onChange={e=>edit('name',e.target.value)}/><label htmlFor="character-description">Appearance description</label><textarea id="character-description" rows={6} maxLength={20000} value={form.description} onChange={e=>edit('description',e.target.value)} placeholder="Describe their defining appearance, clothing, and details."/><p className="ak-muted">Shared with every shot using this character. Changes mark affected prompts for review.</p></fieldset>
      <div className="h3-character-usage"><h2>Used in this project <span>{character.usage.length}</span></h2>{character.usage.length>0&&<div className="h3-character-usage-list" role="region" aria-label="Shots using this character" tabIndex={0}>{character.usage.map(use=><button key={use.clip_id+':'+use.shot_id} onClick={()=>void leave(()=>onOpenShot(use))}><Icon name="film"/><span><strong title={use.clip_name}>{use.clip_name}</strong><small title={use.shot_name}>{use.shot_name}</small></span><span aria-hidden="true">→</span></button>)}</div>}{!character.usage.length&&<p className="ak-muted">Add this character to a shot from the Edit workspace.</p>}</div>
      <p className="h3-character-history"><span aria-hidden="true">ⓘ</span>Existing takes keep the references used to render them.</p></section>
    </div>
  </>;
}

export function ProjectAssets({projectId,projectName,onEdit,onOpenShot,handle,cache}:{cache:CharacterListCache;projectId:string;projectName:string;onEdit:()=>void;onOpenShot:(use:CharacterUsage)=>void;handle:RefObject<AssetsHandle|null>}){
  const [items,setItems]=useState<ProjectCharacter[]>(()=>cache.get(projectId)||[]),[loading,setLoading]=useState(true),[hasLoaded,setHasLoaded]=useState(()=>cache.has(projectId)),[error,setError]=useState(''),[query,setQuery]=useState(''),[selected,setSelected]=useState<string|null>(null);
  const [newOpen,setNewOpen]=useState(()=>sessionStorage.getItem(`h3-create-character:${projectId}`)==='1'),[newName,setNewName]=useState(''),[creating,setCreating]=useState(false),[createError,setCreateError]=useState('');
  useEffect(()=>{sessionStorage.removeItem(`h3-create-character:${projectId}`);},[projectId]);
  const latestItems=useRef(items);latestItems.current=items;
  useEffect(()=>{if(hasLoaded)cache.set(projectId,items);},[cache,projectId,items,hasLoaded]);
  const refresh=useCallback(async(signal?:AbortSignal)=>{
    const before=latestItems.current;setLoading(true);
    try{
      const next=await characters.list(projectId,signal);
      if(signal?.aborted)return;
      // A background response must not overwrite edits made while it was pending.
      setItems(current=>current===before?next:current);setHasLoaded(true);setError('');
    }catch(e){if(!signal?.aborted)setError(message(e));}
    finally{if(!signal?.aborted)setLoading(false);}
  },[projectId]);
  useEffect(()=>{const abort=new AbortController();void refresh(abort.signal);return()=>abort.abort();},[refresh]);
  const current=items.find(c=>c.id===selected);
  const visible=items.filter(c=>(c.name+' '+c.description).toLowerCase().includes(query.toLowerCase()));
  return <section className="h3-assets-workspace">
    <aside className="h3-assets-sidebar h3-project-sidebar"><ProjectNavigation name={projectName} mode="assets" onEdit={onEdit} onAssets={()=>{void (async()=>{if(!handle.current||await handle.current.canLeave())setSelected(null);})();}}/><div className="h3-assets-categories"><span className="h3-project-eyebrow">Assets</span><button aria-current="page" onClick={()=>{void (async()=>{if(!handle.current||await handle.current.canLeave())setSelected(null);})();}}><Icon name="image"/>Characters <span>{hasLoaded?items.length:'…'}</span></button></div><ProjectSidebarFooter/></aside>
    <div className="h3-assets-content">
      {error&&<Notice>{error}<button onClick={()=>void refresh()}>Retry</button></Notice>}
      {current?<CharacterEditor key={current.id} projectId={projectId} character={current} onChange={next=>setItems(all=>all.map(c=>c.id===next.id?next:c))} onBack={()=>setSelected(null)} onOpenShot={onOpenShot} handle={handle}/>:<>
        <div className="h3-assets-heading"><div><p className="h3-assets-breadcrumb">{projectName} / Assets</p><h1>Characters</h1></div><button className="ak-primary" disabled={!hasLoaded} onClick={()=>{setNewName('');setCreateError('');setNewOpen(true);}}><Icon name="plus"/>New character</button></div>
        <label className="h3-character-search"><Icon name="search"/><input type="search" placeholder="Search characters" aria-label="Search characters" value={query} onChange={e=>setQuery(e.target.value)}/></label>
        {!hasLoaded&&loading?<div className="h3-character-grid h3-character-skeletons" role="status" aria-label="Loading characters" aria-busy="true">{[0,1,2,3].map(i=><div className="h3-character-card" key={i} aria-hidden="true"><div className="h3-character-card-image"/><div className="h3-character-card-copy"><i/><i/><i/></div></div>)}</div>:visible.length>0?<div className="h3-character-grid">{visible.map(c=><article key={c.id} className="h3-character-card"><button className="h3-character-card-image" onClick={()=>setSelected(c.id)} aria-label={'Edit '+c.name}>{c.reference?<img src={preview(c.reference)} alt={c.name} loading="lazy"/>:<div className="h3-character-placeholder"><Icon name="image"/><span>Add reference image</span></div>}</button><div className="h3-character-card-copy"><h2>{c.name}</h2><p className="h3-character-summary">{c.description||'Add a description of their appearance.'}</p><p className="h3-character-count"><Icon name="film"/>{usageText(c)}</p><button onClick={()=>setSelected(c.id)}><Icon name="pencil"/>Edit character</button></div></article>)}</div>:null}
        {hasLoaded&&!visible.length&&<div className="h3-assets-empty"><Icon name="image"/><h2>{query?'No matching characters':'No characters yet'}</h2><p>{query?'Try another name or description.':'Add a character to get started.'}</p>{!query&&<button className="ak-primary" disabled={!hasLoaded} onClick={()=>{setNewName('');setCreateError('');setNewOpen(true);}}><Icon name="plus"/>New character</button>}</div>}

      </>}
    </div>
    {newOpen&&<Dialog title="New character" onClose={()=>{if(!creating)setNewOpen(false);}}><form className="h3-new-character" onSubmit={async event=>{event.preventDefault();if(creating)return;setCreating(true);setCreateError('');try{const c=await characters.create(projectId,newName.trim());setItems(all=>[...all,c]);setSelected(c.id);setNewOpen(false);}catch(e){setCreateError(message(e));}finally{setCreating(false);}}}><label htmlFor="new-character-name">Character name</label><input id="new-character-name" autoFocus required maxLength={120} value={newName} onChange={e=>setNewName(e.target.value)} disabled={creating}/>{createError&&<p role="alert">{createError}</p>}<div><button type="button" disabled={creating} onClick={()=>setNewOpen(false)}>Cancel</button><button className="ak-primary" disabled={creating||!newName.trim()}>{creating?'Creating…':'Create character'}</button></div></form></Dialog>}
  </section>;
}
