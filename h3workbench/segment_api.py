"""Revision-guarded segment editing and take selection."""
import copy
from pathlib import Path
from fastapi import APIRouter, UploadFile, File, Form
from fastapi.responses import FileResponse
from PIL import Image
from . import segments, segment_jobs, pipeline, store, media
from .config import MEDIA

router=APIRouter(prefix='/api/clips/{clip_id}/shots/{shot_id}/segments')


def context(clip_id,shot_id):
    c=store.clip_get(clip_id)
    return c,pipeline.shot_of(c,shot_id)


@router.get('')
def get(clip_id:str,shot_id:str):
    return segments.view(*context(clip_id,shot_id))


@router.post('/final')
def create_final(clip_id:str,shot_id:str,body:dict):
    from . import shot_finals
    context(clip_id, shot_id)
    return {'job': store.public_job(shot_finals.queue(clip_id, shot_id, body))}


@router.get('/attempts/{job_id}')
def attempt(clip_id:str,shot_id:str,job_id:str):
    from . import shot_finals
    context(clip_id, shot_id)
    return shot_finals.attempt(clip_id, shot_id, job_id)


@router.post('/preview')
def preview(clip_id:str,shot_id:str,body:dict):
    c,s=context(clip_id,shot_id)
    if body.get('revision') != c['revision']:raise store.Conflict('Shot changed; refresh the segment plan')
    items=segments.layout(s,body['operation'])
    return {'revision':c['revision'],'segments':items,
            'dialogue':[segments.dialogue(c,s,x) for x in items],
            'preserved_takes':sum(len(x.get('takes',[])) for x in segments.current(s))}


@router.post('/workflow')
def workflow(clip_id:str,shot_id:str,body:dict):
    from . import workflow
    def change(c):
        if any(j['clip_id']==clip_id and j['shot_id']==shot_id for j in store.pending_jobs(('queued','waiting','running','cancelling'))):
            raise store.Conflict('Wait for this shot’s active jobs before updating its workflow')
        workflow.adopt(pipeline.shot_of(c,shot_id))
    return store.clip_mutate(clip_id,change,body['revision'])


@router.post('/layout')
def layout(clip_id:str,shot_id:str,body:dict):
    return segments.apply_layout(clip_id,shot_id,body['revision'],body['operation'])


@router.patch('/{segment_id}')
def edit(clip_id:str,shot_id:str,segment_id:str,body:dict):
    return segments.edit(clip_id,shot_id,segment_id,body['revision'],body['changes'])


@router.post('/{segment_id}/take')
def select(clip_id:str,shot_id:str,segment_id:str,body:dict):
    def change(c):
        shot=pipeline.shot_of(c,shot_id);items=segments.current(shot)
        seg=next((x for x in items if x['id']==segment_id),None)
        if seg is None:raise ValueError('Segment no longer exists')
        chosen=segments.take(seg,body['take_id'])
        if chosen is None:raise ValueError('Take does not belong to this segment')
        # Choosing an older take also restores its editable local directions.
        # Shared settings and predecessor choices remain explicit user choices.
        seg.update({k:copy.deepcopy(v) for k,v in chosen.get('settings',{}).items() if k in segments.EDITABLE})
        seg['render_mode'] = chosen.get('settings', {}).get('render_mode') or 'swap'
        seg['guide_prompt'] = chosen.get('settings', {}).get('guide_prompt') or ''
        seg['cast_prompt_signature'] = chosen.get('settings', {}).get('cast_prompt_signature')
        # Selecting a saved take restores its exact text until Update prompt is requested.
        seg['prompt_mode'] = 'manual'
        for key in segments.PROMPT_FIELDS:
            seg[key] = copy.deepcopy(chosen.get('settings', {}).get(key))
        if 'cast_setup' in shot and seg.get('cast_inputs') is None:
            from . import cast
            seg['cast_inputs'] = cast.legacy_selection(shot, seg)
        seg['needs_review']=False
        if shot.get('segment_masks_version') == 1:
            from . import segment_masks
            segment_masks.restore_take(shot, seg, chosen)
        segments.validate(shot,items)
        seg['selected_take']=chosen['id'];shot['segments']=items
        shot['selected_render']=None;c['result']=None
    c=store.clip_mutate(clip_id,change,body['revision'])
    s=pipeline.shot_of(c,shot_id)
    queued=None
    if all(segments.reusable_take(c, s, seg) for seg in segments.current(s)):
        queued=pipeline.queue('segment_assemble',clip_id,shot_id)
    return {'clip':c,'assembly_job':queued}


@router.post('/{segment_id}/prompt')
def update_prompt(clip_id:str,shot_id:str,segment_id:str,body:dict):
    return segments.update_prompt(clip_id,shot_id,segment_id,body['revision'])


@router.get('/{segment_id}/source-frame')
def frame(clip_id:str,shot_id:str,segment_id:str):
    c,s=context(clip_id,shot_id)
    seg=next((x for x in segments.current(s) if x['id']==segment_id),None)
    if seg is None:raise ValueError('Segment no longer exists')
    index=s['start']+s.get('anchor_source_index',seg['start'])
    key=segments.digest([c.get('render_source') or c['source'],index])
    path=MEDIA/'segment-frames'/f'{key}.png'
    if not path.exists():
        path.parent.mkdir(exist_ok=True)
        media.frame(store.path(c.get('render_source') or c['source']),index,path)
    return FileResponse(path,media_type='image/png')


@router.post('/{segment_id}/image')
def image(clip_id:str,shot_id:str,segment_id:str,revision:int=Form(...),file:UploadFile=File(...)):
    c,s=context(clip_id,shot_id)
    if revision!=c['revision']:raise store.Conflict('Shot changed; retry the image import')
    if not any(x['id']==segment_id for x in segments.current(s)):raise ValueError('Segment no longer exists')
    path=MEDIA/'batches'/c['batch_id']/clip_id/'images'/f'{store.uid()}.png'
    path.parent.mkdir(parents=True,exist_ok=True)
    with Image.open(file.file) as im:im.convert('RGB').save(path)
    asset=store.asset_add(path)
    def change(v):
        shot=pipeline.shot_of(v,shot_id);items=segments.current(shot)
        seg=next(x for x in items if x['id']==segment_id)
        seg['anchors'].append({'id':store.uid(),'asset':asset,'generator':'Manual import',
            'source_frame':shot['start']+shot.get('anchor_source_index',seg['start']),
            'reference':media.reference(v,shot),'created':store.now()})
        seg.update(anchor=asset,anchor_mode='custom');shot['segments']=items
    return store.clip_mutate(clip_id,change,revision)
