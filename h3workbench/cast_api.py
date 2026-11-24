"""Revision-guarded cast setup, uploads and character mask edits."""
import copy
from fastapi import APIRouter, UploadFile, File, Form
from PIL import Image, ImageOps, UnidentifiedImageError
from . import cast, masks, media, pipeline, project_characters, segments, store
from .config import MEDIA

router = APIRouter(prefix='/api/clips/{clip_id}/shots/{shot_id}/cast')


def change(clip_id, shot_id, revision, fn):
    def mutate(c):
        s = pipeline.shot_of(c, shot_id)
        if 'cast_setup' not in s:
            raise ValueError('Set up the shot cast first')
        fn(c, s)
        cast.validate_catalog(s, s['cast_setup'])
        segments.validate(s, segments.current(s))
        c['result'] = None
    return store.clip_mutate(clip_id, mutate, revision)


@router.post('/characters')
def add_character(clip_id: str, shot_id: str, body: dict):
    project_id = store.clip_get(clip_id)['batch_id']
    ids = body.get('character_ids')
    if not isinstance(ids, list) or not ids or any(not isinstance(key, str) for key in ids) or len(ids) != len(set(ids)):
        raise ValueError('Choose project characters to add to this shot')
    create_mask = body.get('create_mask', True)
    if type(create_mask) is not bool: raise ValueError('Invalid mask creation option')
    def add(c, s):
        target = None
        if 'segment_id' in body:
            target = next((v for v in s['segments'] if v['id'] == body['segment_id']), None)
            if target is None: raise ValueError('Segment no longer exists')
        with store.db() as con:
            entries = [project_characters.get(con, project_id, key) for key in ids]
        for entry in entries:
            existing = next((v for v in s['cast_setup']['characters'] if v.get('project_character_id') == entry['id']), None)
            if existing:
                if target is not None and existing['id'] not in target['cast_inputs']['characters']:
                    target['cast_inputs']['characters'].append(existing['id'])
                continue
            character = project_characters.shot_record(entry, store.uid('cast-'))
            track = {'id':store.uid('mask-'), 'character_id':character['id'],
                'state':{'mask':None,'expansion':64,'selection':'largest','subject_index':0,'mask_points':[],'mask_applied_points':[]}}
            s['cast_setup']['characters'].append(character)
            if create_mask and s.get('segment_masks_version') != 1: s['cast_setup']['mask_tracks'].append(track)
            for segment in [target] if target is not None else s['segments']:
                segment['cast_inputs']['characters'].append(character['id'])
                if create_mask and s.get('segment_masks_version') != 1:
                    segment['cast_inputs']['mask_tracks'].append(track['id'])
                    if 'mask_assignments' in segment['cast_inputs']:
                        segment['cast_inputs']['mask_assignments'][character['id']] = track['id']
    return change(clip_id, shot_id, body['revision'], add)


def shot_character(s, character_id):
    entry = next((v for v in s['cast_setup']['characters'] if v['id'] == character_id), None)
    if entry is None:
        raise ValueError('Character is no longer in this shot')
    return entry


@router.patch('/characters/{character_id}')
def change_character(clip_id: str, shot_id: str, character_id: str, body: dict):
    project_id = store.clip_get(clip_id)['batch_id']
    def swap(c, s):
        current = shot_character(s, character_id)
        with store.db() as con:
            replacement = project_characters.get(con, project_id, body.get('project_character_id'))
        if 'segment_id' in body:
            target = next((v for v in s['segments'] if v['id'] == body['segment_id']), None)
            if target is None: raise ValueError('Segment no longer exists')
            chosen = target['cast_inputs']
            if character_id not in chosen['characters']: raise ValueError('Character is not assigned to this segment')
            entry = next((v for v in s['cast_setup']['characters'] if v.get('project_character_id') == replacement['id']), None)
            if entry is None:
                entry = project_characters.shot_record(replacement, store.uid('cast-'))
                s['cast_setup']['characters'].append(entry)
            if entry['id'] != character_id and entry['id'] in chosen['characters']:
                raise ValueError('Character is already assigned to this segment')
            if s.get('segment_masks_version') == 1:
                chosen['characters'] = [entry['id'] if key == character_id else key for key in chosen['characters']]
                chosen['mask_assignments'] = {}
                return
            assignments = cast.character_masks(chosen)
            if entry['id'] != character_id:
                assignments[entry['id']] = assignments.pop(character_id, [])
            chosen['characters'] = [entry['id'] if key == character_id else key for key in chosen['characters']]
            cast.apply_character_masks(chosen, assignments)
            return
        # Keep the shot identity so every mask, selection and ordering stays put.
        current.update(project_characters.shot_record(replacement, character_id))
    return change(clip_id, shot_id, body['revision'], swap)


@router.post('/characters/{character_id}/duplicate')
def duplicate_character(clip_id: str, shot_id: str, character_id: str, body: dict):
    def duplicate(c, s):
        current = shot_character(s, character_id)
        library = s['cast_setup']
        duplicate = copy.deepcopy(current)
        duplicate['id'] = store.uid('cast-')
        library['characters'].insert(library['characters'].index(current) + 1, duplicate)
        original = next((t for t in library['mask_tracks'] if t['character_id'] == character_id), None)
        if original is None: return
        track = copy.deepcopy(original)
        track.update(id=store.uid('mask-'), character_id=duplicate['id'])
        library['mask_tracks'].insert(library['mask_tracks'].index(original) + 1, track)
        for segment in s['segments']:
            chosen = segment['cast_inputs']
            for collection, old, new in [('characters', character_id, duplicate['id']),
                                          ('mask_tracks', original['id'], track['id'])]:
                if old in chosen[collection]:
                    chosen[collection].insert(chosen[collection].index(old) + 1, new)
            if 'mask_assignments' in chosen and character_id in chosen['mask_assignments']:
                chosen['mask_assignments'][duplicate['id']] = track['id']
    return change(clip_id, shot_id, body['revision'], duplicate)


@router.delete('/characters/{character_id}')
def remove_character(clip_id: str, shot_id: str, character_id: str, body: dict):
    def remove(c, s):
        current = shot_character(s, character_id)
        library = s['cast_setup']
        tracks = {t['id'] for t in library['mask_tracks'] if t['character_id'] == character_id}
        library['characters'].remove(current)
        for track in library['mask_tracks']:
            if track['id'] in tracks: track['character_id'] = None
        for segment in s['segments']:
            chosen = segment['cast_inputs']
            if character_id not in chosen['characters']: continue
            if s.get('segment_masks_version') == 1:
                chosen['characters'].remove(character_id)
                chosen['mask_assignments'] = {}
                continue
            if 'mask_assignments' not in chosen:
                chosen['characters'] = [key for key in chosen['characters'] if key != character_id]
                chosen['mask_tracks'] = [key for key in chosen['mask_tracks'] if key not in tracks]
                continue
            assignments = cast.character_masks(chosen)
            chosen['characters'] = [key for key in chosen['characters'] if key != character_id]
            assignments.pop(character_id, None)
            cast.apply_character_masks(chosen, assignments)
            segment['use_mask'] = bool(chosen['mask_tracks'])
    return change(clip_id, shot_id, body['revision'], remove)


@router.post('/masks')
def add_mask(clip_id: str, shot_id: str, body: dict):
    def add(c, s):
        tracks = s['cast_setup']['mask_tracks']
        segment = None
        if s.get('segment_masks_version') == 1:
            segment = next((v for v in s['segments'] if v['id'] == body.get('segment_id')), None)
            if segment is None: raise ValueError('Choose a segment before adding a mask')
        local = [t for t in tracks if segment is None or t.get('segment_id') == segment['id']]
        number = 1
        names = {t.get('name', f'Mask {i+1}') for i, t in enumerate(local)}
        while f'Mask {number}' in names: number += 1
        record = {'id': store.uid('mask-'), 'character_id': None, 'name': f'Mask {number}',
            'color': cast.MASK_COLORS[(number-1) % len(cast.MASK_COLORS)],
            'state': {'mask': None, 'expansion': 64, 'selection': 'largest', 'subject_index': 0,
                      'mask_points': body.get('points', []), 'mask_applied_points': []}}
        if segment:
            record['segment_id'] = segment['id']
            segment['cast_inputs']['mask_tracks'].append(record['id'])
            segment['cast_inputs']['mask_assignments'] = {}
            if not local:
                segment['use_mask'] = True
        tracks.append(record)
    return change(clip_id, shot_id, body['revision'], add)


@router.patch('/masks/{track_id}')
def rename_mask(clip_id: str, shot_id: str, track_id: str, body: dict):
    name = body.get('name')
    cast._text(name, 'mask name', 120, empty=False)
    def rename(c):
        cast.track(pipeline.shot_of(c, shot_id), track_id)['name'] = name.strip()
    return store.clip_mutate(clip_id, rename, body['revision'])


@router.delete('/masks/{track_id}')
def remove_mask(clip_id: str, shot_id: str, track_id: str, body: dict):
    def remove(c, s):
        if s.get('segment_masks_version') != 1:
            for i, entry in enumerate(s['cast_setup']['mask_tracks']):
                entry.setdefault('name', f'Mask {i+1}')
                entry.setdefault('color', cast.MASK_COLORS[i % len(cast.MASK_COLORS)])
        track = cast.track(s, track_id)
        s.setdefault('mask_track_history', []).append(copy.deepcopy(track))
        s['cast_setup']['mask_tracks'].remove(track)
        for segment in s['segments']:
            chosen = segment['cast_inputs']
            if s.get('segment_masks_version') == 1:
                if segment['id'] == track['segment_id']:
                    chosen['mask_tracks'] = [k for k in chosen['mask_tracks'] if k != track_id]
                    chosen['mask_assignments'] = {}
                    if not any(t.get('segment_id') == segment['id'] for t in s['cast_setup']['mask_tracks']):
                        segment['use_mask'] = False
                continue
            if track_id not in chosen['mask_tracks']: continue
            assignments = cast.character_masks(chosen)
            cast.apply_character_masks(chosen, {key: [v for v in values if v != track_id] for key, values in assignments.items()})
            segment['use_mask'] = bool(chosen['mask_tracks'])
    return change(clip_id, shot_id, body['revision'], remove)


@router.patch('/anchors/{anchor_id}')
def edit_anchor(clip_id: str, shot_id: str, anchor_id: str, body: dict):
    edits = body.get('changes', {})
    if not isinstance(edits,dict) or set(edits) - {'name','prompt'}:
        raise ValueError('Edit the anchor name and instructions')
    def edit(c, s):
        a = next((v for v in s['cast_setup']['scene_anchors'] if v['id']==anchor_id),None)
        if a is None: raise ValueError('Anchor no longer exists')
        if 'name' in edits: a['name'] = edits['name']
        if 'prompt' in edits:
            cast._text(edits['prompt'], 'anchor prompt')
            a.setdefault('provenance',{})['prompt'] = edits['prompt']
    return change(clip_id, shot_id, body['revision'], edit)


@router.post('/images')
def image(clip_id: str, shot_id: str, revision: int=Form(...), kind: str=Form(...),
          entry_id: str=Form(''), source_frame: int|None=Form(None), file: UploadFile=File(...),
          capture_id: str=Form(''), segment_id: str=Form('')):
    c = store.clip_get(clip_id); s = pipeline.shot_of(c, shot_id)
    if c['revision'] != revision: raise store.Conflict('Shot changed; retry the upload')
    if 'cast_setup' not in s: raise ValueError('Set up the shot cast first')
    collection = 'scene_anchors'
    if kind != 'anchor': raise ValueError('Edit character references in Project Assets')
    if entry_id and not any(v['id']==entry_id for v in s['cast_setup'][collection]):
        raise ValueError('Upload target no longer exists')
    capture = next((v for v in s.get('anchor_captures', []) if v['id']==capture_id), None)
    if capture_id and (capture is None or capture.get('anchor_id')):
        raise ValueError('Capture is unavailable or already has an edited image')
    if segment_id and not any(v['id']==segment_id for v in segments.current(s)):
        raise ValueError('Target segment no longer exists')
    if capture:
        if entry_id: raise ValueError('Upload a capture as a new anchor')
        source_frame = capture['source_frame']
    if source_frame is not None and not 0 <= source_frame < s['end']-s['start']:
        raise ValueError('Anchor source frame is outside this shot')
    path = MEDIA/'batches'/c['batch_id']/clip_id/'cast-images'/f'{store.uid()}.png'
    path.parent.mkdir(parents=True,exist_ok=True)
    try:
        with Image.open(file.file) as im:
            ImageOps.exif_transpose(im).convert('RGB').save(path)
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ValueError('Upload a readable image file') from exc
    asset = store.asset_add(path)
    def save(v, target):
        library=target['cast_setup']
        existing=next((x for x in library[collection] if x['id']==entry_id),None)
        record={'id':entry_id or store.uid('anchor-'), 'name':existing['name'] if existing else f'Scene anchor {len(library[collection])+1}',
            'asset':asset,'source_frame':source_frame,'provenance':{'generator':'Manual import','created':store.now(),
            'source':v.get('render_source') or v['source'], 'source_frame':target['start']+source_frame if source_frame is not None else None,
            'source_asset':media.anchor_source(target) if source_frame==target.get('anchor_source_index',0) else None}}
        if capture:
            record['provenance'].update(source_asset=capture['asset'], capture_id=capture_id)
            next(v for v in target['anchor_captures'] if v['id']==capture_id)['anchor_id']=record['id']
        if existing:
            record['provenance']['previous']=existing
            if existing.get('provenance', {}).get('job_id'):
                record['provenance']['job_id']=existing['provenance']['job_id']
            library[collection][library[collection].index(existing)]=record
        else: library[collection].append(record)
        if segment_id:
            chosen=next(v for v in target['segments'] if v['id']==segment_id)['cast_inputs']['scene_anchors']
            if record['id'] not in chosen: chosen.append(record['id'])
    return change(clip_id, shot_id, revision, save)


@router.post('/captures')
def capture_frame(clip_id: str, shot_id: str, body: dict):
    """Retain an original frame for external editing without changing generation inputs."""
    c=store.clip_get(clip_id);s=pipeline.shot_of(c,shot_id)
    if body.get('revision')!=c['revision']: raise store.Conflict('Shot changed; capture the frame again')
    frame=body.get('frame')
    if type(frame) is not int or not 0<=frame<s['end']-s['start']:
        raise ValueError('Choose a frame inside this shot')
    segment_id=body.get('segment_id')
    if segment_id and not any(v['id']==segment_id for v in segments.current(s)):
        raise ValueError('Selected segment no longer exists')
    source=c.get('render_source') or c['source']
    directory=MEDIA/'batches'/c['batch_id']/clip_id/'anchor-captures'
    directory.mkdir(parents=True,exist_ok=True)
    asset=media.shot_frame(c,s,frame,directory/f'{store.uid()}.png')
    entry={'id':store.uid('capture-'),'asset':asset,'source':source,'source_frame':frame,
           'clip_frame':s['start']+frame,'segment_id':segment_id,'created':store.now()}
    def save(v):
        pipeline.shot_of(v,shot_id).setdefault('anchor_captures',[]).append(entry)
    result=store.clip_mutate(clip_id,save,body['revision'])
    return {'clip':result,'capture':entry}


def patch_track(clip_id, shot_id, track_id, body):
    edits=body['changes']
    if not isinstance(edits,dict) or set(edits)-{'mask_points','expansion','selection','subject_index','mask_text','mask_version'}:
        raise ValueError('Unknown character mask setting')
    def edit(c,s):
        virtual=cast.virtual_track(s,track_id)
        masks.preserve(virtual)
        if 'mask_version' in edits:
            if set(edits)!={'mask_version'}: raise ValueError('Select a saved mask separately')
            masks.select(virtual,edits['mask_version'],c['dimensions'])
            if 'mask_segment_range' in virtual:
                start, end = virtual['mask_segment_range']
                for field in ('mask_points', 'mask_applied_points'):
                    virtual[field] = [p for p in virtual[field] if start <= p['frame'] < end]
        else:
            if 'selection' in edits and edits['selection'] not in {'largest','leftmost','rightmost','index'}: raise ValueError('Invalid person selection')
            if 'subject_index' in edits and (type(edits['subject_index']) is not int or not 0<=edits['subject_index']<=100): raise ValueError('Invalid person index')
            if 'mask_text' in edits: cast._text(edits['mask_text'],'mask description')
            if any(k in edits and virtual.get(k)!=edits[k] for k in ('selection','subject_index','mask_text')):
                virtual['mask']=None
            virtual.update(edits)
            virtual['mask_approved']=False
        cast.save_track(s,track_id,virtual)
    return change(clip_id,shot_id,body['revision'],edit)


@router.post('/masks/{track_id}/absent')
def absent(clip_id: str, shot_id: str, track_id: str, body: dict):
    import numpy as np
    c=store.clip_get(clip_id);s=pipeline.shot_of(c,shot_id)
    if c['revision']!=body['revision']:raise store.Conflict('Shot changed; review the absent range')
    start,end=body.get('start'),body.get('end')
    if type(start) is not int or type(end) is not int or not 0<=start<end<=s['end']-s['start']:
        raise ValueError('Choose an absent range inside this shot')
    virtual=cast.virtual_track(s,track_id)
    if 'mask_segment_range' in virtual:
        begin, limit = virtual['mask_segment_range']
        if not begin <= start < end <= limit:
            raise ValueError('Choose an absent range inside this segment')
    if not virtual.get('mask'):raise ValueError('Track the character before marking an absent range')
    values=masks.read(virtual['mask'],s['end']-s['start'],c['dimensions'])
    values[start:end]=False
    path=MEDIA/'batches'/c['batch_id']/clip_id/'cast-masks'/f'{store.uid()}.npz'
    path.parent.mkdir(parents=True,exist_ok=True);np.savez_compressed(path,mask=values)
    result={'asset':store.asset_add(path),**masks.coverage(values)}
    def edit(v,sh):
        target=cast.virtual_track(sh,track_id)
        points=[p for p in target.get('mask_applied_points',[]) if not start<=p['frame']<end]
        target['mask_points']=[p for p in target.get('mask_points',[]) if not start<=p['frame']<end]
        masks.install(target,result,f'Absent frames {start}–{end-1}',points)
        cast.save_track(sh,track_id,target)
    return change(clip_id,shot_id,body['revision'],edit)


@router.post('/anchor-jobs')
def generate_anchor(clip_id: str, shot_id: str, body: dict):
    with store.LOCK:
        # A duplicate click rejoins the same in-flight request without generating twice.
        options = {k: v for k, v in body.items() if k != 'worker'}
        c = store.clip_get(clip_id); s = pipeline.shot_of(c, shot_id)
        from . import anchor_jobs
        frozen = anchor_jobs.freeze(c, s, options, body.get('worker', 'auto'))
        for job in store.pending_jobs(('queued', 'waiting', 'running', 'cancelling')):
            if job['kind'] == 'anchor_generate' and job['clip_id'] == clip_id and job['shot_id'] == shot_id:
                old = job['data']['options']
                if all(old.get(k) == frozen.get(k) for k in ('character_id', 'segment_id', 'frame', 'revision', 'model_offloading', 'prompt', 'model')) and ('seed' not in options or old['seed'] == options['seed']):
                    return store.public_job(job)
        job_id = pipeline.queue('anchor_generate', clip_id, shot_id, body.get('worker', 'auto'), options)
        return store.public_job(store.job_get(job_id))


@router.post('/anchor-jobs/{job_id}/use')
def use_generated_anchor(clip_id: str, shot_id: str, job_id: str, body: dict):
    from . import anchor_jobs
    job = store.job_get(job_id)
    if job['kind'] != 'anchor_generate' or job['clip_id'] != clip_id or job['shot_id'] != shot_id or job['state'] != 'success':
        raise ValueError('Choose a completed anchor from this shot')
    return change(clip_id, shot_id, body['revision'], lambda c, s: anchor_jobs.adopt(c, s, job, body.get('enabled', True), body.get('segment_id')))


@router.post('/anchor-draft')
def prepare_anchor(clip_id: str, shot_id: str, body: dict):
    from . import anchor_jobs
    with store.LOCK:
        c = store.clip_get(clip_id); s = pipeline.shot_of(c, shot_id)
        options = anchor_jobs.freeze(c, s, {k:v for k,v in body.items() if k != 'worker'}, body.get('worker', 'auto'))
        directory = MEDIA/'batches'/c['batch_id']/clip_id/'anchor-drafts'
        directory.mkdir(parents=True, exist_ok=True)
        source = media.shot_frame(c, s, options['frame'], directory/f"{store.uid()}.png")
        return {'source': source, 'reference': options['identity']['reference'], 'dimensions': c['dimensions'],
                'character_name': shot_character(s, options['character_id'])['name'], 'models': anchor_jobs.MODELS,
                'request': {k:v for k,v in options.items() if k not in {'identity', 'capacity_evidence'}}, 'worker': body.get('worker', 'auto')}


@router.post('/anchors/{anchor_id}/use')
def toggle_anchor(clip_id: str, shot_id: str, anchor_id: str, body: dict):
    from . import anchor_jobs
    return change(clip_id, shot_id, body['revision'], lambda c,s: anchor_jobs.toggle(s, anchor_id, body.get('segment_id'), body.get('enabled')))


@router.delete('/anchors/{anchor_id}')
def delete_anchor(clip_id: str, shot_id: str, anchor_id: str, body: dict):
    from . import anchor_jobs
    return change(clip_id, shot_id, body['revision'], lambda c,s: anchor_jobs.remove(s, anchor_id=anchor_id))


@router.delete('/anchor-jobs/{job_id}')
def delete_generated_anchor(clip_id: str, shot_id: str, job_id: str, body: dict):
    from . import anchor_jobs
    job = store.job_get(job_id)
    if job['kind'] != 'anchor_generate' or job['clip_id'] != clip_id or job['shot_id'] != shot_id or job['state'] != 'success':
        raise ValueError('Choose a completed anchor from this shot')
    return change(clip_id, shot_id, body['revision'], lambda c,s: anchor_jobs.remove(s, job_id=job_id))
