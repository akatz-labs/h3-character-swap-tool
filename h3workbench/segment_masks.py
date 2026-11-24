"""Segment-owned editable masks over immutable shot-coordinate mask assets.

Keeping assets in shot coordinates preserves preview URLs and historical takes.
Only tracking input video is trimmed; independent records never share mutable state.
"""
import copy
from . import cast, masks, segments, store


def owner(shot, track):
    key = track.get('segment_id')
    if key is None:
        return None
    item = next((s for s in shot['segments'] if s['id'] == key), None)
    if item is None:
        raise ValueError('Mask belongs to an older segment layout')
    return item


def clone(track, segment):
    result = copy.deepcopy(track)
    result.update(id=store.uid('mask-'), segment_id=segment['id'], character_id=None,
                  legacy_id=track.get('legacy_id', track['id']))
    for field in ('mask_points', 'mask_applied_points'):
        result['state'][field] = [p for p in result['state'].get(field, [])
                                  if segment['start'] <= p['frame'] < segment['end']]
    return result


def migrate_shot(clip, shot):
    if shot.get('segment_masks_version') == 1 or 'cast_setup' not in shot:
        return False
    old = copy.deepcopy(shot['cast_setup']['mask_tracks'])
    items = shot['segments'] = segments.current(shot)
    ready = {s['id']: segments.setting_signature(clip, shot, s) for s in items}
    referenced = {key for s in items for key in s['cast_inputs']['mask_tracks']}
    shot.setdefault('mask_track_history', []).extend(old)
    tracks = []
    for i, segment in enumerate(items):
        chosen = segment['cast_inputs']
        enabled = segments.render_shot(shot, segment).get('use_mask', True)
        selected = []
        for j, track in enumerate(old):
            if track['id'] not in chosen['mask_tracks'] and not (i == 0 and track['id'] not in referenced):
                continue
            record = clone(track, segment)
            record.setdefault('name', f'Mask {j+1}')
            tracks.append(record)
            if enabled and track['id'] in chosen['mask_tracks']:
                selected.append(record['id'])
        chosen.update(mask_tracks=selected, mask_assignments={})
        segment['use_mask'] = bool(selected)
    shot['cast_setup']['mask_tracks'] = tracks
    shot['segment_masks_version'] = 1
    # IDs changed, but current generation pixels/settings did not. Preserve
    # current status only for takes whose old signature actually matched.
    for segment in items:
        for take in segment['takes']:
            if take.get('signature') == ready[segment['id']]:
                take['pre_segment_masks_signature'] = take['signature']
                take['signature'] = segments.setting_signature(clip, shot, segment)
    return True


def migrate_store():
    with store.LOCK:
        if store.setting('segment_masks_schema', 0) >= 1:
            return
        pending = {j['clip_id'] for j in store.pending_jobs(('queued', 'waiting', 'running', 'cancelling'))}
        for clip in store.clips():
            if not any('cast_setup' in s and s.get('segment_masks_version') != 1 for s in clip['shots']):
                continue
            if clip['id'] in pending:
                raise store.Conflict('Finish active clip jobs before migrating segment masks')
            def change(c):
                for shot in c['shots']:
                    migrate_shot(c, shot)
            store.clip_mutate(clip['id'], change, clip['revision'])
        store.set_setting('segment_masks_schema', 1)


def rebind_layout(shot, items, confirmed=()):
    retained_ids = {item['id'] for item in items}
    tracks = shot['cast_setup']['mask_tracks']
    removed = [track for track in tracks if track.get('segment_id') not in retained_ids]
    if sorted(confirmed) != sorted(track['id'] for track in removed):
        raise store.Conflict('Review the masks removed by this layout change')
    shot.setdefault('mask_track_history', []).extend(copy.deepcopy(removed))
    shot['cast_setup']['mask_tracks'] = [track for track in tracks if track.get('segment_id') in retained_ids]


def restore_take(shot, segment, take):
    """Restore frozen mask inputs without changing another segment's records."""
    assignments = cast.character_masks(segment['cast_inputs'])
    remap = {}
    selected = []
    tracks = shot['cast_setup']['mask_tracks']
    for entry in take.get('cast_inputs_snapshot', {}).get('mask_tracks', []):
        track = next((t for t in tracks if t.get('segment_id') == segment['id']
                      and entry['id'] in (t['id'], t.get('legacy_id'))), None)
        if track is None:
            track = {'id': store.uid('mask-'), 'segment_id': segment['id'], 'legacy_id': entry['id'],
                     'character_id': None, 'name': f'Mask {len(selected)+1}', 'state': {}}
            tracks.append(track)
        masks.preserve(track['state'])
        track['state'].update(mask=entry['mask'], expansion=entry['expansion'],
                              mask_points=[], mask_applied_points=[], mask_approved=False)
        track['state'].setdefault('selection', 'largest')
        track['state'].setdefault('subject_index', 0)
        # Coverage belongs to the restored asset, not to the previous mask.
        for field in ('mask_coverage', 'mask_empty_frames'):
            track['state'].pop(field, None)
        masks.preserve(track['state'])
        selected.append(track['id'])
        remap[entry['id']] = track['id']
    segment['cast_inputs'].update(mask_tracks=selected, mask_assignments={key: [remap[v] for v in values if v in remap] for key, values in assignments.items()})
    segment['cast_inputs']['mask_assignments'] = cast.character_masks(segment['cast_inputs'])
    segment['use_mask'] = bool(selected)
