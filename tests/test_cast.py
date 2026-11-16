"""Pure domain tests: no media copies, model calls, workers or live app data."""
import copy

import pytest

from h3workbench import cast, masks, segments


@pytest.fixture
def scene():
    clip = {'source': 'source-video', 'reference': 'clip-character', 'character': 'Legacy description'}
    shot = {
        'id': 'shot-1', 'start': 240, 'end': 480, 'seed': 194729,
        'reference': 'shot-character', 'character': 'Shot description',
        'anchor': 'early-anchor', 'use_mask': True, 'mask': 'legacy-mask-asset',
        'mask_base': 'original-mask', 'expansion': 64,
        'mask_points': [{'id': 'p1', 'frame': 15, 'x': .4, 'y': .5, 'label': 1}],
        'mask_applied_points': [{'id': 'p1', 'frame': 15, 'x': .4, 'y': .5, 'label': 1}],
        'mask_empty_frames': [239],
        'mask_versions': [{'asset': 'original-mask', 'points': [], 'created': 1}],
        'anchors': [{'asset': 'early-anchor', 'source_frame': 269, 'generator': 'Manual import'}],
        'selected_render': 'whole-shot-take',
    }
    shot['segments'] = [segments.new(shot, 0, 120, 0), segments.new(shot, 120, 240, 1)]
    shot['segments'][0]['takes'] = [{'id': 'retained-take', 'asset': 'result-video'}]
    shot['segments'][0]['selected_take'] = 'retained-take'
    shot['segments'][1].update(
        anchor_mode='custom', anchor='late-anchor',
        anchors=[{'asset': 'late-anchor', 'source_frame': 394, 'generator': 'Manual import'}],
    )
    shot['segment_layout_history'] = [{'segments': copy.deepcopy(shot['segments'])}]
    return clip, shot


@pytest.fixture
def multi(scene):
    clip, old = scene
    shot = cast.prepare_upgrade(clip, old)
    library = shot['cast_setup']
    library['characters'].extend([
        {'id': 'character-b', 'name': 'Rowan', 'reference': 'ref-b', 'description': 'Green coat'},
        {'id': 'character-c', 'name': 'C', 'reference': 'ref-c', 'description': ''},
    ])
    library['mask_tracks'].append({
        'id': 'mask-b', 'character_id': 'character-b',
        'state': {'mask': 'mask-b-asset', 'expansion': 20, 'mask_points': [],
                  'mask_applied_points': [], 'mask_empty_frames': list(range(110))},
    })
    library['scene_anchors'].append({
        'id': 'anchor-c', 'name': 'Third character', 'asset': 'anchor-c-asset', 'source_frame': 230,
    })
    shot['segments'][1]['cast_inputs'] = {
        'characters': [cast.LEGACY_CHARACTER, 'character-b'],
        'mask_tracks': [cast.LEGACY_MASK, 'mask-b'],
        'scene_anchors': [cast.anchor_id('late-anchor')],
    }
    return clip, shot


def test_legacy_catalog_is_readonly_and_preserves_mask_history(scene):
    clip, shot = scene
    before = copy.deepcopy((clip, shot))
    library = cast.catalog(clip, shot)
    assert library['characters'][0]['reference'] == 'shot-character'
    assert library['characters'][0]['description'] == 'Shot description'
    state = library['mask_tracks'][0]['state']
    assert state['mask'] == 'legacy-mask-asset'
    assert state['mask_base'] == 'original-mask'
    assert state['mask_versions'] == shot['mask_versions']
    assert state['mask_empty_frames'] == [239]
    assert [a['source_frame'] for a in library['scene_anchors']] == [29, 154]
    assert library['scene_anchors'][0]['provenance']['source_frame'] == 269
    state['mask_points'][0]['x'] = .8
    library['characters'][0]['name'] = 'Edited'
    assert (clip, shot) == before


def test_legacy_clip_identity_fallback_and_missing_reference(scene):
    clip, shot = scene
    del shot['reference']
    del shot['character']
    assert cast.catalog(clip, shot)['characters'][0]['reference'] == 'clip-character'
    assert cast.catalog(clip, shot)['characters'][0]['description'] == 'Legacy description'
    clip['reference'] = None
    assert cast.catalog(clip, shot)['characters'][0]['reference'] is None
    with pytest.raises(ValueError, match='Add a reference'):
        cast.reference_plan(clip, shot, shot['segments'][0])


def test_unknown_anchor_source_is_not_guessed_from_current_frame(scene):
    clip, shot = scene
    shot['anchors'] = []
    shot['anchor_source_index'] = 95
    shot['anchor_source_frame'] = 'newly-selected-frame'
    library = cast.catalog(clip, shot)
    early = next(a for a in library['scene_anchors'] if a['asset'] == 'early-anchor')
    assert early['source_frame'] is None


def test_upgrade_is_explicit_draft_retaining_assets_takes_and_layouts(scene):
    clip, shot = scene
    before = copy.deepcopy(shot)
    draft = cast.prepare_upgrade(clip, shot)
    assert shot == before
    assert draft['mask'] == shot['mask']
    assert draft['selected_render'] == 'whole-shot-take'
    assert draft['segment_layout_history'] == shot['segment_layout_history']
    assert draft['segments'][0]['takes'] == shot['segments'][0]['takes']
    assert draft['segments'][0]['selected_take'] == 'retained-take'
    assert draft['segments'][1]['cast_inputs']['scene_anchors'] == [cast.anchor_id('late-anchor')]
    with pytest.raises(ValueError, match='already'):
        cast.prepare_upgrade(clip, draft)
    draft['cast_setup']['mask_tracks'][0]['state']['mask_versions'].clear()
    assert shot == before


def test_picture_slots_only_include_characters_even_with_history_and_legacy_anchors(scene):
    clip, shot = scene
    first, second = shot['segments']
    assert [(p['role'], p['picture'], p['asset']) for p in cast.reference_plan(clip, shot, first)] == [
        ('character', 1, 'shot-character')]
    assert [(p['role'], p['picture'], p['asset']) for p in cast.reference_plan(clip, shot, second)] == [
        ('character', 1, 'shot-character')]


def test_handoff_uses_two_characters_both_masks_and_only_one_scene_anchor(multi):
    clip, shot = multi
    segment = shot['segments'][1]
    resolved = cast.resolve(clip, shot, segment)
    assert [c['id'] for c in resolved['characters']] == [cast.LEGACY_CHARACTER, 'character-b']
    assert [m['state']['expansion'] for m in resolved['mask_tracks']] == [64, 20]
    assert [a['asset'] for a in resolved['scene_anchors']] == ['late-anchor']
    assert [(p['role'], p['picture'], p['asset']) for p in cast.reference_plan(clip, shot, segment)] == [
        ('character', 1, 'shot-character'), ('character', 2, 'ref-b')]
    assert 'ref-c' not in str(cast.generation_inputs(clip, shot, segment))
    assert 'anchor-c-asset' not in str(cast.generation_inputs(clip, shot, segment))


def test_picture_order_follows_segment_selection_not_library_order(multi):
    clip, shot = multi
    segment = shot['segments'][1]
    segment['cast_inputs']['characters'].reverse()
    shot['cast_setup']['characters'].reverse()
    assert [p['asset'] for p in cast.reference_plan(clip, shot, segment)][:2] == [
        'ref-b', 'shot-character']


def test_empty_anchor_selection_does_not_fall_back_to_all_anchors(multi):
    clip, shot = multi
    segment = shot['segments'][1]
    segment['cast_inputs']['scene_anchors'] = []
    assert cast.resolve(clip, shot, segment)['scene_anchors'] == []
    assert len(cast.reference_plan(clip, shot, segment)) == 2


def test_empty_cast_has_no_picture_inputs(multi):
    clip, shot = multi
    segment = shot['segments'][1]
    segment['cast_inputs'] = {key: [] for key in cast.COLLECTIONS}
    assert cast.resolve(clip, shot, segment)['characters'] == []
    assert cast.reference_plan(clip, shot, segment) == []


def test_missing_selection_on_cast_enabled_shot_requires_review(multi):
    clip, shot = multi
    segment = shot['segments'][0]
    del segment['cast_inputs']
    with pytest.raises(ValueError, match='Choose this segment'):
        cast.resolve(clip, shot, segment)


def test_select_mask_adds_owner_but_never_adds_anchors_or_edits_prior_selection(multi):
    clip, shot = multi
    library = cast.catalog(clip, shot)
    before = copy.deepcopy(shot['segments'][0]['cast_inputs'])
    selected = cast.select_mask(library, before, 'mask-b')
    assert selected['characters'] == [cast.LEGACY_CHARACTER, 'character-b']
    assert selected['mask_tracks'] == [cast.LEGACY_MASK, 'mask-b']
    assert selected['scene_anchors'] == before['scene_anchors']
    assert before == shot['segments'][0]['cast_inputs']
    removed = cast.select_mask(library, selected, 'mask-b', active=False)
    assert removed['mask_tracks'] == [cast.LEGACY_MASK]
    assert 'character-b' in removed['characters']
    assert cast.select_mask(library, selected, 'mask-b') == selected


@pytest.mark.parametrize('change,message', [
    ({'characters': [cast.LEGACY_CHARACTER]}, 'requires its character'),
    ({'mask_tracks': ['missing']}, 'no longer exist'),
    ({'scene_anchors': ['missing']}, 'no longer exist'),
    ({'characters': ['character-b', 'character-b']}, 'Duplicate'),
    ({'characters': [True]}, 'identifier'),
    ({'characters': 'character-b'}, 'must be a list'),
])
def test_invalid_selection_rejected(multi, change, message):
    clip, shot = multi
    shot['segments'][1]['cast_inputs'].update(change)
    with pytest.raises(ValueError, match=message):
        cast.resolve(clip, shot, shot['segments'][1])


def test_mask_contexts_keep_shot_coordinates_and_do_not_mutate_sibling(multi):
    clip, shot = multi
    library = cast.catalog(clip, shot)
    before = copy.deepcopy(library)
    a, b = [cast.track_context(shot, track) for track in library['mask_tracks']]
    assert (a['start'], a['end']) == (240, 480)
    assert (b['start'], b['end']) == (240, 480)
    a['mask_points'][0]['frame'] = 130
    assert masks.pending_frames(a) == [15, 130]
    assert masks.pending_frames(b) == []
    assert library == before
    # Splitting generation ranges does not split a shot's character mask data.
    segment = copy.deepcopy(shot['segments'][1])
    segment['start'] = 150
    assert cast.resolve(clip, shot, segment)['mask_tracks'][0]['state'] == before['mask_tracks'][0]['state']


def test_selected_snapshot_ignores_unused_library_edits_but_tracks_used_assets(multi):
    clip, shot = multi
    segment = shot['segments'][1]
    frozen = cast.generation_inputs(clip, shot, segment)
    shot['cast_setup']['characters'][2]['reference'] = 'changed-c'
    shot['cast_setup']['scene_anchors'][2]['asset'] = 'changed-anchor-c'
    assert cast.generation_inputs(clip, shot, segment) == frozen
    shot['cast_setup']['characters'][1]['reference'] = 'changed-b'
    assert cast.generation_inputs(clip, shot, segment) != frozen
    assert frozen['characters'][1]['reference'] == 'ref-b'
    assert frozen['target_range'] == [120, 240]
    assert frozen['history_frames'] == 18


def test_disabled_masks_omit_mask_assets_from_generation_snapshot(multi):
    clip, shot = multi
    shot['use_mask'] = False
    segment = shot['segments'][1]
    frozen = cast.generation_inputs(clip, shot, segment)
    shot['cast_setup']['mask_tracks'][1]['state']['mask'] = 'new-mask'
    assert cast.generation_inputs(clip, shot, segment) == frozen
    assert frozen['mask_tracks'] == []


@pytest.mark.parametrize('change,message', [
    (lambda c: c.update(version=True), 'format'),
    (lambda c: c['characters'][1].update(id=cast.LEGACY_CHARACTER), 'unique'),
    (lambda c: c['mask_tracks'][1].update(character_id='missing'), 'unknown character'),
    (lambda c: c['mask_tracks'][1].update(character_id=cast.LEGACY_CHARACTER), 'one independent'),
    (lambda c: c['mask_tracks'][1]['state'].update(expansion=True), 'expansion'),
    (lambda c: c['scene_anchors'][0].update(source_frame=240), 'outside this shot'),
    (lambda c: c['scene_anchors'][0].update(source_frame=True), 'outside this shot'),
    (lambda c: c['characters'][0].update(name=''), 'character name'),
])
def test_catalog_validation(multi, change, message):
    clip, shot = multi
    change(shot['cast_setup'])
    with pytest.raises(ValueError, match=message):
        cast.catalog(clip, shot)
