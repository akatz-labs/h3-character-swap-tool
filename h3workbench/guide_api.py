"""Guide editing API; revisions protect drafts and approval from concurrent edits."""
import copy
from fastapi import APIRouter, UploadFile, File, Form
from . import guide_prompts, guide_provider, guides, media, pipeline, segments, store

router = APIRouter()
BASE = '/api/clips/{clip_id}/shots/{shot_id}/guides'


def context(clip_id, shot_id):
    clip = store.clip_get(clip_id)
    return clip, pipeline.shot_of(clip, shot_id)


def mutate(clip_id, shot_id, revision, fn):
    def change(clip):
        fn(clip, pipeline.shot_of(clip, shot_id))
    return store.clip_mutate(clip_id, change, revision)


@router.get('/api/guide-provider')
def provider():
    return guide_provider.status()


@router.get(BASE)
def list_guides(clip_id: str, shot_id: str):
    clip, shot = context(clip_id, shot_id)
    return {'revision': clip['revision'], 'guides': [dict(g, helpers=g.get('helpers', []), current=guides.current(clip, shot, g))
        for g in shot.get('guides', []) if not g.get('archived')], 'references': guides.references(clip, shot),
        'helper_presets': guide_prompts.presets(), 'prompt_prefix': ''}


@router.post(BASE)
def add(clip_id: str, shot_id: str, body: dict):
    with store.LOCK:
        clip, shot = context(clip_id, shot_id)
        if body.get('revision') != clip['revision']:
            raise store.Conflict('Shot changed; add the guide again')
        frame = body.get('frame')
        if type(frame) is not int or not 0 <= frame < shot['end'] - shot['start']:
            raise ValueError('Choose a source frame inside this shot')
        existing = next((g for g in shot.get('guides', []) if not g.get('archived') and g['frame'] == frame + shot['start'] and guides.current(clip, shot, g)), None)
        if existing:
            return {'clip': clip, 'guide_id': existing['id']}
        key = store.uid('guide-')
        path = store.MEDIA / 'batches' / clip['batch_id'] / clip_id / 'guides' / (key + '.png')
        path.parent.mkdir(parents=True, exist_ok=True)
        original = media.shot_frame(clip, shot, frame, path)
        helper = next(p for p in guide_prompts.presets() if p['id'] == 'character-swap')
        record = {'id': key, 'frame': shot['start'] + frame, 'source': guides.source(clip),
            'source_asset': original, 'dimensions': clip['dimensions'], 'prompt': '', 'references': [],
            'quality': 'medium', 'helpers': [{'id': helper['id'], 'text': helper['text']}],
            'enabled': True, 'approved': None, 'versions': [], 'created': store.now()}
        result = mutate(clip_id, shot_id, body['revision'], lambda c, s: s.setdefault('guides', []).append(record))
        return {'clip': result, 'guide_id': key}


@router.patch(BASE + '/{guide_id}')
def edit(clip_id: str, shot_id: str, guide_id: str, body: dict):
    project_id = store.clip_get(clip_id)['batch_id']
    def change(c, s):
        changes = body.get('changes', {})
        guide = guides.marker(s, guide_id)
        guides.validate_draft({**c, 'batch_id': project_id}, s, guide, changes)
        guide.update(changes)
        if 'enabled' in changes:
            guides.sync_reference(c, s, guides.marker(s, guide_id))
    return mutate(clip_id, shot_id, body['revision'], change)


@router.delete(BASE + '/{guide_id}')
def archive(clip_id: str, shot_id: str, guide_id: str, body: dict):
    def change(c, s):
        guide = guides.marker(s, guide_id)
        guide.update(archived=True, enabled=False)
        guides.sync_reference(c, s, guide)
    return mutate(clip_id, shot_id, body['revision'], change)


@router.post(BASE + '/{guide_id}/approve')
def approve(clip_id: str, shot_id: str, guide_id: str, body: dict):
    def change(c, s):
        guide = guides.marker(s, guide_id)
        if not guides.current(c, s, guide):
            raise ValueError('The source or canvas changed. Add a guide at the correct frame.')
        version = body.get('version_id')
        if version is not None and not any(v['id'] == version for v in guide['versions']):
            raise ValueError('Choose a saved version of this guide')
        guide['approved'] = version
        guides.sync_reference(c, s, guide)
    return mutate(clip_id, shot_id, body['revision'], change)


@router.post(BASE + '/{guide_id}/save')
def save(clip_id: str, shot_id: str, guide_id: str, body: dict):
    """Apply settings and the chosen version together; uploads remain immutable history."""
    project_id = store.clip_get(clip_id)['batch_id']
    def change(c, s):
        guide = guides.marker(s, guide_id)
        changes = body.get('changes', {})
        if not isinstance(changes, dict) or set(changes) - {'prompt', 'quality', 'references', 'helpers', 'edit_source_asset', 'edit_model'}: raise ValueError('Unknown guide setting')
        guides.validate_draft({**c, 'batch_id': project_id}, s, guide, changes)
        version = body.get('version_id')
        if version is not None:
            if not guides.current(c, s, guide): raise ValueError('Source changed; create a new guide')
            if not any(v['id'] == version for v in guide['versions']): raise ValueError('Choose a saved version')
        guide.update(changes, approved=version)
        guides.sync_reference(c, s, guide)
    return mutate(clip_id, shot_id, body['revision'], change)


@router.post(BASE + '/{guide_id}/generate')
def generate(clip_id: str, shot_id: str, guide_id: str, body: dict):
    with store.LOCK:
        c, s = context(clip_id, shot_id)
        if body.get('revision') != c['revision']:
            raise store.Conflict('Guide changed; review it before generating')
        guide = copy.deepcopy(guides.marker(s, guide_id))
        changes = body.get('changes', {})
        if not isinstance(changes, dict) or set(changes) - {'prompt', 'quality', 'references', 'helpers', 'edit_source_asset', 'edit_model'}: raise ValueError('Unknown generation setting')
        guides.validate_draft(c, s, guide, changes)
        guide.update(changes)
        guide.setdefault('helpers', [])
        guide['edit_source_asset'] = guide.get('edit_source_asset') or guide['source_asset']
        if not guides.current(c, s, guide):
            raise ValueError('The source or canvas changed; add a new guide')
        guides.validate_draft(c, s, guide, {k: guide[k] for k in ('prompt', 'quality', 'references', 'helpers', 'edit_source_asset', 'edit_model') if k in guide})
        if not guide['prompt'].strip() and not guide.get('helpers'):
            raise ValueError('Describe the edit before generating')
        names = {v['asset']: v['name'] for v in guides.references(c, s)}
        guide['reference_labels'] = [names[key] for key in guide['references']]
        full_prompt = guide_prompts.compose(guide)
        if len(full_prompt) > 32000: raise ValueError('The full guide prompt must be at most 32,000 characters')
        if guide_provider.provider() not in {'private-openai', 'local-openai'}:
            raise ValueError('Connect AKATZ Labs generation or upload a guide image')
        selected_provider = guide_provider.provider()
        execution = {'worker': 'cpu'}
        if guide.get('edit_model') == 'h3-image':
            from . import local_h3_image
            if selected_provider != 'local-openai':
                raise ValueError('H3 Image Edit is available only in the local runner')
            worker = body.get('worker', 'auto')
            local_h3_image.ready(worker)
            offload = body.get('model_offloading', True)
            if type(offload) is not bool: raise ValueError('Model offloading must be true or false')
            selected_provider = 'local-h3-image'
            execution = {'worker': worker, 'requested_worker': worker, 'execution_backend': 'shared',
                         'render_spec': {'dimensions': local_h3_image.dimensions(guide['dimensions'], guide['quality'])}}
        else:
            guide_provider.ready()
        job = store.job_create('guide_generate', clip_id, shot_id, {'snapshot': c, 'shot': s,
            'options': {'guide': copy.deepcopy(guide), 'full_prompt': full_prompt,
                        'image_size': 'x'.join(map(str, guide['dimensions'])),
                        'provider': selected_provider, 'model_offloading': body.get('model_offloading', True)}, **execution, 'phase': 'Queued'})
        return store.public_job(store.job_get(job))


@router.post(BASE + '/{guide_id}/upload')
def upload(clip_id: str, shot_id: str, guide_id: str, revision: int = Form(...),
           kind: str = Form(...), file: UploadFile = File(...), select_reference: bool = Form(True)):
    with store.LOCK:
        c, s = context(clip_id, shot_id)
        if c['revision'] != revision:
            raise store.Conflict('Guide changed; retry the upload')
        guides.marker(s, guide_id)
        if kind not in {'reference', 'version', 'source'}:
            raise ValueError('Upload a source, reference or edited version')
        path = store.MEDIA / 'batches' / c['batch_id'] / clip_id / 'guides' / (store.uid() + '.png')
        asset = guides.save_image(file.file, path)
        def attach(c, s):
            guide = guides.marker(s, guide_id)
            if kind == 'reference':
                if len(guide['references']) >= 8:
                    raise ValueError('Use at most 8 additional references')
                s.setdefault('guide_references', []).append({'asset': asset, 'name': (file.filename or 'Reference')[:160], 'kind': 'Uploaded image'})
                if select_reference: guide['references'].append(asset)
            elif kind == 'source':
                guide.setdefault('source_uploads', []).append(asset)
            else:
                guide['versions'].append({'id': store.uid('version-'), 'asset': asset, 'created': store.now(),
                    'model': 'Uploaded edit', 'prompt': '', 'references': [], 'quality': None, 'note': ''})
        return mutate(clip_id, shot_id, revision, attach)


@router.post(BASE + '/render/{segment_id}')
def render(clip_id: str, shot_id: str, segment_id: str, body: dict):
    from . import workflow
    with store.LOCK:
        c,s=context(clip_id,shot_id)
        if body.get('revision') != c['revision']:
            raise store.Conflict('Guides changed; review them before rendering')
        workflow.require(s)
        key=pipeline.queue('render',clip_id,shot_id,body.get('worker','auto'),
            {'model_offloading':body.get('model_offloading',False),
             'segments':{'mode':'segment','segment_id':segment_id,'plan_revision':segments.plan_revision(s)}})
        return store.public_job(store.job_get(key))


@router.post('/api/guide-jobs/{job_id}/collect')
def collect(job_id: str):
    from . import remote_shared
    job = store.job_get(job_id)
    if job['kind'] not in {'guide_generate', 'guide_render'} or job['state'] not in {'failed', 'interrupted', 'cancelled'}:
        raise ValueError('Choose an interrupted guide job')
    provider = job['data'].get('options', {}).get('provider')
    receipt = {'private-openai': 'openai-service-receipt.json', 'local-openai': 'local-openai-receipt.json'}.get(provider, 'comfy-receipt.json')
    has_receipt = remote_shared.recoverable(job) if job['data'].get('execution_backend') == 'shared' else (store.MEDIA / 'jobs' / job_id / receipt).exists()
    if not has_receipt:
        raise ValueError('No submission receipt; start a new generation')
    store.job_update(job_id, 'queued', phase='Collecting existing result', error=None, cancel_requested=False, finalizing=False)
    return store.public_job(store.job_get(job_id))
