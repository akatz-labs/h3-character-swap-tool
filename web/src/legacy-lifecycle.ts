/** Typed lifecycle around preserved, app-owned editors. */
export interface ClipHeader {id:string;name:string;frames:number;fps:number;width:number;height:number;status:'kept'|'ready'|'in-progress';shot?:{id:string;name:string;start:number;end:number}|null}
export interface LegacySnapshot {projectId: string; clipId: string | null; shotId: string | null; page: string; dirty: boolean}
export interface LegacyOptions {projectId: string; page: string; instanceId: string}
export interface LegacyBindings {
  ready: Promise<void>;
  snapshot(): LegacySnapshot;
  prepareLeave(): Promise<boolean>;
  setLocation(value: Partial<LegacyOptions>): void;
  disposeEditors(): void;
  openQueue(worker: string): void;
  selectWorker(worker: string): void;
}
export function createLegacyScope(root: HTMLElement, instanceId: string) {
  const lifecycle=new AbortController(), timers=new Set<number>(), observers:MutationObserver[]=[];
  let disposed=false;
  const key='h3-kit-'+instanceId+'-';
  const storage={
    getItem(name:string){return globalThis.localStorage.getItem(key+name);},
    setItem(name:string,value:string){globalThis.localStorage.setItem(key+name,value);},
    removeItem(name:string){globalThis.localStorage.removeItem(key+name);},
    get h3batch(){return globalThis.localStorage.getItem(key+'batch') || globalThis.localStorage.getItem('h3batch');},
    set h3batch(v:string|null){if(v)globalThis.localStorage.setItem(key+'batch',v);else globalThis.localStorage.removeItem(key+'batch');},
    get h3clip(){return globalThis.localStorage.getItem(key+'clip') || globalThis.localStorage.getItem('h3clip');},
    set h3clip(v:string|null){if(v)globalThis.localStorage.setItem(key+'clip',v);else globalThis.localStorage.removeItem(key+'clip');},
  };
  const selector=(v:string):string=>v.replaceAll('#main','#h3-content');
  const $=(v:string)=>root.querySelector(selector(v)), $$=(v:string)=>[...root.querySelectorAll(selector(v))];
  const document=new Proxy(globalThis.document,{get(target,property){
    if(property==='body')return root;
    if(property==='querySelector')return $;
    if(property==='querySelectorAll')return (v:string)=>root.querySelectorAll(selector(v));
    if(property==='getElementById')return (id:string)=>root.querySelector('#'+CSS.escape(id==='main'?'h3-content':id));
    if(property==='addEventListener')return (type:string,callback:EventListenerOrEventListenerObject,opts?:boolean|AddEventListenerOptions)=>{
      const supplied=typeof opts==='object'?opts:{capture:!!opts};
      target.addEventListener(type,callback,{...supplied,signal:supplied.signal?AbortSignal.any([supplied.signal,lifecycle.signal]):lifecycle.signal});
    };
    const value:unknown=Reflect.get(target,property);return typeof value==='function'?value.bind(target):value;
  }});
  const setTimeout=(callback:()=>void,ms:number):number=>{
    if(disposed)return 0;
    const timer=window.setTimeout(()=>{timers.delete(timer);if(!disposed)callback();},ms);timers.add(timer);return timer;
  };
  const Watcher=class extends MutationObserver {constructor(callback:MutationCallback){super(callback);observers.push(this);}};
  return {document,localStorage:storage,$,$$,setTimeout,MutationObserver:Watcher,lifecycle,
    stop(){if(disposed)return;disposed=true;lifecycle.abort();timers.forEach(t=>window.clearTimeout(t));observers.forEach(o=>o.disconnect());},
    get disposed(){return disposed;},
  };
}
export function createLegacyController(scope:ReturnType<typeof createLegacyScope>,root:HTMLElement,bindings:LegacyBindings) {
  return {ready:bindings.ready,snapshot:bindings.snapshot,
    openQueue(worker:string){void bindings.ready.then(()=>{if(!scope.disposed)bindings.openQueue(worker);});},
    selectWorker(worker:string){void bindings.ready.then(()=>{if(!scope.disposed)bindings.selectWorker(worker);});},
    async update(next:Partial<LegacyOptions>):Promise<boolean>{
      if(scope.disposed)return false;
      const current=bindings.snapshot();
      const changed=(next.projectId!==undefined&&next.projectId!==current.projectId)||
        (next.page!==undefined&&next.page!==current.page);
      if(changed&&!(await bindings.prepareLeave()))return false;
      if(scope.disposed)return false;
      if(changed)bindings.setLocation(next);
      return true;
    },
    canLeave():Promise<boolean>{return bindings.prepareLeave();},
    dispose():void{if(scope.disposed)return;scope.stop();bindings.disposeEditors();root.querySelectorAll('video').forEach(v=>v.pause());root.replaceChildren();},
  };
}
