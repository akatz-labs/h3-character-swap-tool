"""Asynchronous immutable previews of saved foreground recipes, with source audio."""
import json
import os
import subprocess
import tempfile
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
import numpy as np
from PIL import Image
from . import refinement as ref, previews, media, masks, store
from .config import MEDIA

POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix='refine-preview')
FRAME_WORKERS = len(os.sched_getaffinity(0)) if hasattr(os, 'sched_getaffinity') else (os.cpu_count() or 1)
FRAME_POOL = ThreadPoolExecutor(max_workers=FRAME_WORKERS, thread_name_prefix='refine-frame')
MATTE_LOCKS = [threading.Lock() for _ in range(64)]
PENDING = {}


def matte_key(f, frame, count, dimensions, mask_identity=None):
    # A keyframe edit only invalidates frames whose interpolated polygon changes.
    face = f['face']
    return ref.digest(dict(version=1, alpha=f['alpha'], mask=mask_identity or f['mask'], frame=frame,
                           count=count, dimensions=dimensions, gate=f['gate_px'], core=f['core_px'],
                           face={**face, 'keyframes': ref.polygon_at(face, frame)} if face['enabled'] else None))


def cached_matte(f, frame, count, dimensions, load, mask_identity=None):
    key = matte_key(f, frame, count, dimensions, mask_identity)
    path = MEDIA / 'previews' / 'matte-frames-v1' / key[:2] / (key+'.npz')
    with MATTE_LOCKS[int(key[:4], 16) % len(MATTE_LOCKS)]:
        if path.exists():
            with np.load(path, allow_pickle=False) as z:
                return z['alpha'], True
        raw, sam = load(frame)
        alpha = ref.matte_frame(raw, sam, f, frame)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Lossless native array; never quantize or reduce the matte's resolution.
        with tempfile.NamedTemporaryFile(dir=path.parent, suffix='.npz', delete=False) as tmp:
            pending = tmp.name
            try:
                np.savez_compressed(tmp, alpha=alpha)
                tmp.close()
                os.replace(pending, path)
            finally:
                if os.path.exists(pending): os.unlink(pending)
        return alpha, False


def mask_identities(asset, count, dimensions):
    """One frame correction creates a new asset, but leaves other frames reusable."""
    import hashlib
    key = ref.digest([asset, count, dimensions])
    path = MEDIA / 'previews' / 'mask-identities-v1' / (key+'.json')
    with previews.lock_for('mask-identities:'+key):
        if path.exists(): return json.loads(path.read_text()), None
        sam = masks.read(asset, count, dimensions)
        identities = [{'sha256':hashlib.sha256(np.packbits(frame).tobytes()).hexdigest()} for frame in sam]
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path.parent, mode='w', delete=False) as tmp:
            pending = tmp.name
            try:
                json.dump(identities, tmp)
                tmp.close()
                os.replace(pending, path)
            finally:
                if os.path.exists(pending): os.unlink(pending)
        return identities, sam


def ordered_frames(fn, count):
    """Use all available CPU cores without retaining a whole decoded result queue."""
    futures = deque()
    next_frame = 0
    try:
        while next_frame < count or futures:
            while next_frame < count and len(futures) < FRAME_WORKERS * 2:
                futures.append(FRAME_POOL.submit(fn, next_frame))
                next_frame += 1
            yield futures.popleft().result()
    finally:
        for future in futures: future.cancel()


def prepare(c, s, side, view, retry=False):
    if side not in ref.SIDES or view not in {'rgb','alpha','sam','checker','black','white','plate','comparison'}:
        raise ValueError('Unknown foreground view')
    r = ref.draft(c, s)
    ref.validate_view(s, r, side, view)
    fields=[] if view=='rgb' else ['mask','gate_px'] if view=='sam' else ['alpha','mask','gate_px','core_px','face']
    spec = dict(version=2, binding=ref.source_binding(c,s) if side=='source' else r['binding'],
                foreground={k:r[side][k] for k in fields}, side=side,
                view=view, plate=r['plate_id'] if view=='plate' else None,
                audio=c.get('audio',False), source=c['source'])
    key = ref.digest(spec)
    d = MEDIA / 'previews' / ('refine-'+key)
    with previews.LOCK:
        if (d/'ready.json').exists():
            return json.loads((d/'ready.json').read_text())
        if retry and PENDING.get(key,{}).get('state')=='failed':
            PENDING.pop(key)
        if key not in PENDING:
            PENDING[key] = {'state':'preparing', 'phase':'Waiting for preview', 'completed':0, 'total':s['end']-s['start']}
            def build():
                try:
                    def progress(phase, completed=0):
                        with previews.LOCK:
                            PENDING[key].update(phase=phase, completed=completed)
                    result = render(c,s,r,side,view,d,progress)
                    pending=d/'ready.pending.json'
                    pending.write_text(json.dumps(result))
                    pending.replace(d/'ready.json')
                    with previews.LOCK: PENDING.pop(key,None)
                except Exception as error:
                    with previews.LOCK: PENDING[key]={'state':'failed','error':str(error)}
            POOL.submit(build)
        return dict(PENDING[key])


def render(c,s,r,side,view,d,progress=None):
    started=time.monotonic()
    report=progress or (lambda *args: None)
    report('Loading foreground')
    n=s['end']-s['start']; dims=c['dimensions']; w,h=dims
    pw,ph=previews.size(dims); f=r[side]
    # Reserve for uncompressed native caches as well as the viewing encode.
    expected=n*pw*ph*3*(2 if view=='comparison' else 1)
    if view not in {'rgb','sam'}: expected+=n*w*h*8
    from .config import check_storage
    check_storage(max(2,(expected+2**30-1)//2**30))
    d.mkdir(parents=True,exist_ok=True)
    source=r['binding']['source'] if side=='source' else r['binding']['render_asset']
    if not source: raise ValueError('Select a render first')
    start=s['start'] if side=='source' else 0
    rgb=ref.decode(source,start,start+n,dims) if view not in {'alpha','sam'} else None
    sam=masks.read(f['mask'],n,dims) if view=='sam' else None
    identities=None
    if view not in {'rgb','sam'}:
        identities,sam=mask_identities(f['mask'],n,dims)
    raw=None
    input_lock=threading.Lock()
    def load_inputs(frame):
        nonlocal sam,raw
        with input_lock:
            if sam is None: sam=masks.read(f['mask'],n,dims)
            if raw is None: raw=ref.raw_alpha(f['alpha'],n,dims)
        return raw[frame],sam[frame]
    bg=None
    if view=='plate':
        plate=next((p for p in s.get('backplates',[]) if p['id']==r['plate_id']),None)
        if not plate: raise ValueError('Select a backplate first')
        bg=ref.decode(plate['asset'],0,n,dims)
    y,x=np.indices((h,w));checker=np.repeat((80+((x//24+y//24)%2)*80)[...,None],3,axis=2)
    def make_frame(i):
        hit=False
        if view=='rgb': frame=rgb[i]
        elif view=='sam': frame=np.repeat((ref.sam_support(sam[i],f['gate_px'])*255).astype(np.uint8)[...,None],3,axis=2)
        else:
            a,hit=cached_matte(f,i,n,dims,load_inputs,identities[i])
            a=a[...,None]
            if view=='alpha': frame=np.repeat(np.rint(a*255).astype(np.uint8),3,axis=2)
            else:
                back=bg[i] if bg is not None else checker if view in {'checker','comparison'} else 255 if view=='white' else 0
                frame=np.rint(rgb[i]*a+back*(1-a)).astype(np.uint8)
        if view=='comparison':frame=np.concatenate([rgb[i],frame],axis=1)
        return np.asarray(Image.fromarray(frame).resize((pw*(2 if view=='comparison' else 1),ph),Image.Resampling.LANCZOS)).tobytes(),hit
    path=d/'preview.pending.mp4'
    args=['ffmpeg','-v','error','-nostdin','-y','-f','rawvideo','-pix_fmt','rgb24','-s',f'{pw*(2 if view=="comparison" else 1)}x{ph}','-r','24','-i','pipe:0']
    if c.get('audio'):
        args+=['-ss',str(s['start']/24),'-i',str(store.path(c['source'])),'-map','0:v:0','-map','1:a:0','-c:a','aac','-b:a','128k','-t',str(n/24)]
    else: args+=['-an']
    args+=['-c:v','libx264','-preset','veryfast','-crf','18','-g','12','-pix_fmt','yuv420p','-movflags','+faststart',str(path)]
    with (d/'encode.log').open('w') as log:
        p=subprocess.Popen(args,stdin=subprocess.PIPE,stderr=log)
        try:
            hits=0
            for i,(pixels,hit) in enumerate(ordered_frames(make_frame,n)):
                hits+=hit
                p.stdin.write(pixels)
                report('Preparing foreground',i+1)
            p.stdin.close()
            report('Finishing playback',n)
            if p.wait()!=0: raise RuntimeError((d/'encode.log').read_text()[-2000:])
        finally:
            if p.poll() is None: p.terminate();p.wait()
    info=media.probe(path)
    if info['frames']!=n or abs(info['fps']-24)>.001: raise ValueError('Preview timeline mismatch')
    path.replace(d/'preview.mp4')
    return dict(state='ready',asset=store.asset_add(d/'preview.mp4'),frames=n,fps=24,width=pw*(2 if view=='comparison' else 1),height=ph,
                preparation_seconds=round(time.monotonic()-started,3),matte_cache_hits=hits)
