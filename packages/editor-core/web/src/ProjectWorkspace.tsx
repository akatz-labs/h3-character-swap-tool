import {createContext,useContext,type ReactNode} from 'react';

export interface ProjectWorkspaceState {
  projects:readonly {id:string;name:string}[];
  selected:string;
  loading:boolean;
  onSelect:(id:string)=>void;
  onNew:()=>void;
  onRename:(id:string,name:string,expectedName:string)=>Promise<void>;
  onDelete:(id:string)=>Promise<void>;
  onQueue:()=>void;
  activeJobs:number;
}
export const ProjectWorkspaceContext=createContext<ProjectWorkspaceState|null>(null);
export function useProjectWorkspace(){return useContext(ProjectWorkspaceContext);}

/** Same sidebar scaffold while account metadata is pending or the account is empty. */
export function WorkspacePlaceholder({sidebar,children}:{sidebar:ReactNode;children:ReactNode}){
  return <section className="h3-workspace-placeholder"><aside className="h3-project-sidebar">{sidebar}</aside><div className="h3-workspace-placeholder-content">{children}</div></section>;
}
export function ShotListSkeleton(){
  return <div className="h3-shot-skeletons" role="status" aria-label="Loading shots" aria-busy="true">
    <span className="h3-project-eyebrow">Loading shots…</span>
    {[0,1,2].map(i=><div className="h3-shot-skeleton" aria-hidden="true" key={i}><span/><div><i/><i/></div></div>)}
  </div>;
}
