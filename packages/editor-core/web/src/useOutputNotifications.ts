import {useEffect,useMemo,useState} from 'react';
import type {ClipHeader} from './legacy-lifecycle.ts';

/** Browser-local read receipts, scoped to the app instance and shot. */
export function useOutputNotifications(instanceId:string,header:ClipHeader|null,viewing:boolean){
  const key=header?.shot?`h3-kit-${instanceId}-outputs-seen-${header.id}-${header.shot.id}`:null;
  const outputs=header?.shot?.outputs;
  const seen=useMemo(()=>{
    if(!key)return new Set<string>();
    const saved=localStorage.getItem(key);
    let parsed:unknown;
    try{parsed=saved?JSON.parse(saved):null;}catch{parsed=null;}
    // Existing history is the baseline on the first visit in this browser.
    return new Set<string>(Array.isArray(parsed)&&parsed.every(id=>typeof id==='string')?parsed:outputs);
  },[key]);
  const [unread,setUnread]=useState(false);
  useEffect(()=>{
    if(!key||!outputs){setUnread(false);return;}
    const update=()=>{
      if(viewing&&!document.hidden)outputs.forEach(id=>seen.add(id));
      localStorage.setItem(key,JSON.stringify([...seen].sort()));
      setUnread(outputs.some(id=>!seen.has(id)));
    };
    const changed=(event:StorageEvent)=>{
      if(event.key!==key||!event.newValue)return;
      let parsed:unknown;
      try{parsed=JSON.parse(event.newValue);}catch{return;}
      if(!Array.isArray(parsed)||!parsed.every(id=>typeof id==='string'))return;
      parsed.forEach(id=>seen.add(id));update();
    };
    update();
    document.addEventListener('visibilitychange',update);
    window.addEventListener('storage',changed);
    return()=>{document.removeEventListener('visibilitychange',update);window.removeEventListener('storage',changed);};
  },[key,outputs,seen,viewing]);
  return unread;
}
