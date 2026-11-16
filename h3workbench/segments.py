"""Editable generation segments, exact source ranges and take dependencies.

This module owns plans, not worker processes. All writes use the clip revision.
"""
import copy
import hashlib
import json
import re
from . import media, render_models, store
from worker_nodes.h3_workbench_runtime.segment_planning import plan_segment

PROMPT_FIELDS = {'performance_prompt', 'dialogue', 'speaker'}
RENDER_FIELDS = {'lora_id', 'use_mask', 'use_source_audio'}
EDITABLE = {'prompt', 'seed', 'connection', 'history_frames', 'anchor_mode', 'anchor', 'cast_inputs', 'render_mode', 'guide_prompt'} | PROMPT_FIELDS | RENDER_FIELDS
FIELDS = EDITABLE | {'id', 'start', 'end', 'anchors', 'takes', 'selected_take', 'needs_review', 'cast_prompt_signature', 'prompt_mode'}


def uses_guides(segment):
    return segment.get('render_mode') in {'guides', 'swap_pika'}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def render_shot(shot, segment):
    """Segment overrides share the shot canvas and immutable source/mask assets."""
    return {**shot, **{k: segment[k] for k in RENDER_FIELDS if segment.get(k) is not None}}


def current(shot):
    if shot.get('segments') is not None:
        items = copy.deepcopy(shot['segments'])
        for item in items:
            item.setdefault("cast_inputs", None)
            item.setdefault("render_mode", "swap")
            item.setdefault("guide_prompt", "")
            for key in PROMPT_FIELDS:
                item.setdefault(key, None)
        return items
    result = []
    for w in render_models.window_plan(shot)['windows']:
        a, b = w['source_start'] + w['discarded_overlap'], w['source_end_exclusive']
        result.append(new(shot, a, b, len(result), key=f"segment-{shot['id']}-{a}-{b}"))
    return result


def new(shot, start, end, index, key=None):
    return {'id': key or store.uid('segment-'), 'start': start, 'end': end,
            'connection': 'continue' if index else 'start',
            'history_frames': shot.get('history_frames', 18), 'prompt': '',
            'performance_prompt': None, 'dialogue': None, 'speaker': None,
            'cast_inputs': {'characters': [], 'mask_tracks': [], 'scene_anchors': [], 'mask_assignments': {}} if 'cast_setup' in shot else None,
            'render_mode': 'swap', 'guide_prompt': '',
            'seed': (shot.get('seed', 194729) + index) % (2**63),
            'anchor_mode': 'shot' if not index and shot.get('use_anchor', True) else 'none', 'anchor': None,
            'anchors': [], 'takes': [], 'selected_take': None, 'needs_review': False, 'prompt_mode': 'auto'}


def validate(shot, items):
    if not isinstance(items, list) or not 1 <= len(items) <= 1000:
        raise ValueError('Choose between 1 and 1000 segments')
    covered, chain_start, ids = 0, 0, set()
    for i, item in enumerate(items):
        if not isinstance(item, dict) or set(item) - FIELDS:
            raise ValueError('Unknown segment setting')
        if any(k in item and item[k] is None for k in RENDER_FIELDS):
            raise ValueError('Choose a valid segment render setting')
        render_models.settings(render_shot(shot, item))
        if item.get('render_mode') is not None and (not isinstance(item['render_mode'], str) or item['render_mode'] not in {'swap', 'guides', 'swap_pika'}):
            raise ValueError('Choose Character swap or Guided edit')
        if item.get('guide_prompt') is not None and (not isinstance(item['guide_prompt'], str) or len(item['guide_prompt']) > 12000):
            raise ValueError('Guided render prompt must be at most 12,000 characters')
        key = item.get('id')
        if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', key) or key in ids:
            raise ValueError('Segment identifiers must be unique')
        ids.add(key)
        a, b = item.get('start'), item.get('end')
        if type(a) is not int or type(b) is not int or a != covered or b <= a:
            raise ValueError('Segments must cover the shot in order, without gaps or overlaps')
        if item.get('connection') not in {'start', 'continue'} or (not i and item['connection'] != 'start'):
            raise ValueError('The first segment starts fresh; later segments can continue or start fresh')
        if item['connection'] == 'start':
            chain_start = a
        history = item.get('history_frames')
        if type(history) is not int or history not in render_models.HISTORY_FRAMES:
            raise ValueError('Choose a supported continuation history')
        h = history if item['connection'] == 'continue' else 0
        if h > a - chain_start:
            raise ValueError('History extends before this continuous sequence; reduce history or move the boundary')
        if item.get('anchor_mode') not in {'shot', 'custom', 'none'}:
            raise ValueError('Choose the shot anchor, a segment reference, or no anchor')
        if not isinstance(item.get('prompt'), str) or len(item['prompt']) > 20000:
            raise ValueError('Segment directions must be text of at most 20,000 characters')
        for field in ('performance_prompt', 'dialogue'):
            value = item.get(field)
            if value is not None and (not isinstance(value, str) or len(value) > 20000):
                raise ValueError(f'Segment {field} must be text of at most 20,000 characters')
        if item.get('prompt_mode') not in (None, 'auto', 'manual'):
            raise ValueError('Choose automatic or manual prompt text')
        if item.get('speaker') not in (None, 'target', 'other', 'offscreen', 'none'):
            raise ValueError('Choose a supported segment speaker')
        if type(item.get('seed')) is not int or not 0 <= item['seed'] < 2**63:
            raise ValueError('Enter a whole-number seed of 0 or greater')
        if item['anchor_mode'] == 'custom' and item.get('anchor') not in [a['asset'] for a in item.get('anchors', [])]:
            raise ValueError('Choose an imported or generated reference for this segment')
        if 'cast_setup' in shot:
            from . import cast
            cast.validate_selection(shot['cast_setup'], item.get('cast_inputs'))
            if shot.get('segment_masks_version') == 1:
                selected = set(item['cast_inputs']['mask_tracks'])
                if any(t['id'] in selected and t.get('segment_id') != item['id'] for t in shot['cast_setup']['mask_tracks']):
                    raise ValueError('Select masks belonging to this segment')
        covered = b
    if covered != shot['end'] - shot['start']:
        raise ValueError('Segments must cover the entire shot')
    return items


def effective_history(segment):
    return segment['history_frames'] if segment['connection'] == 'continue' else 0


def anchor(shot, segment):
    return shot.get('anchor') if segment['anchor_mode'] == 'shot' else segment.get('anchor') if segment['anchor_mode'] == 'custom' else None


def dialogue(clip, shot, segment):
    if segment.get('dialogue') is not None:
        return segment['dialogue']
    return media.dialogue(clip, shot['start'] + segment['start'], shot['start'] + segment['end'])


def speaker(shot, segment):
    return segment.get('speaker') or shot.get('speaker', 'target')


def reference_prompt(shot, segment):
    h, ref = effective_history(segment), anchor(shot, segment)
    context = '<Picture 2> is the generated boundary from the preceding segment. Continue its motion and appearance.' if h else (
        media.reference_prompt(media.FIRST_FRAME_ANCHOR, shot) if ref else '')
    if h and ref:
        context += ' <Picture 3> is an additional scene and performance reference for this segment; preserve continuity with the generated boundary.'
    return context


def build_prompt(clip, shot, segment):
    """Explicit Update prompt action: create the complete, editable model text."""
    if "cast_setup" in shot:
        from . import cast
        return cast.build_prompt(clip, shot, segment)
    virtual = {**shot, 'dialogue': dialogue(clip, shot, segment), 'speaker': speaker(shot, segment)}
    text = media.prompt(clip, virtual).replace(
        media.reference_prompt(media.FIRST_FRAME_ANCHOR, shot), reference_prompt(shot, segment))
    text = text.replace('Current shot dialogue:', 'Current segment dialogue:')
    return '\n\n'.join(p for p in [text, segment['prompt'].strip()] if p)


def prompt(clip, shot, segment):
    if segment.get('render_mode') == 'guides':
        from .guides import PROPAGATION
        return segment.get('guide_prompt') or PROPAGATION
    if 'cast_setup' in shot:
        if prompt_mode(segment) == 'auto':
            return build_prompt(clip, shot, segment)
        return segment['performance_prompt'] if segment.get('performance_prompt') is not None else shot.get('prompt', '')
    if segment.get('performance_prompt') is not None:
        return media.reference_prompt(segment['performance_prompt'], shot)
    # Preserve the exact effective text of legacy takes until explicitly edited.
    shared = shot.get('prompt', '')
    old = f'Current shot dialogue: "{shot.get("dialogue", "")}".'
    shared = shared.replace(old, '').replace(media.FIRST_FRAME_ANCHOR, '').replace(media.REFERENCE_ANCHOR, '').strip()
    context = reference_prompt(shot, segment)
    words = dialogue(clip, shot, segment)
    return '\n\n'.join(p for p in [shared, context, f'Current segment dialogue: "{words}". Follow source audio timing. Earlier reference dialogue is context only; do not repeat it.', segment['prompt'].strip()] if p)


def prompt_mode(segment):
    # Older projects did not record authorship. Preserve their saved text.
    return segment.get('prompt_mode') or ('manual' if segment.get('performance_prompt') is not None else 'auto')


def refresh_auto_prompts(clip):
    """Keep owned prompt text in sync at the clip write boundary, never on reads."""
    from . import cast
    for shot in clip.get('shots', []):
        if 'cast_setup' not in shot:
            continue
        for segment in shot['segments']:
            if segment.get('render_mode') == 'guides' or prompt_mode(segment) != 'auto':
                continue
            segment['performance_prompt'] = build_prompt(clip, shot, segment)
            segment['cast_prompt_signature'] = cast.prompt_signature(clip, shot, segment)
            segment['needs_review'] = False


def setting_signature(clip, shot, segment):
    if uses_guides(segment):
        from . import guides
        return guides.signature(clip, shot, segment)
    shot = render_shot(shot, segment)
    generation = render_models.settings(shot)
    # Enabled is the historical behavior. Keep existing takes current when
    # this new control is absent or explicitly on.
    if generation["use_source_audio"]:
        generation.pop("use_source_audio")
    if 'cast_setup' in shot:
        from . import cast, guides
        return digest({'inputs': cast.generation_inputs(clip, shot, segment, strict=False),
                       'timed_guides': guides.selected(clip, shot, segment),
                       'generation': generation, 'render_mp': shot.get('render_mp', 1),
                       'seed': segment['seed'], 'prompt': prompt(clip, shot, segment)})
    return digest({'source': clip.get('render_source') or clip['source'], 'reference': media.reference(clip, shot),
        'shot_start': shot['start'], 'segment': {k: segment[k] for k in ['id', 'start', 'end', 'connection', 'history_frames', 'seed']},
        'prompt': prompt(clip, shot, segment), 'anchor': anchor(shot, segment),
        'generation': generation, 'render_mp': shot.get('render_mp', 1),
        'mask': shot.get('mask') if shot.get('use_mask', True) else None, 'expansion': shot.get('expansion', 64)})


def take(segment, key=None):
    return next((t for t in segment.get('takes', []) if t['id'] == (key or segment.get('selected_take'))), None)


def current_takes(clip, shot, items=None):
    """Saved outputs are retained; continuation requires an unchanged dependency chain."""
    items = current(shot) if items is None else items
    result = {}
    for i, seg in enumerate(items):
        parents = predecessors(items, i)
        if any(p['id'] not in result for p, _ in parents):
            continue
        deps = [{'segment_id': p['id'], 'take_id': result[p['id']]['id'], 'frames': n} for p, n in parents]
        sig = setting_signature(clip, shot, seg)
        candidates = list(reversed(seg.get('takes', [])))
        selected = take(seg)
        if selected:
            candidates.insert(0, selected)
        for t in candidates:
            inputs = t.get('guide_inputs_snapshot') or t.get('cast_inputs_snapshot')
            if (t.get('master') and (t.get('start'), t.get('end')) == (seg['start'], seg['end'])
                    and t.get('dimensions') == media.render_spec(clip, shot)['dimensions']
                    and (not inputs or (inputs['source'] == (clip.get('render_source') or clip['source']) and inputs['shot_start'] == shot['start']))
                    and t.get('signature') == sig and t.get('dependencies', []) == deps):
                result[seg['id']] = t
                break
    return result


def compatible_take(clip, shot, segment):
    """Existing finals may use saved pixels even when regeneration is needed."""
    t = take(segment)
    if not t or not t.get('master') or (t.get('start'), t.get('end')) != (segment['start'], segment['end']):
        return False
    inputs = t.get('guide_inputs_snapshot') or t.get('cast_inputs_snapshot')
    return (t.get('dimensions') == media.render_spec(clip, shot)['dimensions']
            and (not inputs or (inputs['source'] == (clip.get('render_source') or clip['source']) and inputs['shot_start'] == shot['start'])))


def reusable_take(clip, shot, segment):
    return segment['id'] in current_takes(clip, shot)


def predecessors(items, index):
    """Exact tail contributors, oldest first; never cross a fresh start."""
    needed = effective_history(items[index])
    result = []
    for item in reversed(items[:index]):
        if not needed:
            break
        n = min(needed, item['end'] - item['start'])
        result.append((item, n)); needed -= n
        if item['connection'] == 'start':
            break
    if needed:
        raise ValueError('Not enough preceding frames in this continuous sequence')
    return list(reversed(result))


def dependencies(items, index):
    return [{'segment_id': item['id'], 'take_id': item.get('selected_take'), 'frames': n}
            for item, n in predecessors(items, index)]


def states(clip, shot, items=None):
    items = current(shot) if items is None else items
    result = []
    valid = current_takes(clip, shot, items)
    for i, seg in enumerate(items):
        chosen = valid.get(seg['id'])
        parents = predecessors(items, i)
        deps = [{'segment_id': p['id'], 'take_id': valid.get(p['id'], {}).get('id'), 'frames': n} for p, n in parents]
        blocked = any(p['id'] not in valid for p, _ in parents)
        ready = bool(chosen)
        cast_state = {}
        if 'cast_setup' in shot:
            from . import cast
            cast_state = {'pictures': cast.reference_plan(clip, shot, seg, strict=False),
                          'prompt_mode': prompt_mode(seg),
                          'prompt_needs_update': seg.get('render_mode') != 'guides' and prompt_mode(seg) == 'auto' and seg.get('cast_prompt_signature') != cast.prompt_signature(clip, shot, seg)}
        result.append({**cast_state, 'id': seg['id'], 'current': ready, 'status': 'ready' if ready else 'stale' if seg.get('takes') else 'empty',
            'reusable': ready, 'blocked': blocked, 'dialogue': dialogue(clip, shot, seg),
            'prompt': prompt(clip, shot, seg), 'speaker': speaker(shot, seg), 'dependencies': deps,
            'window_frames': plan_segment(seg['end']-seg['start']+effective_history(seg), effective_history(seg))[0]['window_frames']})
    return result


def plan_revision(shot):
    return digest(current(shot))


def view(clip, shot):
    items = validate(shot, current(shot))
    return {'segments': items, 'states': states(clip, shot, items), 'revision': clip['revision'],
            'plan_revision': plan_revision(shot), 'history_choices': list(render_models.HISTORY_FRAMES)}


def layout(shot, operation):
    old = current(shot); end = shot['end']-shot['start']
    bounds = [0, *[s['end'] for s in old]]
    op = operation.get('operation')
    if op == 'partition':
        frames = operation.get('frames')
        if type(frames) is not int or frames < 1:
            raise ValueError('Choose a positive whole-frame segment length')
        bounds = [*range(0, end, frames), end]
    elif op == 'split':
        frame = operation.get('frame')
        if type(frame) is not int or not 0 < frame < end or frame in bounds:
            raise ValueError('Place the playhead inside a segment to split it')
        bounds = sorted([*bounds, frame])
    elif op in {'merge', 'boundary'}:
        index = operation.get('index')
        if type(index) is not int or not 1 <= index < len(old):
            raise ValueError('Choose a boundary between segments')
        if op == 'merge':
            bounds.pop(index)
        else:
            frame = operation.get('frame')
            if type(frame) is not int or not bounds[index-1] < frame < bounds[index+1]:
                raise ValueError('The boundary must stay between its neighboring boundaries')
            bounds[index] = frame
    else:
        raise ValueError('Unknown segment layout operation')
    result = []
    for i, (a,b) in enumerate(zip(bounds,bounds[1:])):
        exact = next((s for s in old if (s['start'],s['end']) == (a,b)), None)
        if exact:
            result.append(copy.deepcopy(exact)); continue
        overlaps = [s for s in old if s['end'] > a and s['start'] < b]
        # Moving a boundary changes duration, not either segment's cast or prompt.
        if op == 'boundary':
            overlaps = [old[i]]
        seg = new(shot,a,b,i)
        if overlaps:
            first = overlaps[0]
            seg['render_mode'] = first.get('render_mode') or 'swap'
            seg['guide_prompt'] = first.get('guide_prompt') or ''
            seg.update({k: copy.deepcopy(first[k]) for k in ['seed','history_frames','prompt']})
            seg.update({k: copy.deepcopy(first[k]) for k in RENDER_FIELDS if k in first})
            if a == first['start']:
                seg.update({k:copy.deepcopy(first[k]) for k in ['connection','anchor_mode','anchor','anchors']})
            seg['prompt'] = '\n\n'.join(dict.fromkeys(s['prompt'] for s in overlaps if s['prompt'].strip()))
            for key in ('performance_prompt', 'dialogue'):
                values = list(dict.fromkeys(s[key] for s in overlaps if s.get(key) is not None))
                if values:
                    seg[key] = '\n\n'.join(values)
            seg['speaker'] = first.get('speaker')
            seg['prompt_mode'] = 'manual' if any(prompt_mode(s) == 'manual' for s in overlaps) else 'auto'
            seg['needs_review'] = bool(seg['prompt']) or any(seg[k] is not None for k in PROMPT_FIELDS)
        if 'cast_setup' in shot:
            from . import cast
            cast_sources = overlaps if op in {'merge', 'boundary'} or (overlaps and a == overlaps[0]['start']) else []
            seg['cast_inputs'] = {key: list(dict.fromkeys(v for old_seg in cast_sources for v in old_seg['cast_inputs'][key])) for key in cast.COLLECTIONS}
            if any('mask_assignments' in item['cast_inputs'] for item in cast_sources):
                assignments = {}
                for old_seg in cast_sources:
                    for character, masks in cast.character_masks(old_seg['cast_inputs']).items():
                        assignments[character] = list(dict.fromkeys(assignments.get(character, []) + masks))
                cast.apply_character_masks(seg['cast_inputs'], assignments)
            seg['cast_prompt_signature'] = None
            seg['needs_review'] = True
        result.append(seg)
    # A split cannot invent history before the sequence. Keep a supported amount.
    chain = 0
    for i, seg in enumerate(result):
        if not i: seg['connection'] = 'start'
        if seg['connection'] == 'start': chain = seg['start']
        elif seg['history_frames'] > seg['start']-chain:
            seg['history_frames'] = max(n for n in render_models.HISTORY_FRAMES if n <= seg['start']-chain)
    # Carried mask IDs are rebound after the new layout is confirmed.
    return validate({**shot, 'segment_masks_version': 0},result)


def apply_layout(clip_id, shot_id, revision, operation):
    from .pipeline import shot_of
    def change(c):
        shot = shot_of(c,shot_id)
        items = layout(shot,operation)
        if items == current(shot): return
        if any(j['clip_id'] == clip_id and j['shot_id'] == shot_id for j in store.pending_jobs(('queued','waiting','running','cancelling'))):
            raise store.Conflict('Wait for this shot’s active jobs before applying a new segment layout')
        shot.setdefault('segment_layout_history',[]).append({'id':store.uid('segments-'), 'created':store.now(),
            'segments':current(shot), 'selected_render':shot.get('selected_render')})
        if shot.get('segment_masks_version') == 1:
            from . import segment_masks
            segment_masks.rebind_layout(shot, items)
        shot['segments'] = items
        shot['selected_render'] = None
        c['result'] = None
    return store.clip_mutate(clip_id,change,revision)


def edit(clip_id, shot_id, segment_id, revision, changes):
    from .pipeline import shot_of
    if not isinstance(changes,dict) or not set(changes) <= EDITABLE:
        raise ValueError('Unknown segment field')
    def change(c):
        shot = shot_of(c,shot_id); items = current(shot)
        seg = next((s for s in items if s['id'] == segment_id),None)
        if seg is None: raise ValueError('Segment no longer exists')
        if ({'dialogue', 'speaker'} & changes.keys() and 'performance_prompt' not in changes
                and ('cast_setup' not in shot or prompt_mode(seg) == 'manual')):
            # Preserve edited text while changing dialogue metadata.
            seg['performance_prompt'] = prompt(c, shot, seg)
        seg.update(changes); seg['needs_review'] = False
        if shot.get('segment_masks_version') == 1:
            seg['use_mask'] = bool(seg['cast_inputs']['mask_tracks'])
        if 'performance_prompt' in changes:
            seg['prompt_mode'] = 'manual'
        if 'cast_setup' in shot:
            from . import cast
            if 'performance_prompt' in changes:
                seg['cast_prompt_signature'] = cast.prompt_signature(c, shot, seg)
        validate(shot,items); shot['segments'] = items
    return store.clip_mutate(clip_id,change,revision)


def update_prompt(clip_id, shot_id, segment_id, revision):
    from .pipeline import shot_of
    def change(c):
        shot = shot_of(c, shot_id); items = current(shot)
        seg = next((s for s in items if s['id'] == segment_id), None)
        if seg is None: raise ValueError('Segment no longer exists')
        seg['performance_prompt'] = build_prompt(c, shot, seg)
        seg['prompt_mode'] = 'auto'
        if 'cast_setup' in shot:
            from . import cast
            seg['cast_prompt_signature'] = cast.prompt_signature(c, shot, seg)
        seg['needs_review'] = False
        validate(shot, items); shot['segments'] = items
    return store.clip_mutate(clip_id, change, revision)
