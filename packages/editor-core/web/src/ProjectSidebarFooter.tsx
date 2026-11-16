import {useState} from 'react';
import {Dialog} from '@akatz/app-kit/react';
import {adapter} from './adapter';
import {useProjectWorkspace} from './ProjectWorkspace.tsx';

/** Shared community links and build identity for every project workspace. */
export function ProjectSidebarFooter(){
  const workspace=useProjectWorkspace();
  const [creditsOpen,setCreditsOpen]=useState(false);
  return <div className="h3-project-sidebar-footer">
    {workspace&&<button type="button" onClick={workspace.onQueue} disabled={!workspace.selected||workspace.loading}><svg className="ak-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true"><path d="M8 6h13M8 12h13M8 18h13M3 6h1M3 12h1M3 18h1"/></svg>Project queue{workspace.activeJobs>0&&<span className="h3-queue-activity" role="status" aria-label={`${workspace.activeJobs} active jobs`}><span className="h3-ui-busy" aria-hidden="true"/><span className="h3-queue-count">{workspace.activeJobs}</span></span>}</button>}
    <footer><span>v{adapter.version}</span><button type="button" className="h3-credits-button" onClick={()=>setCreditsOpen(true)}>Credits</button><span>made by <a href="https://github.com/akatz-ai" target="_blank" rel="noopener noreferrer">Akatz</a></span></footer>
    {creditsOpen&&<Dialog title="Credits" onClose={()=>setCreditsOpen(false)}><div className="h3-model-credits">
      <p>Thanks to the teams and creators behind the models that make this tool possible.</p>
      <section><h3>Viggle Remake</h3><p>Character swapping with the Viggle Remake model, built on MiniMax H3.</p><a href="https://huggingface.co/Viggle/Viggle-Remake" target="_blank" rel="noopener noreferrer">View model on Hugging Face ↗</a></section>
    </div></Dialog>}
  </div>;
}
