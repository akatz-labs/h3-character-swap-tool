import copy
import io
import json

import pytest
from PIL import Image
from fastapi.testclient import TestClient
from h3workbench import cast, pipeline, project_characters as characters, project_character_api, segments, store
from h3workbench.app import app
from test_cast import scene


@pytest.fixture
def project(tmp_path, monkeypatch, scene):
    monkeypatch.setattr(store, 'DATA', tmp_path)
    monkeypatch.setattr(store, 'MEDIA', tmp_path)
    monkeypatch.setattr(project_character_api, 'MEDIA', tmp_path)
    monkeypatch.setattr(pipeline, 'start', lambda:None)
    store.init()
    c, s = scene
    cast.migrate_shot(c, s)
    c.update(name='First clip', shots=[s], result='existing-result')
    first = store.batch_create('Project one')
    other = store.batch_create('Project two')
    a = store.clip_create(first, copy.deepcopy(c))
    c['name'] = 'Second clip'
    b = store.clip_create(first, copy.deepcopy(c))
    c['name'] = 'Other project clip'
    foreign = store.clip_create(other, copy.deepcopy(c))
    store.set_setting('project_character_schema', 0)
    with TestClient(app) as client:
        yield client, first, other, a, b, foreign


def without_links(c):
    result = copy.deepcopy(c)
    result.pop('revision')
    for s in result['shots']:
        for ch in s['cast_setup']['characters']:
            ch.pop('project_character_id', None)
            ch.pop('project_character_revision', None)
    return result


def test_migration_deduplicates_only_within_project_and_preserves_work(project):
    client, first, other, a, b, foreign = project
    items = client.get(f'/api/batches/{first}/characters').json()
    assert len(items) == 1 and len(items[0]['usage']) == 2
    elsewhere = client.get(f'/api/batches/{other}/characters').json()
    assert len(elsewhere) == 1 and elsewhere[0]['id'] != items[0]['id']
    for old in [a, b, foreign]:
        current = store.clip_get(old['id'])
        assert without_links(current) == without_links(old)
        assert current['revision'] == old['revision'] + 1
        before = segments.states(old, old['shots'][0])
        after = segments.states(current, current['shots'][0])
        assert before == after
    snapshot = store.clips()
    characters.migrate_store()
    assert store.clips() == snapshot


def test_update_refreshes_two_clips_but_keeps_masks_prompts_takes_and_other_project(project):
    client, first, other, a, b, foreign = project
    entry = characters.listing(first)[0]
    before = {c['id']:c for c in store.clips()}
    response = client.patch(f'/api/batches/{first}/characters/{entry["id"]}', json={
        'revision':entry['revision'], 'changes':{'name':'Emilia', 'description':'New appearance'}})
    assert response.status_code == 200, response.text
    assert response.json()['revision'] == 2
    for old in [a, b]:
        prior = before[old['id']]; current = store.clip_get(old['id'])
        s = current['shots'][0]; original = prior['shots'][0]
        assert s['cast_setup']['characters'][0]['name'] == 'Emilia'
        assert s['cast_setup']['mask_tracks'] == original['cast_setup']['mask_tracks']
        assert s['segments'] == original['segments']
        assert s['segment_layout_history'] == original['segment_layout_history']
        assert s['selected_render'] == original['selected_render']
        assert current['result'] is None
        for seg in s['segments']:
            assert seg['cast_prompt_signature'] != cast.prompt_signature(current, s, seg)
    assert store.clip_get(foreign['id']) == before[foreign['id']]
    with store.db() as con:
        versions = con.execute('SELECT data FROM character_versions WHERE character_id=? ORDER BY revision', (entry['id'],)).fetchall()
    assert len(versions) == 2 and json.loads(versions[0][0])['description'] == entry['description']
    conflict = client.patch(f'/api/batches/{first}/characters/{entry["id"]}', json={'revision':1,'changes':{'name':'Stale'}})
    assert conflict.status_code == 409


def test_picker_adds_project_asset_with_independent_masks_and_guards_scope(project):
    client, first, other, a, b, _ = project
    new = client.post(f'/api/batches/{first}/characters', json={'name':'Kai'}).json()
    def add(clip, project_character):
        return client.post(f'/api/clips/{clip["id"]}/shots/shot-1/cast/characters', json={
            'revision':store.clip_get(clip['id'])['revision'], 'character_ids':[project_character['id']]})
    foreign = characters.create(other, 'Private character')
    assert add(a, foreign).status_code == 404
    results = [add(c, new) for c in [a, b]]
    assert all(r.status_code == 200 for r in results)
    sa, sb = [r.json()['shots'][0] for r in results]
    assert sa['cast_setup']['characters'][1]['project_character_id'] == sb['cast_setup']['characters'][1]['project_character_id']
    assert sa['cast_setup']['mask_tracks'][1]['id'] != sb['cast_setup']['mask_tracks'][1]['id']
    for s in [sa, sb]:
        assert s['cast_setup']['characters'][1]['id'] in s['segments'][0]['cast_inputs']['characters']
        assert s['cast_setup']['mask_tracks'][1]['state']['mask'] is None
    again = add(a, new)
    assert len(again.json()['shots'][0]['cast_setup']['characters']) == 2
    assert client.patch(f'/api/batches/{other}/characters/{new["id"]}',json={'revision':1,'changes':{'name':'Bad scope'}}).status_code == 404
    assert client.post('/api/batches/missing/characters',json={'name':'X'}).status_code == 404


def test_reference_upload_is_versioned_and_stale_upload_cannot_overwrite(project):
    client, first, other, a, b, _ = project
    c = characters.listing(first)[0]
    image = io.BytesIO(); Image.new('RGB', (64,96), 'purple').save(image, format='PNG')
    url = f'/api/batches/{first}/characters/{c["id"]}/image'
    upload = lambda revision:client.post(url,data={'revision':revision},files={'file':('reference.png',image.getvalue(),'image/png')})
    response = upload(1)
    assert response.status_code == 200, response.text
    new = response.json(); assert new['reference'] != c['reference']
    assert store.path(new['reference']).is_file()
    assert all(store.clip_get(x['id'])['shots'][0]['cast_setup']['characters'][0]['reference'] == new['reference'] for x in [a,b])
    assert upload(1).status_code == 409
    invalid = client.post(url,data={'revision':2},files={'file':('bad.png',b'not an image','image/png')})
    assert invalid.status_code == 400
    assert characters.listing(first)[0]['reference'] == new['reference']


def test_invalid_character_changes_are_atomic(project):
    client, first, _, a, _, _ = project
    c = characters.listing(first)[0]; before = store.clip_get(a['id'])
    url = f'/api/batches/{first}/characters/{c["id"]}'
    for changes in [{'name':''}, {'name':'x'*121}, {'reference':'foreign-asset'}, {'description':None}]:
        assert client.patch(url,json={'revision':1,'changes':changes}).status_code == 400
    assert characters.listing(first)[0] == c
    assert store.clip_get(a['id']) == before


def test_project_updates_do_not_rewrite_queued_job_inputs(project):
    _, first, _, a, _, _ = project
    snapshot = store.clip_get(a['id'])
    job_id = store.job_create('render', a['id'], 'shot-1', {'snapshot':snapshot, 'shot':snapshot['shots'][0]})
    before = store.job_get(job_id)
    character = characters.listing(first)[0]
    characters.update(first, character['id'], character['revision'], {'description':'Changed after queueing'})
    assert store.job_get(job_id) == before
    assert store.clip_get(a['id'])['shots'][0]['cast_setup']['characters'][0]['description'] == 'Changed after queueing'


def test_active_job_blocks_migration_atomically(tmp_path, monkeypatch, scene):
    monkeypatch.setattr(store, 'DATA', tmp_path); monkeypatch.setattr(store, 'MEDIA', tmp_path)
    store.init(); project_id = store.batch_create('Migration safety')
    c,s = scene; cast.migrate_shot(c,s); c.update(shots=[s])
    first = store.clip_create(project_id, copy.deepcopy(c))
    second = store.clip_create(project_id, copy.deepcopy(c))
    store.job_create('render', second['id'], s['id'], {'snapshot':second})
    store.set_setting('project_character_schema', 0)
    with pytest.raises(store.Conflict, match='Finish active'):
        characters.migrate_store()
    assert characters.listing(project_id) == []
    assert store.clip_get(first['id']) == first
    assert store.clip_get(second['id']) == second
    assert store.setting('project_character_schema') == 0


def test_reference_upload_preserves_alpha(project):
    client, first, *_ = project
    entry = characters.listing(first)[0]
    image = io.BytesIO()
    Image.new('RGBA', (32, 32), (80, 140, 220, 0)).save(image, format='PNG')
    response = client.post(f'/api/batches/{first}/characters/{entry["id"]}/image',
        data={'revision': entry['revision']}, files={'file': ('alpha.png', image.getvalue(), 'image/png')})
    assert response.status_code == 200, response.text
    with Image.open(store.path(response.json()['reference'])) as saved:
        assert saved.mode == 'RGBA' and saved.getpixel((0, 0))[3] == 0
