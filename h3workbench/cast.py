"""Shot cast, explicit segment inputs, prompt compilation and mask composition.

Asset IDs reuse the existing media store. Stored shots are migrated once; new shots use the same structure.
"""
import copy
import hashlib
import re

from . import masks, media, segments


COLLECTIONS = ('characters', 'mask_tracks', 'scene_anchors')
SELECTION_FIELDS = frozenset(COLLECTIONS)
LEGACY_CHARACTER = 'character-legacy'
LEGACY_MASK = 'mask-legacy'
MASK_COLORS = ('#83d9c4', '#db8bce', '#e9ac68', '#79b8ef', '#d8ce73', '#b3a0ef')
MASK_FIELDS = {
    'mask', 'mask_base', 'mask_versions', 'mask_points', 'mask_applied_points',
    'mask_coverage', 'mask_empty_frames', 'mask_approved', 'mask_text',
    'selection', 'subject_index', 'expansion',
}


def _id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', value):
        raise ValueError('Expected a stable cast, mask or anchor identifier')
    return value


def _text(value, label, limit=20000, empty=True):
    if not isinstance(value, str) or len(value) > limit or (not empty and not value.strip()):
        raise ValueError(f'Invalid {label}')
    return value


def _asset(value, optional=False):
    if value is None and optional:
        return
    if not isinstance(value, str) or not value:
        raise ValueError('Expected a saved asset identifier')


def anchor_id(asset):
    """Stable across legacy layout changes; importing the same asset reuses it."""
    return 'anchor-' + hashlib.sha256(asset.encode()).hexdigest()[:24]


def legacy_catalog(clip, shot):
    """Read-only compatibility view; even old segment anchors remain available."""
    character = {
        'id': LEGACY_CHARACTER, 'name': 'Character 1',
        'reference': media.reference(clip, shot),
        'description': media.character(clip, shot) or '',
    }
    state = {k: copy.deepcopy(v) for k, v in shot.items() if k in MASK_FIELDS}
    state.setdefault('expansion', 64)
    track = {'id': LEGACY_MASK, 'character_id': LEGACY_CHARACTER, 'state': state}
    anchors = {}
    records = [*shot.get('anchors', [])]
    layouts = [segments.current(shot), *[v["segments"] for v in shot.get("segment_layout_history", [])]]
    for segment in (s for layout in layouts for s in layout):
        records.extend(segment.get('anchors', []))
        for take in segment.get('takes', []):
            settings = take.get('settings', {})
            records.extend(settings.get('anchors', []))
            if settings.get('anchor'):
                records.append({'asset': settings['anchor']})
        if segment.get('anchor'):
            records.append({'asset': segment['anchor']})
    if shot.get('anchor'):
        records.append({'asset': shot['anchor']})
    for record in records:
        asset = record.get('asset')
        if not asset or asset in anchors:
            continue
        absolute = record.get('source_frame')
        relative = absolute - shot['start'] if type(absolute) is int else None
        if relative is not None and not 0 <= relative < shot['end'] - shot['start']:
            relative = None
        anchors[asset] = {
            'id': anchor_id(asset), 'name': f'Scene anchor {len(anchors) + 1}',
            'asset': asset, 'source_frame': relative,
            # Preserve historical provenance without guessing its source frame.
            'provenance': copy.deepcopy(record),
        }
    return {
        'version': 1, 'characters': [character], 'mask_tracks': [track],
        'scene_anchors': list(anchors.values()),
    }


def catalog(clip, shot):
    value = copy.deepcopy(shot['cast_setup']) if 'cast_setup' in shot else legacy_catalog(clip, shot)
    return validate_catalog(shot, value)


def validate_catalog(shot, value):
    if (not isinstance(value, dict) or set(value) != {'version', *COLLECTIONS}
            or type(value.get('version')) is not int or value['version'] != 1):
        raise ValueError('Unsupported shot cast format')
    ids = set()
    for collection in COLLECTIONS:
        rows = value[collection]
        if not isinstance(rows, list):
            raise ValueError(f'{collection} must be a list')
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError('Invalid cast entry')
            key = _id(row.get('id'))
            if key in ids:
                raise ValueError('Cast, mask and anchor identifiers must be unique')
            ids.add(key)
    character_ids = {c['id'] for c in value['characters']}
    frames = shot['end'] - shot['start']
    for character in value['characters']:
        if (set(character) - {'id', 'name', 'reference', 'description', 'project_character_id', 'project_character_revision'}
                or not {'id', 'name', 'reference', 'description'} <= set(character)):
            raise ValueError('Character cards contain a name, reference and description')
        if 'project_character_id' in character:
            _id(character['project_character_id'])
            if type(character.get('project_character_revision')) is not int or character['project_character_revision'] < 1:
                raise ValueError('Invalid project character revision')
        _text(character['name'], 'character name', 120, empty=False)
        _text(character['description'], 'character description')
        _asset(character['reference'], optional=True)
    owners = set()
    for track in value['mask_tracks']:
        if shot.get('segment_masks_version') == 1 and 'segment_id' not in track:
            raise ValueError('A mask must belong to a segment')
        if set(track) - {'id', 'character_id', 'state', 'name', 'color', 'segment_id', 'legacy_id'} or not {'id', 'character_id', 'state'} <= set(track):
            raise ValueError('Invalid character mask track')
        if 'name' in track: _text(track['name'], 'mask name', 120, empty=False)
        if 'color' in track and track['color'] not in MASK_COLORS: raise ValueError('Unknown mask color')
        owner = track['character_id']
        if owner is not None: _id(owner)
        if owner is not None and owner not in character_ids:
            raise ValueError('Mask track refers to an unknown character')
        if owner is not None and owner in owners:
            raise ValueError('Each character has one independent mask track')
        owners.add(owner)
        state = track['state']
        if not isinstance(state, dict) or set(state) - MASK_FIELDS:
            raise ValueError('Invalid mask editor state')
        for field in ('mask', 'mask_base'):
            _asset(state.get(field), optional=True)
        for field in ('mask_points', 'mask_applied_points'):
            masks.validate_points(state.get(field, []), frames)
        if 'segment_id' in track:
            from . import segment_masks
            segment = segment_masks.owner(shot, track)
            if any(not segment['start'] <= p['frame'] < segment['end']
                   for field in ('mask_points', 'mask_applied_points') for p in state.get(field, [])):
                raise ValueError('Mask points must be inside their segment')
        expansion = state.get('expansion', 64)
        if type(expansion) is not int or not 0 <= expansion <= 256:
            raise ValueError('Mask expansion must be a whole number from 0 to 256')
    for anchor in value['scene_anchors']:
        if set(anchor) - {'id', 'name', 'asset', 'source_frame', 'provenance'}:
            raise ValueError('Unknown scene anchor field')
        _text(anchor.get('name'), 'scene anchor name', 120, empty=False)
        _asset(anchor.get('asset'))
        frame = anchor.get('source_frame')
        if frame is not None and (type(frame) is not int or not 0 <= frame < frames):
            raise ValueError('Anchor source frame is outside this shot')
        if not isinstance(anchor.get('provenance', {}), dict):
            raise ValueError('Invalid anchor provenance')
    return value


def legacy_selection(shot, segment):
    anchor = segments.anchor(shot, segment)
    return {
        'characters': [LEGACY_CHARACTER],
        'mask_tracks': [LEGACY_MASK],
        'scene_anchors': [anchor_id(anchor)] if anchor else [],
    }


def character_masks(chosen):
    """UI associations; retain old combined edit regions without targeting people."""
    result = {key: list(value) if isinstance(value, list) else [value]
              for key, value in chosen.get('mask_assignments', {}).items() if key in chosen['characters']}
    used = {key for values in result.values() for key in values}
    retained = [key for key in chosen['mask_tracks'] if key not in used]
    if chosen['characters'] and retained:
        first = chosen['characters'][0]
        result[first] = result.get(first, []) + retained
    return result


def apply_character_masks(chosen, assignments):
    chosen['mask_assignments'] = {key: values for key, values in assignments.items() if key in chosen['characters'] and values}
    chosen['mask_tracks'] = list(dict.fromkeys(key for values in chosen['mask_assignments'].values() for key in values))
    return chosen


def validate_selection(library, selection):
    if not isinstance(selection, dict) or (set(selection) - (SELECTION_FIELDS | {'mask_assignments'}) or not SELECTION_FIELDS <= set(selection)):
        raise ValueError('Select characters, mask tracks and scene anchors explicitly')
    for collection in COLLECTIONS:
        keys = selection[collection]
        if not isinstance(keys, list):
            raise ValueError(f'Selected {collection} must be a list')
        for key in keys:
            _id(key)
        if len(keys) != len(set(keys)):
            raise ValueError(f'Duplicate selected {collection}')
        available = {row['id'] for row in library[collection]}
        if set(keys) - available:
            raise ValueError(f'Selected {collection} no longer exist')
    assignments = selection.get('mask_assignments')
    if assignments is not None:
        if not isinstance(assignments, dict): raise ValueError('Invalid mask assignments')
        if set(assignments) - set(selection['characters']): raise ValueError('Assign only selected characters')
        if any(not isinstance(v, str) or v not in selection['mask_tracks'] for value in assignments.values() for v in (value if isinstance(value, list) else [value])):
            raise ValueError('Assign only selected masks')
    else:
        for track in library['mask_tracks']:
            if track['id'] in selection['mask_tracks'] and track['character_id'] is not None and track['character_id'] not in selection['characters']:
                raise ValueError('A selected mask requires its character reference')
    return selection


def selection(clip, shot, segment):
    library = catalog(clip, shot)
    chosen = segment.get('cast_inputs')
    if chosen is None:
        if 'cast_setup' in shot:
            raise ValueError('Choose this segment’s cast inputs before generation')
        chosen = legacy_selection(shot, segment)
    return copy.deepcopy(validate_selection(library, chosen))


def select_mask(library, chosen, track_id, active=True):
    """Editor helper: selecting a mask also selects its character, never anchors."""
    result = copy.deepcopy(validate_selection(library, chosen))
    track = next((t for t in library['mask_tracks'] if t['id'] == track_id), None)
    if track is None:
        raise ValueError('Mask track no longer exists')
    if active:
        if track_id not in result['mask_tracks']:
            result['mask_tracks'].append(track_id)
        if track['character_id'] is not None and track['character_id'] not in result['characters']:
            result['characters'].append(track['character_id'])
    elif track_id in result['mask_tracks']:
        result['mask_tracks'].remove(track_id)
    return result


def prepare_upgrade(clip, shot):
    """Return a cast draft sharing existing assets; never write the store."""
    if 'cast_setup' in shot:
        raise ValueError('This shot already has a cast setup')
    result = copy.deepcopy(shot)
    result['cast_setup'] = catalog(clip, shot)
    result['segments'] = segments.current(shot)
    for segment in result['segments']:
        segment['cast_inputs'] = legacy_selection(shot, segment)
    return result


def resolve(clip, shot, segment):
    """Resolve ONLY this segment's selections, in their explicit picture order."""
    library = catalog(clip, shot)
    chosen = selection(clip, shot, segment)
    result = {}
    for collection in COLLECTIONS:
        by_id = {row['id']: row for row in library[collection]}
        result[collection] = [copy.deepcopy(by_id[key]) for key in chosen[collection]]
    if shot.get('segment_masks_version') == 1:
        result['mask_tracks'] = [copy.deepcopy(t) for t in library['mask_tracks']
                                 if t.get('segment_id') == segment['id'] and masks.tracked(t['state'])]
    for track in result['mask_tracks']:
        track['character_id'] = None
    return result


def reference_plan(clip, shot, segment, strict=True):
    """Only segment characters occupy numbered Picture reference slots.

    Timeline guides and generated history condition their own frame positions.
    Retained legacy anchor records are never implicit picture inputs.
    """
    chosen = resolve(clip, shot, segment)
    images = []
    for character in chosen['characters']:
        if strict and not character['reference']:
            raise ValueError(f'Add a reference image for {character["name"]}')
        images.append({'role': 'character', 'id': character['id'],
                       'asset': character['reference']})
    return [{**image, 'picture': i + 1} for i, image in enumerate(images)]


def track_context(shot, track):
    """Existing mask functions can operate on one detached track, in shot time."""
    return {'id': shot['id'], 'start': shot['start'], 'end': shot['end'],
            **copy.deepcopy(track['state'])}


def generation_inputs(clip, shot, segment, strict=True):
    """Frozen selected inputs, excluding unused cast and anchor library entries.

    This is input provenance, not a complete render signature: prompt, seed,
    generation settings and predecessor take IDs still belong to segments.
    """
    shot = segments.render_shot(shot, segment)
    resolved = resolve(clip, shot, segment)
    return {
        'version': 1,
        'characters': [
            {k: c[k] for k in ('id', 'name', 'reference', 'description')}
            for c in resolved['characters']
        ],
        'mask_tracks': [
            {'id': track['id'], 'character_id': track['character_id'],
             'mask': track['state'].get('mask'), 'expansion': track['state'].get('expansion', 64)}
            for track in resolved['mask_tracks']
        ] if shot.get('use_mask', True) else [],
        'scene_anchors': [],
        'pictures': reference_plan(clip, shot, segment, strict=strict),
        'source': clip.get('render_source') or clip['source'],
        'shot_start': shot['start'],
        'target_range': [segment['start'], segment['end']],
        'history_frames': segments.effective_history(segment),
    }


def track(shot, track_id):
    found = next((t for t in shot.get('cast_setup', {}).get('mask_tracks', [])
                  if t['id'] == track_id), None)
    if found is None:
        raise ValueError('Choose an existing character mask')
    return found


def virtual_track(shot, track_id):
    """Keep shot metadata, but never leak another character's mask state."""
    result = {k: copy.deepcopy(v) for k, v in shot.items() if k not in MASK_FIELDS}
    result.update(expansion=64, selection='largest', subject_index=0, mask_points=[],
                  mask_applied_points=[], mask=None)
    result.update(copy.deepcopy(track(shot, track_id)['state']))
    from . import segment_masks
    segment = segment_masks.owner(shot, track(shot, track_id))
    if segment:
        result['mask_segment_range'] = [segment['start'], segment['end']]
    return result


def save_track(shot, track_id, virtual):
    track(shot, track_id)['state'] = {
        k: copy.deepcopy(v) for k, v in virtual.items() if k in MASK_FIELDS}


def prompt_signature(clip, shot, segment):
    shot = segments.render_shot(shot, segment)
    inputs = generation_inputs(clip, shot, segment, strict=False)
    return segments.digest({k: inputs[k] for k in ('characters', 'pictures')} | {
        'prompt_recipe': 'character-video-v2',
        'audio': shot.get('use_source_audio', True)})


def build_prompt(clip, shot, segment):
    shot = segments.render_shot(shot, segment)
    chosen = resolve(clip, shot, segment)
    pictures = reference_plan(clip, shot, segment, strict=False)
    words = []
    by_id = {c['id']: c for c in chosen['characters']}
    for image in pictures:
        label = f'<Picture {image["picture"]}>'
        c = by_id[image['id']]
        description = c['description'].strip().rstrip('.')
        words.append(f'{c["name"]} from {label}' + (f' ({description})' if description else ''))
    target = 'the person' if len(words) == 1 else 'the selected people'
    words = [f'Replace {target} in <Video 1> with ' + ' and '.join(words) + '.'] if words else []
    words.append('Keep the source motion, expressions, timing, camera and background. Use the character references for identity and clothing only.')
    if len(pictures) > 1:
        words.append('Keep each replacement matched to its source person; leave other people unchanged.')
    audio = shot.get('use_source_audio', True)
    if audio:
        words.append('Follow <Audio 1> for speech timing.')
    dialogue = segments.dialogue(clip, shot, segment) if audio else (segment.get('dialogue') or '').strip()
    if dialogue:
        speech = {'target': 'The selected character is speaking.',
                  'other': 'Another person is speaking.',
                  'offscreen': 'Speech is offscreen; do not make visible characters speak.',
                  'none': 'No dialogue; preserve the visible source performance.'}
        words.append(f'{speech[segments.speaker(shot, segment)]} Dialogue: "{dialogue}".')
    return '\n\n'.join([' '.join(words), segment.get('prompt', '').strip()]).strip()


def validate_generation(clip, shot, segment):
    shot = segments.render_shot(shot, segment)
    pictures = reference_plan(clip, shot, segment)
    # MiniMaxH3ReferenceToVideo ref_images TemplatePrefix max=9.
    if len(pictures) > 9:
        raise ValueError('H3 accepts at most 9 reference images, including the generated boundary')
    if segments.prompt_mode(segment) == 'auto' and segment.get('cast_prompt_signature') != prompt_signature(clip, shot, segment):
        raise ValueError('References changed: Update prompt or explicitly save your revised performance prompt')
    validate_masks(clip, shot, segment)
    return pictures


def validate_masks(clip, shot, segment):
    """Masks describe the edit region, independently of character references."""
    chosen = resolve(clip, shot, segment)
    if segments.render_shot(shot, segment).get('use_mask', True):
        if not chosen['mask_tracks'] and shot.get('segment_masks_version') != 1:
            raise ValueError('Select at least one mask track or disable masks')
        begin = max(0, segment['start'] - segments.effective_history(segment))
        for t in chosen['mask_tracks']:
            if not t['state'].get('mask'):
                raise ValueError('Track each selected mask or disable masks')
            if any(begin <= f < segment['end'] for f in masks.pending_frames(t['state'])):
                raise ValueError('Apply pending corrections on each selected mask')


def combined_mask(clip, shot, segment, dimensions):
    """Expand each mask independently at render resolution, then union once."""
    import math
    import numpy as np
    from scipy.ndimage import distance_transform_edt
    start = segment['start'] - segments.effective_history(segment)
    end = segment['end']
    width, height = dimensions
    combined = np.zeros((end-start, height, width), dtype=bool)
    scale = math.sqrt(math.prod(dimensions) / math.prod(clip['dimensions']))
    for t in resolve(clip, shot, segment)['mask_tracks']:
        state = t['state']
        if not state.get('mask'):
            raise ValueError('Selected mask track has no saved mask')
        values = masks.read(state['mask'], shot['end']-shot['start'], dimensions)[start:end]
        radius = round(state.get('expansion', 64) * scale)
        for i, value in enumerate(values):
            combined[i] |= distance_transform_edt(~value) <= radius if radius and value.any() else value
    return combined


def migrate_shot(clip, shot):
    """Convert stored shot directions once without rewriting the model prompt.

    Current take signatures are translated only when the old signature matches
    the old inputs. Stale takes stay stale. Their IDs, files and exact prompts
    remain intact, and their original signatures are retained as provenance.
    """
    if 'cast_setup' in shot:
        return False
    old = copy.deepcopy(shot)
    upgraded = prepare_upgrade(clip, old)
    layouts = [(segments.current(old), upgraded['segments'])]
    layouts.extend((a['segments'], b['segments']) for a, b in zip(
        old.get('segment_layout_history', []), upgraded.get('segment_layout_history', [])))
    for before, after in layouts:
        for source, target in zip(before, after):
            source = segments.current({**old, 'segments': [source]})[0]
            target['cast_inputs'] = legacy_selection(old, source)
            target['performance_prompt'] = segments.prompt(clip, old, source)
            target['prompt_mode'] = 'manual'
            target['cast_prompt_signature'] = prompt_signature(clip, upgraded, target)
            for take in target.get('takes', []):
                prior = {**source, **{k: copy.deepcopy(v) for k,v in take.get('settings', {}).items()
                                     if k in segments.EDITABLE}}
                matches = take.get('signature') == segments.setting_signature(clip, old, prior)
                converted = {**prior, 'cast_inputs': legacy_selection(old, prior),
                             'prompt_mode': 'manual',
                             'performance_prompt': take.get('prompt', segments.prompt(clip, old, prior))}
                converted['cast_prompt_signature'] = prompt_signature(clip, upgraded, converted)
                take.setdefault('settings', {}).update({k: copy.deepcopy(converted[k]) for k in
                    ('cast_inputs', 'performance_prompt', 'cast_prompt_signature', 'prompt_mode')})
                if matches:
                    take['pre_cast_signature'] = take['signature']
                    take['signature'] = segments.setting_signature(clip, upgraded, converted)
                    take['cast_inputs_snapshot'] = generation_inputs(clip, upgraded, converted, strict=False)
    shot.update(upgraded)
    return True


def migrate_clip(clip):
    changed = False
    for shots in [clip.get('shots', []), *[v['shots'] for v in clip.get('shot_layout_history', [])]]:
        for shot in shots:
            changed = migrate_shot(clip, shot) or changed
    return changed


def migrate_store():
    """Persist the one-time conversion before the app accepts edits or jobs."""
    from . import store
    with store.LOCK:
        if store.setting('shot_cast_schema', 0) >= 1:
            return
        pending = {j['clip_id'] for j in store.pending_jobs(('queued','waiting','running','cancelling'))}
        for clip in store.clips():
            draft = copy.deepcopy(clip)
            if not migrate_clip(draft):
                continue
            if clip['id'] in pending:
                raise store.Conflict('Finish active clip jobs before migrating its shot cast')
            store.clip_mutate(clip['id'], migrate_clip, clip['revision'])
        store.set_setting('shot_cast_schema', 1)


def initialize_shot(clip, shot):
    """New shots choose their cast from the project instead of creating characters."""
    shot.setdefault('anchor_conditioning', 'reference')
    # Explicitly seed new shots; existing/legacy plans retain their saved ranges.
    shot['segments'] = [segments.new(shot, 0, shot['end'] - shot['start'], 0)]
    shot.update(prepare_upgrade(clip, shot))
    shot['cast_setup']['characters'] = []
    shot['cast_setup']['mask_tracks'] = [{'id': LEGACY_MASK, 'character_id': None, 'name': 'Mask 1',
        'color': MASK_COLORS[0], 'state': {'mask': None, 'expansion': 64, 'selection': 'largest',
        'subject_index': 0, 'mask_points': [], 'mask_applied_points': []}}]
    for segment in shot['segments']:
        segment['cast_inputs']['characters'] = []
        segment['cast_inputs']['mask_tracks'] = []
        # A blank reference is valid while setting up a new shot.
        segment['performance_prompt'] = build_prompt(clip, shot, segment)
        segment['cast_prompt_signature'] = prompt_signature(clip, shot, segment)
    from . import segment_masks
    segment_masks.migrate_shot(clip, shot)
    # Empty shots use a virtual Mask 1 until the first point is placed.
    shot['cast_setup']['mask_tracks'] = []
    return shot
