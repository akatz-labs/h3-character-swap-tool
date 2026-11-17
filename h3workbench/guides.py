"""Source-bound guide markers, immutable image versions and active selections."""
import copy
from PIL import Image, ImageOps, UnidentifiedImageError
from . import guide_prompts, media, store

PROPAGATION = ('Apply the changes shown in the edited guide frames throughout the source video. '
               'Preserve the source motion, timing, camera movement and unedited content.')


def marker(shot, key):
    value = next((v for v in shot.get('guides', []) if v['id'] == key and not v.get('archived')), None)
    if value is None:
        raise ValueError('Guide no longer exists')
    return value


def source(clip):
    return clip.get('render_source') or clip['source']


def current(clip, shot, guide):
    return guide['source'] == source(clip) and shot['start'] <= guide['frame'] < shot['end'] and guide['dimensions'] == clip['dimensions']


def selected(clip, shot, segment, strict=False, *, required=True):
    result = []
    for guide in sorted(shot.get('guides', []), key=lambda v: v['frame']):
        if guide.get('archived') or not guide['enabled'] or not shot['start'] + segment['start'] <= guide['frame'] < shot['start'] + segment['end']:
            continue
        version = next((v for v in guide['versions'] if v['id'] == guide.get('approved')), None)
        if version:
            if strict and not current(clip, shot, guide):
                raise ValueError('An approved guide belongs to another source or canvas; disable it and add a new guide')
            result.append({'id': guide['id'], 'frame': guide['frame'], 'version_id': version['id'],
                           'asset': version['asset'], 'source': guide['source'], 'dimensions': guide['dimensions']})
    if strict and required and not result:
        raise ValueError('Approve and enable at least one guide in this segment')
    return result


def signature(clip, shot, segment):
    from . import segments, cast, masks
    swap = segment.get('render_mode') == 'swap_pika'
    pending = [t['id'] for t in cast.resolve(clip, shot, segment)['mask_tracks'] if masks.pending_frames(t['state'])] if swap and segments.render_shot(shot, segment).get('use_mask', True) else []
    return segments.digest({**({'pending_masks': pending} if pending else {}), 'mode': ('viggle-remake-swap-v1' if segment.get('lora_id') == 'viggle-remake-swap' else 'swap-pika-v1.5-manual-sigmas-v5') if swap else 'ripple-v1', 'source': source(clip), 'shot_start': shot['start'],
        'range': [segment['start'], segment['end']], 'history_frames': segments.effective_history(segment), 'guides': selected(clip, shot, segment),
        'dimensions': media.render_spec(clip, shot)['dimensions'], 'seed': segment['seed'],
        'prompt': segments.prompt(clip, shot, segment),
        **({'references': cast.generation_inputs(clip, shot, segment, strict=False),
            'source_audio': segments.render_shot(shot, segment).get('use_source_audio', True)} if swap else {})})


def references(clip, shot):
    with store.db() as con:
        rows = con.execute('SELECT data FROM project_characters WHERE batch_id=? ORDER BY created', (clip['batch_id'],)).fetchall()
    import json
    result = []
    for row in rows:
        item = json.loads(row['data'])
        if item.get('reference'):
            result.append({'asset': item['reference'], 'name': item['name'], 'kind': 'Character'})
    for item in shot.get('cast_setup', {}).get('characters', []):
        if item.get('reference'):
            result.append({'asset': item['reference'], 'name': item['name'], 'kind': 'Character'})
    if not shot.get('cast_setup') and (shot.get('reference') or clip.get('reference')):
        result.append({'asset': shot.get('reference') or clip['reference'], 'name': 'Character reference', 'kind': 'Character'})
    result.extend(shot.get('guide_references', []))
    for guide in shot.get('guides', []):
        version = next((v for v in guide['versions'] if v['id'] == guide.get('approved')), None)
        if version and current(clip, shot, guide):
            result.append({'asset': version['asset'], 'name': f'Guide · frame {guide["frame"] - shot["start"]}', 'kind': 'Approved guide'})
    return list({v['asset']: v for v in result}.values())


def validate_draft(clip, shot, guide, changes):
    if not isinstance(changes, dict):
        raise ValueError('Guide settings must be an object')
    if set(changes) - {'prompt', 'references', 'quality', 'enabled', 'helpers', 'edit_source_asset', 'edit_model'}:
        raise ValueError('Unknown guide setting')
    if 'edit_source_asset' in changes:
        available = {guide['source_asset'], *guide.get('source_uploads', []), *[v['asset'] for v in guide['versions']]}
        value = changes['edit_source_asset']
        if not isinstance(value, str) or value not in available:
            raise ValueError('Choose the original frame, an uploaded source or a saved version of this guide')
    if 'prompt' in changes and (not isinstance(changes['prompt'], str) or len(changes['prompt']) > 12000):
        raise ValueError('Guide instructions must be text of at most 12,000 characters')
    if 'quality' in changes and (not isinstance(changes['quality'], str) or changes['quality'] not in {'low', 'medium', 'high'}):
        raise ValueError('Choose low, medium or high quality')
    if 'edit_model' in changes and (not isinstance(changes['edit_model'], str) or changes['edit_model'] not in {'gpt-image', 'h3-image'}):
        raise ValueError('Choose an available guide edit model')
    if 'helpers' in changes:
        guide_prompts.validate_helpers(changes['helpers'])
    if 'enabled' in changes and type(changes['enabled']) is not bool:
        raise ValueError('Enabled must be true or false')
    if 'references' in changes:
        ids = changes['references']
        available = {v['asset'] for v in references(clip, shot)}
        if not isinstance(ids, list) or len(ids) > 8 or any(not isinstance(v, str) or v not in available for v in ids) or len(ids) != len(set(ids)):
            raise ValueError('Choose up to 8 reference images from this project')


def save_image(file, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with Image.open(file) as image:
            if image.width * image.height > 40_000_000:
                raise ValueError('Image must be at most 40 megapixels')
            ImageOps.exif_transpose(image).convert('RGB').save(path)
    except (UnidentifiedImageError, OSError) as error:
        raise ValueError('Upload a readable image') from error
    return store.asset_add(path)


def sync_reference(clip, shot, guide):
    """Retire old guide-to-reference aliases without rewriting saved takes."""
    from . import cast, segments
    version = next((v for v in guide['versions'] if v['id'] == guide.get('approved')), None)
    active = version and guide['enabled'] and not guide.get('archived') and current(clip, shot, guide)
    if 'cast_setup' not in shot:
        if not active:
            return
        upgraded = cast.prepare_upgrade(clip, shot)
        # Older shots stored user text on the shot rather than on each segment.
        # Adding a guide must not silently replace that text during cast upgrade.
        for segment in upgraded['segments']:
            if segment.get('performance_prompt') is None and shot.get('prompt', '').strip():
                segment['performance_prompt'] = segments.prompt(clip, shot, segment)
                segment['prompt_mode'] = 'manual'
        shot.update(upgraded)
    library = shot['cast_setup']['scene_anchors']
    prior = {v['id'] for v in library if v.get('provenance', {}).get('guide_id') == guide['id']}
    for segment in shot['segments']:
        chosen = segment['cast_inputs']['scene_anchors']
        chosen[:] = [value for value in chosen if value not in prior]


def migrate_store():
    """Upgrade live input roles; keep immutable takes, guides and manual text."""
    with store.LOCK:
        if store.setting('timed_guide_inputs_schema', 0) >= 1:
            return
        pending = {j['clip_id'] for j in store.pending_jobs(('queued', 'waiting', 'running', 'cancelling'))}
        for clip in store.clips():
            if not any('cast_setup' in s for s in clip.get('shots', [])):
                continue
            if clip['id'] in pending:
                raise store.Conflict('Finish active clip jobs before upgrading its guide inputs')
            def change(c):
                for shot in c['shots']:
                    if 'cast_setup' not in shot:
                        continue
                    for segment in shot['segments']:
                        segment['cast_inputs']['scene_anchors'] = []
                        custom = segment.get('guide_prompt', '').strip()
                        if segment.get('render_mode') == 'swap_pika' and custom:
                            segment.update(performance_prompt=custom, prompt_mode='manual')
            store.clip_mutate(clip['id'], change, clip['revision'])
        store.set_setting('timed_guide_inputs_schema', 1)


def execute(job, directory):
    from . import guide_provider
    options = job['data']['options']
    guide = options['guide']
    edit_source = guide.get('edit_source_asset') or guide['source_asset']
    inputs = [store.path(edit_source), *[store.path(key) for key in guide['references']]]
    # Frozen at submission; historical attempts retain their exact input text.
    prompt = options['full_prompt'] if 'full_prompt' in options else guide_prompts.compose(guide)
    provider = options.get('provider', 'comfy')
    if provider == 'local-h3-image':
        from . import local_h3_image
        output = local_h3_image.run(job, directory, prompt, inputs)
        model = 'H3 Image Edit'
    elif provider == 'local-openai':
        from . import local_openai
        output = local_openai.run(job, directory, prompt, guide['quality'], inputs, options.get('image_size'))
        model = local_openai.MODEL
    elif provider == 'private-openai':
        from . import guide_gateway
        output = guide_gateway.run(job, directory, prompt, guide['quality'], inputs, options.get('image_size'))
        model = guide_gateway.MODEL
    else:
        output = guide_provider.run(job, directory,
            lambda names: guide_provider.image_graph(prompt, guide['quality'], names, 'h3-guides/' + job['id']), inputs, paid=True)
        model = guide_provider.MODEL
    raw = store.asset_add(output)
    with Image.open(output) as image:
        width, height = image.size
        if abs((width / height) / (guide['dimensions'][0] / guide['dimensions'][1]) - 1) > .05:
            note = 'Framing differs from the source. Review before use.'
        else:
            note = ''
    version = {'id': job['id'], 'asset': raw, 'created': store.now(), 'model': model, 'provider': provider,
               'source_asset': edit_source, 'original_source_asset': guide['source_asset'],
               'edit_model': guide.get('edit_model', 'gpt-image'),
               'requested_size': options.get('image_size', 'auto'), 'dimensions': [width, height],
               'prompt': guide['prompt'], 'helpers': copy.deepcopy(guide.get('helpers', [])), 'full_prompt': prompt,
               'references': guide['references'], 'quality': guide['quality'], 'note': note}
    # Attach to its original marker even if its draft changed during generation.
    def attach(clip):
        shot = next((s for s in clip['shots'] if s['id'] == job['shot_id']), None)
        target = next((g for g in (shot or {}).get('guides', []) if g['id'] == guide['id']), None)
        if target and not any(v['id'] == version['id'] for v in target['versions']):
            target['versions'].append(copy.deepcopy(version))
            if (target.get('approved') == guide.get('approved')
                    and target['source_asset'] == guide['source_asset']
                    and target['enabled'] and not target.get('archived') and current(clip, shot, target)):
                baseline = next(g for g in job['data']['shot']['guides'] if g['id'] == guide['id'])
                # Preserve settings saved elsewhere while this image was running.
                for key in ('prompt', 'quality', 'references', 'helpers', 'edit_source_asset', 'edit_model'):
                    if key in guide and target.get(key) == baseline.get(key):
                        target[key] = copy.deepcopy(guide[key])
                target.update(approved=version['id'], auto_saved_version=version['id'])
                sync_reference(clip, shot, target)
    store.clip_mutate(job['clip_id'], attach)
    return {'asset': raw}
