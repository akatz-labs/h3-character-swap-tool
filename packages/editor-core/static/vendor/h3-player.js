/* Shared comparison transport, 2026-10-03. Embedded by scripts/upgrade.py. */
const H3Player = (() => {
  // Workbench adaptation: styles live in style.css and are scoped to the modal.
  // Transport/capture/render/seek logic is shared with the report Atlas.
  class Atlas {
    constructor(video,canvas,wipe,timeline,fps=()=>24) {
      Object.assign(this,{video,canvas,wipe,timeline,fps});
      this.disposed=false;this.cleanups=[];
      const listen=(target,name,fn)=>{target.addEventListener(name,fn);this.cleanups.push(()=>target.removeEventListener(name,fn))};
      this.snapshot=document.createElement('canvas');
      this.snapshotContext=this.snapshot.getContext('2d',{alpha:false});
      this.top=document.createElement('canvas');this.top.className='h3-overlay';
      this.top.setAttribute('aria-hidden','true');this.top.draggable=false;
      canvas.parentElement.classList.add('h3-preview-stage');canvas.after(this.top);
      this.baseContext=canvas.getContext('2d');this.topContext=this.top.getContext('2d',{alpha:false});
      this.time=null;this.version=0;this.renderKey='';this.listeners=[];
      this.pending=null;this.busy=false;this.epoch=0;this.waiters=[];this.scrubbing=false;
      this.intent=false;this.playEpoch=0;
      this.stats={captures:0,renders:0,wipes:0,seeks:0,coalesced:0};
      video.preload='auto';
      if(wipe)listen(wipe,'input',()=>this.reveal());
      if(timeline){
        listen(timeline,'pointerdown',()=>{this.scrubbing=true});
        const release=()=>{this.scrubbing=false;if(this.time!==null&&!this.busy)this.emit()};
        listen(window,'pointerup',release);listen(window,'pointercancel',release);
        listen(timeline,'blur',release);
      }
      listen(video,'emptied',()=>this.reset());
      listen(video,'loadeddata',()=>this.capture(video.currentTime,true));
      listen(video,'seeked',()=>this.capture(video.currentTime,true));
      listen(video,'pause',()=>this.capture(video.currentTime,true));
      listen(video,'ended',()=>this.stop());
      // Exactly one frame callback; idle videos do not run a perpetual RAF loop.
      if(video.requestVideoFrameCallback){
        const tick=(_,m)=>{if(this.disposed)return;this.capture(m.mediaTime);this.frameCallback=video.requestVideoFrameCallback(tick)};
        this.frameCallback=video.requestVideoFrameCallback(tick);
      }else{
        let raf=0;
        const tick=()=>{if(video.paused){raf=0;return}this.capture(video.currentTime);raf=requestAnimationFrame(tick)};
        listen(video,'playing',()=>{if(!raf)raf=requestAnimationFrame(tick)});
        listen(video,'pause',()=>{cancelAnimationFrame(raf);raf=0;this.capture(video.currentTime)});
        listen(video,'timeupdate',()=>this.capture(video.currentTime));
      }
      this.cleanups.push(()=>this.top.remove());
      const players=window.h3Players||(window.h3Players=[]);players.push(this);
    }
    dispose(){
      this.disposed=true;this.reset();
      if(this.frameCallback!==undefined)this.video.cancelVideoFrameCallback?.(this.frameCallback);
      this.cleanups.splice(0).forEach(fn=>fn());this.listeners=[];
      window.h3Players=(window.h3Players||[]).filter(p=>p!==this);
      this.video.removeAttribute('src');this.video.load();
    }
    reset(){
      this.stop();
      this.epoch++;this.pending=null;this.time=null;this.renderKey='';
      this.waiters.splice(0).forEach(w=>w.resolve(false));
    }
    onFrame(fn){this.listeners.push(fn)}
    emit(){this.listeners.forEach(fn=>fn(this.time))}
    capture(t,force=false){
      const v=this.video;if(this.disposed||v.readyState<2||!v.videoWidth||v.seeking)return;
      const max=this.timeline?.hasAttribute('max')?Number(this.timeline.max):Infinity;
      const f=Math.max(0,Math.min(max,Math.floor(t*this.fps()+.01)));
      if(!force&&this.time!==null&&Math.floor(this.time*this.fps()+.01)===f)return;
      this.frameWidth=v.videoWidth;this.frameHeight=v.videoHeight;this.frameSource=v;
      // During playback crop only the two selected columns. Copying a whole
      // nine-column atlas every frame wastes bandwidth and can drop frames.
      // Keep a full snapshot when paused so changing columns/dragging while a
      // seek or media load is pending never samples an unpresented frame.
      if(v.paused){
        if(this.snapshot.width!==v.videoWidth||this.snapshot.height!==v.videoHeight){
          this.snapshot.width=v.videoWidth;this.snapshot.height=v.videoHeight;
        }
        this.snapshotContext.drawImage(v,0,0);this.frameSource=this.snapshot;
      }
      this.time=f/this.fps();this.version++;
      this.stats.captures++;this.emit();
    }
    reveal(){
      const percent=Math.max(0,Math.min(100,+this.wipe?.value||0));
      this.top.style.clipPath=`inset(0 0 0 ${percent}%)`;this.stats.wipes++;
    }
    render(columns,left=0,right=1,enabled=true){
      this.top.hidden=!enabled||!this.stats.captures||this.canvas.hidden;
      if(!enabled||this.time===null){this.reveal();return null}
      const w=Math.round(this.frameWidth/columns),h=this.frameHeight;
      if(!w||!h)return null;
      const key=[this.version,columns,left,right,w,h].join(':');
      if(key!==this.renderKey){
        if(this.canvas.width!==w||this.canvas.height!==h){this.canvas.width=w;this.canvas.height=h}
        if(this.top.width!==w||this.top.height!==h){this.top.width=w;this.top.height=h}
        // Both views are copied together from one decoder's presented frame.
        // On pause/seek the source is the frozen snapshot; wipe uses CSS only.
        this.baseContext.drawImage(this.frameSource,left*w,0,w,h,0,0,w,h);
        this.topContext.drawImage(this.frameSource,right*w,0,w,h,0,0,w,h);
        this.canvas.parentElement.style.setProperty('--ratio',w/h);
        this.renderKey=key;this.stats.renders++;
      }
      this.reveal();return this.time;
    }
    seek(frame,max=Infinity){
      const target=Math.max(0,Math.min(max,Math.round(frame)));
      if(this.pending!==null)this.stats.coalesced++;
      this.pending=target;
      const promise=new Promise((resolve,reject)=>this.waiters.push({resolve,reject}));
      this.drain();return promise;
    }
    async drain(){
      if(this.busy)return;this.busy=true;const epoch=this.epoch;
      try{
        while(this.pending!==null&&epoch===this.epoch){
          const frame=this.pending;this.pending=null;const v=this.video;
          const t=Math.min((frame+.02)/this.fps(),Number.isFinite(v.duration)?Math.max(0,v.duration-.001):Infinity);
          if(v.readyState<2)await this.event('loadeddata',epoch);
          if(epoch!==this.epoch)break;
          if(v.seeking)await this.event('seeked',epoch);
          if(Math.abs(v.currentTime-t)>1e-5){
            const decoded=this.event('seeked',epoch);this.stats.seeks++;v.currentTime=t;await decoded;
          }
          if(epoch===this.epoch)this.capture(v.currentTime,true);
        }
        if(epoch===this.epoch)this.waiters.splice(0).forEach(w=>w.resolve(true));
      }catch(e){if(epoch===this.epoch){this.pending=null;this.waiters.splice(0).forEach(w=>w.reject(e))}}
      finally{this.busy=false;if(this.pending!==null)this.drain()}
    }
    event(name,epoch){
      return new Promise((resolve,reject)=>{
        const v=this.video,cleanup=()=>{clearTimeout(timer);v.removeEventListener(name,ok);v.removeEventListener('error',bad);v.removeEventListener('emptied',cancel)};
        const ok=()=>{cleanup();resolve()},cancel=()=>{cleanup();resolve()},bad=()=>{cleanup();reject(Error('Preview could not decode. Choose it again or download the master.'))};
        const timer=setTimeout(()=>{cleanup();reject(Error('Preview is still loading. Try Play again.'))},15000);
        v.addEventListener(name,ok,{once:true});v.addEventListener('error',bad,{once:true});v.addEventListener('emptied',cancel,{once:true});
        if(epoch!==this.epoch)cancel();
      });
    }
    async settled(){if(this.busy||this.pending!==null)await new Promise((resolve,reject)=>this.waiters.push({resolve,reject}))}
    stop(){this.intent=false;this.playEpoch++;this.video.pause()}
    async start(restartFrame=null){
      this.intent=true;const token=++this.playEpoch;
      try{
        if(restartFrame!==null)await this.seek(restartFrame);
        await this.settled();if(token!==this.playEpoch)return false;
        await this.video.play();if(token!==this.playEpoch){this.video.pause();return false}return true;
      }
      catch(e){if(token===this.playEpoch)this.intent=false;throw e}
    }
    debug(){return {version:'20261003-1',time:this.time,leftFrame:this.time===null?null:Math.round(this.time*this.fps()),rightFrame:this.time===null?null:Math.round(this.time*this.fps()),seeking:this.busy,pending:this.pending,stats:{...this.stats},quality:this.video.getVideoPlaybackQuality?.()}}
  }
  const nativeSeeks=new WeakMap();
  function nativeSeek(v,time){
    let state=nativeSeeks.get(v);
    if(!state){state={busy:false,target:null,waiters:[]};nativeSeeks.set(v,state)}
    state.target=time;
    const promise=new Promise(resolve=>state.waiters.push(resolve));
    if(state.busy)return promise;
    state.busy=true;
    const end=ok=>{state.busy=false;state.target=null;state.waiters.splice(0).forEach(resolve=>resolve(ok))};
    const step=()=>{
      if(state.target===null){end(true);return}
      if(!Number.isFinite(v.duration)){
        const cleanup=()=>{clearTimeout(timer);v.removeEventListener('loadeddata',ready);v.removeEventListener('error',cancel);v.removeEventListener('emptied',cancel)};
        const ready=()=>{cleanup();step()},cancel=()=>{cleanup();end(false)};
        const timer=setTimeout(cancel,15000);
        v.addEventListener('loadeddata',ready,{once:true});v.addEventListener('error',cancel,{once:true});v.addEventListener('emptied',cancel,{once:true});return;
      }
      const target=Math.max(0,Math.min(v.duration-.001,state.target));state.target=null;
      if(Math.abs(v.currentTime-target)<.00001&&!v.seeking){step();return}
      const cleanup=()=>{clearTimeout(timer);v.removeEventListener('seeked',finish);v.removeEventListener('emptied',cancel);v.removeEventListener('error',cancel)};
      const finish=()=>{cleanup();step()},cancel=()=>{cleanup();end(false)};
      const timer=setTimeout(cancel,15000);
      v.addEventListener('seeked',finish,{once:true});v.addEventListener('emptied',cancel,{once:true});v.addEventListener('error',cancel,{once:true});
      if(Math.abs(v.currentTime-target)>=.00001)v.currentTime=target;
    };step();return promise;
  }
  return {Atlas,nativeSeek};
})();
