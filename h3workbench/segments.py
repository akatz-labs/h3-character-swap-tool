"""Editable generation segments, exact source ranges and take dependencies.

This module owns plans, not worker processes. All writes use the clip revision.
"""
import copy
import hashlib
import json
import re
from . import media, render_models, store

EDITABLE = {'prompt', 'seed', 'connection', 'history_frames', 'anchor_mode', 'anchor'}
FIELDS = EDITABLE | {'id', 'start', 'end', 'anchors', 'takes', 'selected_take', 'needs_review'}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def current(shot):
    if shot.get('segments') is not None:
        return copy.deepcopy(shot['segments'])
    result = []
    for w in render_models.window_plan(shot)['windows']:
        a, b = w['source_start'] + w['discarded_overlap'], w['source_end_exclusive']
        result.append(new(shot, a, b, len(result), key=f"segment-{shot['id']}-{a}-{b}"))
    return result


def new(shot, start, end, index, key=None):
    return {'id': key or store.uid('segment-'), 'start': start, 'end': end,
            'connection': 'continue' if index else 'start',
            'history_frames': shot.get('history_frames', 18), 'prompt': '',
            'seed': (shot.get('seed', 194729) + index) % (2**63),
            'anchor_mode': 'shot' if not index and shot.get('use_anchor', True) else 'none', 'anchor': None,
            'anchors': [], 'takes': [], 'selected_take': None, 'needs_review': False}


def validate(shot, items):
    if not isinstance(items, list) or not 1 <= len(items) <= 1000:
        raise ValueError('Choose between 1 and 1000 segments')
    covered, chain_start, ids = 0, 0, set()
    for i, item in enumerate(items):
        if not isinstance(item, dict) or set(item) - FIELDS:
            raise ValueError('Unknown segment setting')
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
        needed = b-a + bool(h)
        if not any(v >= needed and v > h for v in render_models.WINDOW_FRAMES):
            raise ValueError('A segment must fit one diffusion pass (at most 362 frames, including its continuation boundary)')
        if item.get('anchor_mode') not in {'shot', 'custom', 'none'}:
            raise ValueError('Choose the shot anchor, a segment reference, or no anchor')
        if not isinstance(item.get('prompt'), str) or len(item['prompt']) > 20000:
            raise ValueError('Segment directions must be text of at most 20,000 characters')
        if type(item.get('seed')) is not int or not 0 <= item['seed'] < 2**63:
            raise ValueError('Enter a whole-number seed of 0 or greater')
        if item['anchor_mode'] == 'custom' and item.get('anchor') not in [a['asset'] for a in item.get('anchors', [])]:
            raise ValueError('Choose an imported or generated reference for this segment')
        covered = b
    if covered != shot['end'] - shot['start']:
        raise ValueError('Segments must cover the entire shot')
    return items


def effective_history(segment):
    return segment['history_frames'] if segment['connection'] == 'continue' else 0


def anchor(shot, segment):
    return shot.get('anchor') if segment['anchor_mode'] == 'shot' else segment.get('anchor') if segment['anchor_mode'] == 'custom' else None


def dialogue(clip, shot, segment):
    return media.dialogue(clip, shot['start'] + segment['start'], shot['start'] + segment['end'])


def prompt(clip, shot, segment):
    shared = shot.get('prompt', '')
    # Only remove our own generated full-shot dialogue and anchor boilerplate.
    old = f'Current shot dialogue: "{shot.get("dialogue", "")}".'
    shared = shared.replace(old, '').replace('<Picture 2> is the exact first-frame scene and pose anchor.', '').strip()
    h, ref = effective_history(segment), anchor(shot, segment)
    context = '<Picture 2> is the generated boundary from the preceding segment. Continue its motion and appearance.' if h else (
        '<Picture 2> is the exact first-frame scene and pose anchor.' if ref else '')
    if h and ref:
        context += ' <Picture 3> is an additional scene and performance reference for this segment; preserve continuity with the generated boundary.'
    words = dialogue(clip, shot, segment)
    return '\n\n'.join(p for p in [shared, context, f'Current segment dialogue: "{words}". Follow source audio timing. Earlier reference dialogue is context only; do not repeat it.', segment['prompt'].strip()] if p)


def setting_signature(clip, shot, segment):
    return digest({'source': clip.get('render_source') or clip['source'], 'reference': clip.get('reference'),
        'shot_start': shot['start'], 'segment': {k: segment[k] for k in ['id', 'start', 'end', 'connection', 'history_frames', 'seed']},
        'prompt': prompt(clip, shot, segment), 'anchor': anchor(shot, segment),
        'generation': render_models.settings(shot), 'render_mp': shot.get('render_mp', 1),
        'mask': shot.get('mask') if shot.get('use_mask', True) else None, 'expansion': shot.get('expansion', 64)})


def take(segment, key=None):
    return next((t for t in segment.get('takes', []) if t['id'] == (key or segment.get('selected_take'))), None)


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
    for i, seg in enumerate(items):
        chosen = take(seg)
        deps = dependencies(items, i)
        blocked = any(not d['take_id'] for d in deps)
        stale_parent = any(not result[next(j for j, s in enumerate(items) if s['id'] == d['segment_id'])]['current'] for d in deps)
        ready = bool(chosen and chosen.get('signature') == setting_signature(clip, shot, seg)
                     and chosen.get('dependencies', []) == deps and not stale_parent)
        result.append({'id': seg['id'], 'current': ready, 'status': 'ready' if ready else 'stale' if chosen else 'empty',
            'blocked': blocked or stale_parent, 'dialogue': dialogue(clip, shot, seg),
            'prompt': prompt(clip, shot, seg), 'dependencies': deps,
            'window_frames': next(v for v in render_models.WINDOW_FRAMES if v >= seg['end']-seg['start']+bool(effective_history(seg)) and v > effective_history(seg))})
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
        if type(frames) is not int or not 1 <= frames <= 361:
            raise ValueError('Choose a segment length of at most 15 seconds')
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
        seg = new(shot,a,b,i)
        if overlaps:
            first = overlaps[0]
            seg.update({k: copy.deepcopy(first[k]) for k in ['seed','history_frames','prompt']})
            if a == first['start']:
                seg.update({k:copy.deepcopy(first[k]) for k in ['connection','anchor_mode','anchor','anchors']})
            seg['prompt'] = '\n\n'.join(dict.fromkeys(s['prompt'] for s in overlaps if s['prompt'].strip()))
            seg['needs_review'] = bool(seg['prompt'])
        result.append(seg)
    # A split cannot invent history before the sequence. Keep a supported amount.
    chain = 0
    for i, seg in enumerate(result):
        if not i: seg['connection'] = 'start'
        if seg['connection'] == 'start': chain = seg['start']
        elif seg['history_frames'] > seg['start']-chain:
            seg['history_frames'] = max(n for n in render_models.HISTORY_FRAMES if n <= seg['start']-chain)
    return validate(shot,result)


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
        seg.update(changes); seg['needs_review'] = False
        validate(shot,items); shot['segments'] = items
    return store.clip_mutate(clip_id,change,revision)
