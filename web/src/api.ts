import {z} from 'zod';
import {JsonClient,type ProjectSummary} from '@akatz/app-kit';
export const http=new JsonClient();
const batchSchema=z.object({id:z.string(),name:z.string(),created:z.number(),clips:z.number().int().nonnegative()});
const clipSchema=z.object({id:z.string(),batch_id:z.string(),name:z.string(),poster:z.string().nullable().optional(),
  result:z.string().nullable().optional(),shots:z.array(z.object({id:z.string()}))}).passthrough();
const jobSchema=z.object({id:z.string(),clip_id:z.string().nullable(),state:z.string(),kind:z.string(),data:z.object({worker:z.string().optional(),phase:z.string().optional()}).passthrough()}).passthrough();
export const stateSchema=z.object({batches:z.array(batchSchema),clips:z.array(clipSchema),jobs:z.array(jobSchema),
  queue_paused:z.boolean(),shared_executor:z.boolean(),workers:z.array(z.string()),
  anchor_providers:z.object({selected:z.string(),items:z.array(z.object({id:z.string()}).passthrough())})});
export type DomainState=z.infer<typeof stateSchema>;
export const healthSchema=z.object({appId:z.literal('h3-workbench'),instanceId:z.string().min(1)}).passthrough();
export function projectsFor(state:DomainState):ProjectSummary[]{
  return state.batches.map(b=>{
    const clips=state.clips.filter(c=>c.batch_id===b.id),ids=new Set(clips.map(c=>c.id));
    return {id:b.id,name:b.name,generationCount:clips.length,
      activeCount:state.jobs.filter(j=>j.clip_id&&ids.has(j.clip_id)&&['submitting','queued','waiting','running','cancelling'].includes(j.state)).length,
      updatedAt:new Date(b.created*1000).toISOString(),
      thumbnailUrl:clips[0]?.poster?'/api/assets/'+encodeURIComponent(clips[0].poster)+'/preview?size=256':null};
  });
}
export const domain={
  state:(signal?:AbortSignal)=>http.request('/api/state',stateSchema,signal?{signal}:{}),
  health:()=>http.request('/api/health',healthSchema),
  create:(name:string)=>http.request('/api/batches',z.object({id:z.string()}),{method:'POST',headers:{'x-akatz-host':'1'},body:JSON.stringify({name})}),
  rename:(id:string,name:string,expectedName:string)=>http.request('/api/batches/'+encodeURIComponent(id),z.object({id:z.string(),name:z.string()}),
    {method:'PATCH',headers:{'x-akatz-host':'1'},body:JSON.stringify({name,expected_name:expectedName})}),
};
