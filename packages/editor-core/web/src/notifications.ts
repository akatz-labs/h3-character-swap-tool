/** One notification channel for the shared editor and its legacy bridge. */
export type Toast = {id:number;message:string;tone:'info'|'error'};
let current:Toast|null=null;
let sequence=0;
const listeners=new Set<()=>void>();
export const subscribeNotifications=(listener:()=>void)=>{listeners.add(listener);return()=>{listeners.delete(listener);};};
export const notificationSnapshot=()=>current;
export function dismissNotification(){current=null;listeners.forEach(listener=>listener());}
export function notify(message:string,tone:Toast['tone']='info'){
  if(!message||current?.message===message&&current.tone===tone)return;
  current={id:++sequence,message,tone};listeners.forEach(listener=>listener());
}
export function isExpectedCancellation(error:unknown){return error instanceof Error&&error.name==='AbortError';}
export function reportError(error:unknown){
  if(isExpectedCancellation(error))return;
  notify(error instanceof Error?error.message:String(error),'error');
}
