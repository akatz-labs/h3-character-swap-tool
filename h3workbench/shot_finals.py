"""Explicit, immutable shot finals; export choices never modify editor selections."""
import copy
import re
from PIL import Image, ImageOps
from . import media, segments, segment_jobs, store


def freeze(clip, shot, options):
    if not shot:
        raise ValueError('Choose a shot')
    if set(options) - {'revision', 'request_id', 'take_ids', 'acknowledge_continuity'}:
        raise ValueError('Unknown final option')
    if not re.fullmatch(r'[A-Za-z0-9-]{8,80}', str(options.get('request_id', ''))):
        raise ValueError('A final request ID is required')
    if options.get('revision') != clip['revision']:
        raise store.Conflict('Shot changed; reopen Create final to review its takes')
    items = segments.current(shot)
    choices = options.get('take_ids')
    if not isinstance(choices, dict) or set(choices) != {s['id'] for s in items}:
        raise ValueError('Choose one take for every segment')
    warnings = []
    for index, seg in enumerate(items):
        chosen = segments.take(seg, choices[seg['id']])
        if not choices[seg['id']] or not chosen:
            raise ValueError(f'Choose a saved take for segment {index + 1}')
        candidate = {**seg, 'selected_take': chosen['id']}
        if not segments.compatible_take(clip, shot, candidate):
            raise ValueError(f'Segment {index + 1} take does not match the source, frames or resolution')
        # Admission verifies artifacts exist; no regeneration is triggered here.
        for key in ('master', 'asset'):
            if not store.path(chosen[key]).is_file():
                raise ValueError(f'Segment {index + 1} media is unavailable')
        if any(choices.get(d['segment_id']) != d['take_id'] for d in chosen.get('dependencies', [])):
            warnings.append(seg['id'])
    if warnings and options.get('acknowledge_continuity') is not True:
        raise ValueError('These takes use different preceding history; acknowledge the continuity warning')
    return {**copy.deepcopy(options), 'continuity_warnings': warnings}


def queue(clip_id, shot_id, body):
    """Idempotency survives completion/reloads and rejects changed request content."""
    from . import pipeline
    with store.LOCK:
        with store.db() as db:
            rows = db.execute("SELECT id FROM jobs WHERE kind='shot_final' AND clip_id=? AND shot_id=?",
                              (clip_id, shot_id)).fetchall()
        for row in rows:
            job = store.job_get(row['id'])
            old = job['data']['options']
            if old.get('request_id') == body.get('request_id'):
                if {k: v for k, v in old.items() if k != 'continuity_warnings'} != body:
                    raise store.Conflict('This final request ID was already used with different choices')
                return job
            if job['state'] in {'queued', 'running', 'waiting', 'cancelling'}:
                raise store.Conflict('A final is already being created for this shot')
        return store.job_get(pipeline.queue('shot_final', clip_id, shot_id, options=body))


def execute(job, directory):
    from . import pipeline
    clip = copy.deepcopy(job['data']['snapshot'])
    shot = copy.deepcopy(job['data']['shot'])
    choices = job['data']['options']['take_ids']
    items = segments.current(shot)
    for seg in items:
        seg['selected_take'] = choices[seg['id']]
    shot['segments'] = items
    # Always produce a new final, even if the legacy auto-assembly used these takes.
    shot['renders'] = []
    result = segment_jobs.assemble(clip, shot, directory)
    if not result:
        raise ValueError('The selected takes cannot be assembled')
    selected = [segments.take(s) for s in items]
    result.update(id=job['id'], start=0, end=shot['end'] - shot['start'],
                  source=clip.get('render_source') or clip['source'], shot_start=shot['start'],
                  continuity_warnings=job['data']['options'].get('continuity_warnings', []),
                  segment_takes=[{'segment_id': s['id'], 'take_id': t['id'],
                                  'label': f"Take {s['takes'].index(t) + 1}"} for s, t in zip(items, selected)])
    result['settings'] = {'segments': [dict(copy.deepcopy(t.get('settings', {})), id=seg['id'],
        start=t['start'], end=t['end'], selected_take=t['id'], prompt=t.get('prompt', ''))
        for seg, t in zip(items, selected)]}
    source = media.shot_media(clip, shot, directory, dimensions=selected[0]['dimensions'])
    movie = store.path(result['asset'])
    comparison = directory / 'side-by-side.mp4'
    def compare(inputs, graph, target):
        media.ff([*inputs, '-filter_complex', graph, '-map', '[v]', '-map', '1:a?',
                  '-c:v', 'libx264', '-preset', 'fast', '-crf', '18', '-pix_fmt', 'yuv420p',
                  '-c:a', 'aac', '-b:a', '192k', '-t', str(result['end'] / 24),
                  '-movflags', '+faststart', target])
        if media.probe(target)['frames'] != result['end']:
            raise ValueError('Comparison does not match the exact shot duration')
        return store.asset_add(target)
    result['comparison'] = compare(['-i', source, '-i', movie], '[0:v][1:v]hstack=inputs=2[v]', comparison)
    refs = list(dict.fromkeys(v['reference'] for t in selected
                            for v in (t.get('cast_inputs_snapshot') or {}).get('characters', [])
                            if v.get('reference')))
    if not refs:
        refs = list(dict.fromkeys(t['reference'] for t in selected if t.get('reference')))
    if refs:
        width, height = selected[0]['dimensions']
        strip_width = max(64, (width // 3) // 2 * 2)
        strip = Image.new('RGB', (strip_width, height), '#141c24')
        for i, ref in enumerate(refs):
            top, bottom = round(height * i / len(refs)), round(height * (i + 1) / len(refs))
            with Image.open(store.path(ref)) as image:
                thumb = ImageOps.contain(image.convert('RGB'), (strip_width, max(1, bottom - top)))
                strip.paste(thumb, ((strip_width - thumb.width) // 2, top + (bottom - top - thumb.height) // 2))
        path = directory / 'references.png'; strip.save(path)
        result['comparison_reference'] = compare(['-i', source, '-i', movie, '-loop', '1', '-framerate', '24', '-i', path],
            '[0:v][2:v][1:v]hstack=inputs=3:shortest=1[v]', directory / 'with-references.mp4')
    pipeline.check_finalization_cancel(job)
    def attach(_clip, current, _stable):
        if current is not None and not any(v['id'] == result['id'] for v in current.get('finals', [])):
            current.setdefault('finals', []).append(result)
    pipeline.attach(job, attach)
    return result


def attempt(clip_id, shot_id, job_id):
    """Only generation provenance is returned, never a worker/configuration snapshot."""
    job = store.job_get(job_id)
    if (job['clip_id'], job['shot_id']) != (clip_id, shot_id):
        raise KeyError('Attempt does not belong to this shot')
    data = job['data']; shot = data.get('shot') or {}
    keys = {'id', 'start', 'end', 'seed', 'prompt', 'performance_prompt', 'render_mode', 'use_mask',
            'use_source_audio', 'lora_id', 'history_frames', 'connection'}
    items = (data.get('segment_plan') or {}).get('segments', shot.get('segments', []))
    scope = {s['id'] for s in data.get('segment_scope', [])}
    items = [s for s in items if not scope or s['id'] in scope]
    from . import cast, guides
    snapshot = data.get('snapshot') or {}
    presented = []
    for seg in items:
        record = {k: v for k, v in seg.items() if k in keys}
        record['performance_prompt'] = segments.prompt(snapshot, shot, seg)
        if 'cast_setup' in shot and seg.get('render_mode') != 'guides':
            record['cast_inputs_snapshot'] = cast.generation_inputs(snapshot, shot, seg, strict=False)
        record['guide_inputs_snapshot'] = {'shot_start': shot['start'],
            'guides': guides.selected(snapshot, shot, seg, required=False)}
        presented.append(record)
    return {'shot': {k: v for k, v in shot.items() if k in keys},
            'segments': presented,
            'generation': data.get('generation', {}), 'render_spec': data.get('render_spec', {}),
            'options': {'model_offloading': (data.get('options') or {}).get('model_offloading')},
            'cast_inputs': data.get('cast_inputs_snapshot') or {}, 'guides': []}
