"""Named masks survive character changes; assignment is local to a segment."""
import copy
from h3workbench import cast, segments, store
from test_cast import scene
from test_project_characters import project


def test_named_mask_lifecycle_and_segment_assignment(project):
    client, _, _, original, _, _ = project
    cid, sid = original['id'], original['shots'][0]['id']
    path = f'/api/clips/{cid}/shots/{sid}/cast'
    before = store.clip_get(cid)
    created = client.post(path+'/masks', json={'revision': before['revision']})
    assert created.status_code == 200, created.text
    c = created.json(); s = c['shots'][0]; mask = s['cast_setup']['mask_tracks'][-1]
    assert mask['character_id'] is None and mask['state']['expansion'] == 64
    assert mask['color'] != s['cast_setup']['mask_tracks'][0].get('color', cast.MASK_COLORS[0])
    renamed = client.patch(path+'/masks/'+mask['id'], json={'revision': c['revision'], 'name': 'Person on left'})
    assert renamed.status_code == 200, renamed.text
    c = renamed.json(); s = c['shots'][0]
    seg = s['segments'][0]; character = s['cast_setup']['characters'][0]['id']
    chosen = copy.deepcopy(seg['cast_inputs'])
    chosen.update(mask_tracks=[mask['id']], mask_assignments={character: mask['id']})
    response = client.patch(f'/api/clips/{cid}/shots/{sid}/segments/{seg["id"]}', json={
        'revision': c['revision'], 'changes': {'cast_inputs': chosen}})
    assert response.status_code == 200, response.text
    c = response.json(); s = c['shots'][0]
    resolved = cast.generation_inputs(c, s, s['segments'][0], strict=False)
    assert resolved['mask_tracks'][0]['id'] == mask['id']
    assert resolved['mask_tracks'][0]['character_id'] is None
    assert s['segments'][0]['cast_inputs']['mask_assignments'][character] == mask['id']
    assert s['cast_setup']['mask_tracks'][-1]['character_id'] is None
    assert s['segments'][1:]==before['shots'][0]['segments'][1:]
    takes = [copy.deepcopy(v['takes']) for v in s['segments']]
    deleted = client.request('DELETE', path+'/masks/'+mask['id'], json={'revision': c['revision']})
    assert deleted.status_code == 200, deleted.text
    s = deleted.json()['shots'][0]
    assert mask['id'] not in s['segments'][0]['cast_inputs']['mask_tracks']
    assert s['segments'][0]['cast_inputs']['mask_assignments'] == {}
    assert s['mask_track_history'][-1]['id'] == mask['id']
    assert [v['takes'] for v in s['segments']] == takes


def test_invalid_assignments_and_new_character_without_new_mask(project):
    client, batch, _, original, _, _ = project
    c = store.clip_get(original['id']); s = c['shots'][0]
    library = s['cast_setup']; chosen = copy.deepcopy(s['segments'][0]['cast_inputs'])
    chosen['mask_assignments'] = {'missing-character': library['mask_tracks'][0]['id']}
    import pytest
    with pytest.raises(ValueError, match='selected characters'):
        cast.validate_selection(library, chosen)
    entry = client.post(f'/api/batches/{batch}/characters', json={'name':'New person','description':''})
    assert entry.status_code == 200, entry.text
    path = f'/api/clips/{c["id"]}/shots/{s["id"]}/cast'
    response = client.post(path+'/characters', json={'revision':c['revision'],
        'character_ids':[entry.json()['id']], 'create_mask':False})
    assert response.status_code == 200, response.text
    current = response.json(); current_shot = current['shots'][0]
    assert len(current_shot['cast_setup']['mask_tracks']) == len(library['mask_tracks'])
    removed = client.request('DELETE', path+'/characters/'+library['characters'][0]['id'], json={'revision':current['revision']})
    assert removed.status_code == 200, removed.text
    assert removed.json()['shots'][0]['cast_setup']['mask_tracks'][0]['state'] == library['mask_tracks'][0]['state']


def test_segment_masks_survive_reference_removal_and_reload(project):
    client, _, _, original, _, _ = project
    c = store.clip_get(original['id']); s = c['shots'][0]; seg = s['segments'][0]
    before = copy.deepcopy(s['segments'][1:])
    chosen = copy.deepcopy(seg['cast_inputs'])
    mask_ids = chosen['mask_tracks']
    chosen.update(characters=[], mask_assignments={})
    path = f'/api/clips/{c["id"]}/shots/{s["id"]}/segments/{seg["id"]}'
    response = client.patch(path, json={'revision': c['revision'], 'changes': {'cast_inputs': chosen, 'use_mask': False}})
    assert response.status_code == 200, response.text
    saved = store.clip_get(c['id']); sh = saved['shots'][0]; item = sh['segments'][0]
    assert item['cast_inputs']['characters'] == []
    assert item['cast_inputs']['mask_tracks'] == mask_ids
    assert sh['segments'][1:] == before
    chosen['characters'] = seg['cast_inputs']['characters']
    response = client.patch(path, json={'revision': saved['revision'], 'changes': {'cast_inputs': chosen, 'use_mask': True}})
    assert response.status_code == 200, response.text
    reloaded = store.clip_get(c['id'])['shots'][0]['segments'][0]
    assert reloaded['use_mask'] and reloaded['cast_inputs']['mask_tracks'] == mask_ids
    assert reloaded['cast_inputs']['mask_assignments'] == {}
