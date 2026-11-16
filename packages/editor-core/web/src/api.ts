import {z} from 'zod';
import {prepareImageUpload} from './image-upload.ts';
import {JsonClient,type ProjectSummary} from '@akatz/app-kit';
import {editorFetch,editorAsset} from './adapter.ts';
export const http=new JsonClient('',editorFetch);
const batchSchema=z.object({id:z.string(),name:z.string(),created:z.number(),clips:z.number().int().nonnegative()});
const clipSchema=z.object({id:z.string(),batch_id:z.string(),name:z.string(),poster:z.string().nullable().optional(),
  result:z.string().nullable().optional(),shots:z.array(z.object({id:z.string()}).passthrough())}).passthrough();
const jobSchema=z.object({id:z.string(),clip_id:z.string().nullable(),state:z.string(),kind:z.string(),data:z.object({worker:z.string().optional(),phase:z.string().optional()}).passthrough()}).passthrough();
export const stateSchema=z.object({batches:z.array(batchSchema),clips:z.array(clipSchema),jobs:z.array(jobSchema),
  queue_paused:z.boolean(),shared_executor:z.boolean(),workers:z.array(z.string()),
  anchor_providers:z.object({selected:z.string(),items:z.array(z.object({id:z.string()}).passthrough())})}).passthrough();
export type DomainState=z.infer<typeof stateSchema>;
export const healthSchema=z.object({appId:z.literal('h3-workbench'),instanceId:z.string().min(1)}).passthrough();
const usageSchema=z.object({clip_id:z.string(),clip_name:z.string(),shot_id:z.string(),shot_name:z.string()});
export const characterSchema=z.object({id:z.string(),name:z.string(),reference:z.string().nullable(),description:z.string(),revision:z.number(),created:z.number(),usage:z.array(usageSchema)});
export type ProjectCharacter=z.infer<typeof characterSchema>;
export type CharacterUsage=z.infer<typeof usageSchema>;
const charactersPath=(projectId:string)=>'/api/batches/'+encodeURIComponent(projectId)+'/characters';
export const characters={
  list:(projectId:string,signal?:AbortSignal)=>http.request(charactersPath(projectId),z.array(characterSchema),signal?{signal}:{}),
  create:(projectId:string,name:string)=>http.request(charactersPath(projectId),characterSchema,{method:'POST',body:JSON.stringify({name})}),
  edit:(projectId:string,c:ProjectCharacter,changes:{name:string;description:string})=>http.request(charactersPath(projectId)+'/'+encodeURIComponent(c.id),characterSchema,{method:'PATCH',body:JSON.stringify({revision:c.revision,changes})}),
  async image(projectId:string,c:ProjectCharacter,file:File){
    const normalized=await prepareImageUpload(file);
    const data=new FormData();data.set('revision',String(c.revision));data.set('file',normalized);
    const response=await editorFetch(charactersPath(projectId)+'/'+encodeURIComponent(c.id)+'/image',{method:'POST',body:data});
    const result=await response.json();if(!response.ok)throw new Error(result.detail||'Reference upload failed');
    return characterSchema.parse(result);
  },
};
export function projectsFor(state:DomainState):ProjectSummary[]{
  return state.batches.map(b=>{
    const clips=state.clips.filter(c=>c.batch_id===b.id),ids=new Set(clips.map(c=>c.id));
    return {id:b.id,name:b.name,generationCount:clips.reduce((n,c)=>n+c.shots.length,0),
      activeCount:state.jobs.filter(j=>j.clip_id&&ids.has(j.clip_id)&&['submitting','queued','waiting','running','cancelling'].includes(j.state)).length,
      updatedAt:new Date(b.created*1000).toISOString(),
      thumbnailUrl:clips[0]?.poster?editorAsset(clips[0].poster,256):null};
  });
}
export const domain={
  state:(signal?:AbortSignal)=>http.request('/api/state',stateSchema,signal?{signal}:{}),
  health:()=>http.request('/api/health',healthSchema),
  create:(name:string)=>http.request('/api/batches',z.object({id:z.string()}),{method:'POST',headers:{'x-akatz-host':'1'},body:JSON.stringify({name})}),
  remove:(id:string)=>http.request('/api/batches/'+encodeURIComponent(id),z.object({id:z.string()}),{method:'DELETE'}),
  rename:(id:string,name:string,expectedName:string)=>http.request('/api/batches/'+encodeURIComponent(id),z.object({id:z.string(),name:z.string()}),
    {method:'PATCH',headers:{'x-akatz-host':'1'},body:JSON.stringify({name,expected_name:expectedName})}),
};
