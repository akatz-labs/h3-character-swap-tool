import json
import pytest
from h3workbench import store

@pytest.fixture
def projects(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DATA', tmp_path / 'state')
    monkeypatch.setattr(store, 'MEDIA', tmp_path / 'media')
    store.DATA.mkdir();store.MEDIA.mkdir()
    store.init()
    return store.batch_create('Delete me'), store.batch_create('Keep me')

def test_delete_removes_owned_files_but_retains_cross_project_references(projects):
    first, other = projects
    root = store.MEDIA / 'batches' / first
    root.mkdir(parents=True)
    owned = root / 'owned.png'; owned.write_bytes(b'owned')
    shared = root / 'shared.png'; shared.write_bytes(b'shared')
    owned_id, shared_id = store.asset_add(owned), store.asset_add(shared)
    clip=store.clip_create(first, {'name':'first','source':owned_id,'shots':[]})
    keep=store.clip_create(other, {'name':'keep','source':shared_id,'shots':[]})
    assert store.batch_delete(first)=={'id':first}
    assert not owned.exists() and shared.exists()
    assert store.clip_get(keep['id'])['source']==shared_id
    with pytest.raises(KeyError):store.clip_get(clip['id'])
    with pytest.raises(KeyError):store.asset(owned_id)
    assert store.asset(shared_id)
    store.cleanup_deleted_projects()
    assert shared.exists()

def test_delete_fences_active_jobs_and_retries_file_cleanup(projects,monkeypatch):
    first,_=projects
    clip=store.clip_create(first,{'name':'first','shots':[]})
    with store.db() as con:
        con.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?)',('test-job','mask_edit',clip['id'],'s','running',1,1,json.dumps({})))
    with pytest.raises(store.Conflict):store.batch_delete(first)
    assert store.clip_get(clip['id'])
