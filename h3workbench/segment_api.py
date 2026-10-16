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


@router.post('/preview')
def preview(clip_id:str,shot_id:str,body:dict):
    c,s=context(clip_id,shot_id)
    if body.get('revision') != c['revision']:raise store.Conflict('Shot changed; refresh the segment plan')
    items=segments.layout(s,body['operation'])
    return {'revision':c['revision'],'segments':items,
            'dialogue':[segments.dialogue(c,s,x) for x in items],
            'preserved_takes':sum(len(x.get('takes',[])) for x in segments.current(s))}


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
        seg['needs_review']=False
        segments.validate(shot,items)
        seg['selected_take']=chosen['id'];shot['segments']=items
        shot['selected_render']=None;c['result']=None
    c=store.clip_mutate(clip_id,change,body['revision'])
    s=pipeline.shot_of(c,shot_id)
    queued=None
    if all(v['current'] for v in segments.states(c,s)):
        queued=pipeline.queue('segment_assemble',clip_id,shot_id)
    return {'clip':c,'assembly_job':queued}


@router.get('/{segment_id}/source-frame')
def frame(clip_id:str,shot_id:str,segment_id:str):
    c,s=context(clip_id,shot_id)
    seg=next((x for x in segments.current(s) if x['id']==segment_id),None)
    if seg is None:raise ValueError('Segment no longer exists')
    key=segments.digest([c.get('render_source') or c['source'],s['start']+seg['start']])
    path=MEDIA/'segment-frames'/f'{key}.png'
    if not path.exists():
        path.parent.mkdir(exist_ok=True)
        media.frame(store.path(c.get('render_source') or c['source']),s['start']+seg['start'],path)
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
            'source_frame':shot['start']+seg['start'],'created':store.now()})
        seg.update(anchor=asset,anchor_mode='custom');shot['segments']=items
    return store.clip_mutate(clip_id,change,revision)
