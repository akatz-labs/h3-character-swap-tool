/** Native file drops share the same import/upload paths as file pickers. */
export function bindFileDrop(root:HTMLElement, options:{
  kind:'image'|'video'; signal:AbortSignal;
  acceptsTarget?:(target:Element)=>boolean;
  active:(active:boolean)=>void;
  files:(files:File[])=>void;
  invalid:()=>void;
}){
  const extensions=options.kind==='video'?/\.(mp4|mov|mkv|webm|avi)$/i:/\.(png|jpe?g|webp|gif|avif|bmp|tiff?)$/i;
  const targetAllowed=(target:EventTarget|null)=>target instanceof Element&&root.contains(target)&&(!options.acceptsTarget||options.acceptsTarget(target));
  const fileDrag=(transfer:DataTransfer|null)=>!!transfer&&Array.from(transfer.types).includes('Files');
  const matchingDrag=(transfer:DataTransfer)=>Array.from(transfer.items).some(item=>item.kind==='file'&&(!item.type||item.type.startsWith(options.kind+'/')));
  const clear=()=>options.active(false);
  root.addEventListener('dragover',event=>{
    if(!targetAllowed(event.target)||!fileDrag(event.dataTransfer))return;
    // Prevent navigation even for a file of the wrong type.
    event.preventDefault();
    const allowed=matchingDrag(event.dataTransfer!);
    event.dataTransfer!.dropEffect=allowed?'copy':'none';options.active(allowed);
  },{signal:options.signal});
  root.addEventListener('dragleave',event=>{if(!targetAllowed(event.relatedTarget))clear();},{signal:options.signal});
  root.addEventListener('drop',event=>{
    clear();if(!targetAllowed(event.target)||!fileDrag(event.dataTransfer))return;
    event.preventDefault();event.stopPropagation();
    const files=Array.from(event.dataTransfer!.files);
    if(!files.length||files.some(file=>!(file.type.startsWith(options.kind+'/')||(!file.type&&extensions.test(file.name))))){options.invalid();return;}
    options.files(files);
  },{signal:options.signal});
  for(const name of ['drop','dragend','blur'])window.addEventListener(name,clear,{signal:options.signal});
  options.signal.addEventListener('abort',clear,{once:true});
}
