import {useEffect,useLayoutEffect,useRef,useState,useSyncExternalStore} from 'react';
import {createPortal} from 'react-dom';
import {Icon} from '@akatz/app-kit/react';
import {dismissNotification,notificationSnapshot,subscribeNotifications} from './notifications.ts';
import './toast.css';

function activeHost(){
  const dialogs=document.querySelectorAll('dialog[open]');
  return dialogs.item(dialogs.length-1)||document.body;
}
/** Follow modal focus scope so the toast stays visible and dismissible across navigation. */
export function ToastViewport(){
  const toast=useSyncExternalStore(subscribeNotifications,notificationSnapshot);
  const element=useRef<HTMLDivElement>(null);
  const [host,setHost]=useState(activeHost);
  useLayoutEffect(()=>{
    if(!toast)return;
    setHost(activeHost());
    const observer=new MutationObserver(()=>setHost(activeHost()));
    observer.observe(document.body,{subtree:true,childList:true,attributes:true,attributeFilter:['open']});
    return()=>observer.disconnect();
  },[toast]);
  // Legacy navigation can detach the portal host before the observer moves it.
  useLayoutEffect(()=>{if(toast&&element.current?.isConnected)element.current.showPopover?.();},[toast,host]);
  useEffect(()=>{
    if(!toast)return;
    const timer=window.setTimeout(dismissNotification,10000);
    return()=>window.clearTimeout(timer);
  },[toast]);
  if(!toast)return null;
  return createPortal(<div key={toast.id} ref={element} id="toast" className="h3-toast" popover="manual" data-tone={toast.tone}>
    <span role={toast.tone==='error'?'alert':'status'}>{toast.message}</span>
    <button type="button" aria-label="Dismiss notification" onClick={dismissNotification}><Icon name="close"/></button>
  </div>,host);
}
