"""Explicit single-pass segment jobs through the existing render executor."""
import copy
import json
import subprocess
from pathlib import Path
import numpy as np
from . import media, masks, remote, render_models, segments, store
from .config import MEDIA


ACTIVE_STATES = ('queued', 'waiting', 'running', 'cancelling')


def scope(plan):
    return [{'id': plan['segments'][i]['id'], 'index': i,
             'start': plan['segments'][i]['start'], 'end': plan['segments'][i]['end']}
            for i in plan['indices']]


def freeze(clip, shot, request):
    if not isinstance(request, dict) or set(request) != {'mode','segment_id','plan_revision'}:
        raise ValueError('Invalid segment render request')
    items = segments.validate(shot, segments.current(shot))
    if request['plan_revision'] != segments.plan_revision(shot):
        raise store.Conflict('Segment settings changed; review the saved plan before generating')
    mode = request['mode']
    if mode not in {'segment','from','all'}:
        raise ValueError('Choose this segment, from here, or the whole shot')
    start = next((i for i,s in enumerate(items) if s['id'] == request['segment_id']),None)
    if start is None: raise ValueError('Choose a current segment')
    indices = [start] if mode == 'segment' else list(range(0 if mode == 'all' else start, len(items)))
    status = segments.states(clip,shot,items)
    active = [j for j in store.pending_jobs(ACTIVE_STATES, full=True)
              if j['kind'] == 'render' and j['clip_id'] == clip['id'] and j['shot_id'] == shot['id']]
    owners = {}
    covered = set()
    for job in active:
        plan = job['data'].get('segment_plan')
        if not plan:
            raise store.Conflict('A whole-shot render is active; wait for it before queueing segments')
        for entry in scope(plan):
            covered.add(entry['id'])
            if job['id']+'-'+entry['id'] not in job['data'].get('segment_results', []):
                owners[entry['id']] = job
    if mode == 'segment' and items[start]['id'] in owners:
        raise store.Conflict('This segment is already queued or generating')
    if mode != 'segment' and active:
        indices = [i for i in indices if items[i]['id'] not in covered and not status[i]['current']]
    if not indices:
        raise store.Conflict('These segments are already current, queued or generating')
    bindings = {}
    for i in indices:
        seg = items[i]
        if seg['anchor_mode'] != 'none' and not segments.anchor(shot,seg):
            raise ValueError(f'Segment {i+1}: select an anchor or choose no anchor')
        if shot.get('use_mask',True):
            if not shot.get('mask'): raise ValueError('Create a person mask or disable masking')
            begin = max(0, seg['start'] - bool(segments.effective_history(seg)))
            if any(begin <= f < seg['end'] for f in masks.pending_frames(shot)):
                raise ValueError(f'Segment {i+1}: apply the pending mask corrections')
            if any(begin <= f < seg['end'] for f in shot.get('mask_empty_frames',[])):
                raise ValueError(f'Segment {i+1}: correct the missing mask frames')
        for predecessor, _ in segments.predecessors(items,i):
            j = items.index(predecessor)
            if j not in indices:
                owner = owners.get(predecessor['id'])
                if owner:
                    parent = next(v for v in owner['data']['segment_plan']['segments'] if v['id'] == predecessor['id'])
                    signature = segments.setting_signature(clip, shot, predecessor)
                    if segments.setting_signature(owner['data']['snapshot'], owner['data']['shot'], parent) != signature:
                        raise store.Conflict(f'Segment {j+1} settings changed after queueing; cancel its queued render or restore those settings first')
                    if owner['state'] == 'cancelling':
                        raise store.Conflict(f'Segment {j+1} is stopping; queue it again after it stops')
                    bindings[predecessor['id']] = {'job_id': owner['id'], 'segment_id': predecessor['id'],
                        'index': j, 'signature': signature, 'take_id': owner['id']+'-'+predecessor['id']}
                    continue
                selected = segments.take(predecessor)
                if not status[j]['current'] or not selected or not selected.get('master'):
                    raise ValueError(f'Generate segment {j+1} first, or generate from an earlier segment')
                if selected['dimensions'] != media.render_spec(clip,shot)['dimensions']:
                    raise ValueError('Continuation resolution changed; regenerate the preceding segments')
    return {'segments':items,'indices':indices,'mode':mode,'dependencies':list(bindings.values()),
            'original_selections':{s['id']:s.get('selected_take') for s in items}}


def resolve_dependencies(job):
    """Bind only the exact promised outputs, before reserving a worker/preparation slot."""
    if not job['data'].get('segment_dependencies') or job['data'].get('segment_dependencies_resolved'):
        return True
    job = store.job_get(job['id'])
    plan = copy.deepcopy(job['data']['segment_plan'])
    for binding in job['data']['segment_dependencies']:
        parent = store.job_get(binding['job_id'])
        result = parent['data'].get('segment_outputs', {}).get(binding['segment_id'])
        if not result and binding['take_id'] in parent['data'].get('segment_results', []):
            # Jobs accepted before this release already saved their takes in the clip.
            clip = store.clip_get(parent['clip_id'])
            shot = next(s for s in clip['shots'] if s['id'] == parent['shot_id'])
            layouts = [segments.current(shot), *[v['segments'] for v in shot.get('segment_layout_history', [])]]
            result = next((t for layout in layouts for s in layout for t in s['takes'] if t['id'] == binding['take_id']), None)
        if result:
            if (result['id'] != binding['take_id'] or result['signature'] != binding['signature']
                    or result.get('dimensions') != job['data']['render_spec']['dimensions'] or not result.get('master')):
                store.job_update(job['id'], 'failed', phase='Preceding segment output does not match',
                                 error=f"Segment {binding['index']+1} returned an incompatible continuation take")
                return False
            item = plan['segments'][binding['index']]
            if not any(t['id'] == result['id'] for t in item['takes']):
                item['takes'].append(copy.deepcopy(result))
            item['selected_take'] = result['id']
        else:
            if parent['state'] not in ACTIVE_STATES:
                store.job_update(job['id'], 'failed', phase='Preceding segment did not finish',
                                 error=f"Segment {binding['index']+1} {parent['state']}; its required take is unavailable. Queue the predecessor and this segment again.")
            else:
                phase = f"Waiting for segment {binding['index']+1}"
                if job['data'].get('phase') != phase:
                    store.job_update(job['id'], phase=phase)
            return False
    store.job_update(job['id'], segment_plan=plan, segment_dependencies_resolved=True, phase='Queued')
    return True


def history_file(items,index,dimensions,directory):
    arrays = []
    width,height = dimensions
    for seg,n in segments.predecessors(items,index):
        chosen = segments.take(seg)
        if not chosen or not chosen.get('master') or chosen['dimensions'] != dimensions:
            raise ValueError('A matching continuation master is missing')
        total = seg['end']-seg['start']
        args = ['ffmpeg','-v','error','-nostdin','-i',str(store.path(chosen['master'])),
                '-vf',f'select=gte(n\\,{total-n})','-frames:v',str(n),'-vsync','0',
                '-f','rawvideo','-pix_fmt','rgb48le','pipe:1']
        completed = subprocess.run(args,check=True,capture_output=True,timeout=180)
        pixels = np.frombuffer(completed.stdout,dtype='<u2')
        if len(pixels) != n*width*height*3:
            raise ValueError('Continuation master has the wrong frame count or dimensions')
        arrays.append(pixels.reshape(n,height,width,3).astype(np.float32)/np.float32(65535))
    if not arrays: return None
    path = directory/'continuation-history.npz'
    np.savez_compressed(path,frames=np.concatenate(arrays))
    return path


def graph(clip, shot, items, index, job, directory):
    seg = items[index]; h = segments.effective_history(seg)
    source_start = seg['start'] - h
    virtual = copy.deepcopy(shot)
    virtual.update(start=shot['start']+source_start,end=shot['start']+seg['end'],
                   prompt=segments.prompt(clip,shot,seg), seed=seg['seed'],
                   anchor=segments.anchor(shot,seg),use_anchor=bool(segments.anchor(shot,seg)))
    spec = media.render_spec(clip,shot)
    if virtual.get('use_mask',True):
        mask = masks.read(shot['mask'],shot['end']-shot['start'],spec['dimensions'])
        path=directory/'segment-mask.npz';np.savez_compressed(path,mask=mask[source_start:seg['end']])
        virtual['mask']=store.asset_add(path)
    stage=copy.deepcopy(job)
    stage['data'].update(shot=virtual,render_spec=spec,generation=render_models.settings(virtual))
    result,files=remote.graph_render(clip,virtual,stage,directory)
    result['805']['inputs']['schedule'] = 'segment'
    result['save_history'] = {'class_type': 'H3RelaySaveHistory', 'inputs': {
        'frames': ['807', 0], 'filename_prefix': job['id']}}
    if h:
        file = history_file(items,index,spec['dimensions'],directory)
        name = job['id']+'-history.npz'; files[name] = file
        result['load_history'] = {'class_type': 'H3RelayLoadHistory', 'inputs': {'history_file': name}}
        for key in ('805', '803', 'append'):
            result[key]['inputs']['initial_history'] = ['load_history', 0]
        # Picture 2 is the generated boundary; an explicit new anchor is Picture 3.
        result['801']['inputs']['ref_images.ref_image_1'] = ['803', 11]
        if virtual['anchor']:
            result['801']['inputs']['ref_images.ref_image_2'] = ['309', 0]
        result['history']['inputs']['first_frame_guide'] = True
    return result,files,stage


def collect(job, clip, shot, items,index,directory,output,graph):
    seg=items[index]
    history=json.loads((output/'history.json').read_text())
    from . import relay_contract
    report, video, master = relay_contract.outputs(history, output/'output', continuation=True)
    spec=media.render_spec(clip,shot)
    for file in [video,master]:
        info=media.probe(file)
        if info['frames'] != seg['end']-seg['start'] or [info['width'],info['height']] != spec['dimensions'] or abs(info['fps']-24)>.001:
            raise ValueError('Segment output differs from its exact source range')
    if len(report['windows']) != 1 or report['history_frames'] != segments.effective_history(seg):
        raise ValueError('Segment execution did not match the single-pass plan')
    return {**spec, **render_models.settings(shot),
        'id':job['id']+'-'+seg['id'],'segment_id':seg['id'], 'start':seg['start'],'end':seg['end'],
        'asset':store.asset_add(video),'master':store.asset_add(master),'api':store.asset_add(directory/'api.json'),
        'signature':segments.setting_signature(clip,shot,seg),'dependencies':segments.dependencies(items,index),
        'prompt':segments.prompt(clip,shot,seg),'dialogue':segments.dialogue(clip,shot,seg),'seed':seg['seed'],
        'anchor':segments.anchor(shot,seg),'history_frames':segments.effective_history(seg),
        'window_frames':report['windows'][0]['window_frames'],
        'use_anchor':bool(segments.effective_history(seg) or segments.anchor(shot,seg)),
        'settings':{k:copy.deepcopy(seg.get(k)) for k in segments.EDITABLE|{'id','start','end'}},'created':store.now(),
        'lora':render_models.identify(graph),'reference':clip['reference'],'raw_decoded':True,
        'continuation_format':'ffv1-rgb48','worker':job['data']['worker']}


def attach_take(job,result):
    from . import pipeline
    def update(c,shot,ok):
        layouts=[segments.current(shot),*[v['segments'] for v in shot.get('segment_layout_history',[])]]
        current=layouts[0]
        for layout in layouts:
            seg=next((s for s in layout if s['id']==result['segment_id'] and s['start']==result['start'] and s['end']==result['end']),None)
            if seg is None: continue
            if not any(t['id']==result['id'] for t in seg['takes']): seg['takes'].append(result)
            if layout is current:
                i=current.index(seg)
                unchanged = seg.get('selected_take') == job['data']['segment_plan']['original_selections'][seg['id']]
                if (ok and unchanged and segments.setting_signature(c,shot,seg)==result['signature']
                        and segments.dependencies(current,i)==result['dependencies']):
                    seg['selected_take']=result['id']
            break
        shot['segments']=current
    pipeline.attach(job,update)


def assemble(clip,shot,directory):
    items=segments.current(shot)
    if not all(s['current'] for s in segments.states(clip,shot,items)):
        return None
    selected=[segments.take(s) for s in items]
    identity=segments.digest([t['id'] for t in selected])[:20]
    existing=next((r for r in shot['renders'] if r.get('segment_chain')==identity),None)
    if existing: return existing
    directory.mkdir(parents=True,exist_ok=True)
    listing=directory/'segments.txt'
    def quote(path): return str(path).replace("'", "'\\''")
    listing.write_text(''.join("file '"+quote(store.path(t['master']))+"'\n" for t in selected))
    source=media.shot_media(clip,shot,directory,dimensions=selected[0]['dimensions'])
    master=directory/'shot-master.mkv'
    media.ff(['-f','concat','-safe','0','-i',listing,'-i',source,'-map','0:v:0','-map','1:a?',
              '-c:v','copy','-c:a','pcm_s16le','-t',str((shot['end']-shot['start'])/24),master])
    movie=directory/'shot.mp4'
    media.ff(['-i',master,'-c:v','libx264','-preset','fast','-crf','16','-pix_fmt','yuv420p',
              '-c:a','aac','-b:a','192k','-movflags','+faststart',movie])
    if media.probe(movie)['frames'] != shot['end']-shot['start']:
        raise ValueError('Selected segments do not assemble to the exact shot duration')
    return {'id':'segmented-'+identity,'asset':store.asset_add(movie),'master':store.asset_add(master),
        'segment_chain':identity,'segment_takes':[{'segment_id':s['id'],'take_id':t['id']} for s,t in zip(items,selected)],
        'seed':shot['seed'],'prompt':shot['prompt'],'reference':clip['reference'],'created':store.now(),
        'settings':{'segments':[{k:copy.deepcopy(s[k]) for k in segments.EDITABLE|{'id','start','end','selected_take'}} for s in items], **render_models.settings(shot)},
        'lora':copy.deepcopy(render_models.LORAS[render_models.settings(shot)['lora_id']]),
        **media.render_spec(clip,shot), **render_models.settings(shot)}


def attach_assembly(job,clip,shot,result):
    if not result:return
    from . import pipeline
    def update(c,current,ok):
        if not any(r['id']==result['id'] for r in current['renders']):current['renders'].append(result)
        items=segments.current(current)
        if (all(v['current'] for v in segments.states(c,current,items)) and
                segments.digest([s.get('selected_take') for s in items])[:20]==result['segment_chain']):
            current['selected_render']=result['id'];c['result']=None
    pipeline.attach(job,update)


def execute(job,directory):
    from . import pipeline
    clip=job['data']['snapshot'];shot=job['data']['shot']
    items=copy.deepcopy(job['data']['segment_plan']['segments'])
    completed=[]
    for i in job['data']['segment_plan']['indices']:
        remote.check_cancel(job)
        store.job_update(job['id'],segment_progress={'index':i,'number':i+1,'total':len(items),'segment_id':items[i]['id']})
        stage_dir=directory/f'segment-{i+1}';stage_dir.mkdir(exist_ok=True)
        receipt=stage_dir/'segment-result.json'
        if receipt.exists():
            result=json.loads(receipt.read_text())
        else:
            store.job_update(job['id'],phase=f'Generating segment {i+1} of {len(items)}')
            graph_data,files,stage=graph(clip,shot,items,i,job,stage_dir)
            output=remote.execute(stage,graph_data,files,stage_dir,
                                  profile='full-offload' if render_models.offloading(job) else 'full')
            # Preserve the worker resolved by Auto for subsequent segments.
            job['data']['worker']=stage['data']['worker']
            job['data']['shared_stages']=store.job_get(job['id'])['data'].get('shared_stages',[])
            result=collect(job,clip,shot,items,i,stage_dir,output,graph_data)
            receipt.write_text(json.dumps(result,indent=2))
        pipeline.check_finalization_cancel(job)
        attach_take(job,result)
        if not any(t['id']==result['id'] for t in items[i]['takes']):items[i]['takes'].append(result)
        items[i]['selected_take']=result['id'];completed.append(result['id'])
        outputs = store.job_get(job['id'])['data'].get('segment_outputs', {})
        outputs[items[i]['id']] = result
        store.job_update(job['id'],segment_results=completed,segment_outputs=outputs,finalizing=False)
    current_clip=store.clip_get(job['clip_id']);current_shot=pipeline.shot_of(current_clip,job['shot_id'])
    result=assemble(current_clip,current_shot,directory/'assembly')
    attach_assembly(job,current_clip,current_shot,result)
    return {'segments':completed,'assembly':result}


def generate_anchor(job,directory):
    from . import anchors,pipeline
    c=job['data']['snapshot'];shot=job['data']['shot'];key=job['data']['options']['segment_id']
    seg=next(s for s in segments.current(shot) if s['id']==key)
    frame=media.frame(store.path(c.get('render_source') or c['source']),shot['start']+seg['start'],directory/'source-frame.png')
    virtual={**shot,'source_frame':frame,'anchor_instructions':seg['prompt']}
    result=anchors.generate(c,virtual,directory)
    def update(v,sh,ok):
        items=segments.current(sh);target=next((s for s in items if s['id']==key),None)
        if target is None:return
        target['anchors'].append(result)
        if ok and all(target[k]==seg[k] for k in ['start','end','prompt','anchor','anchor_mode']):
            target.update(anchor=result['asset'],anchor_mode='custom')
        sh['segments']=items
    pipeline.attach(job,update)
    return result
