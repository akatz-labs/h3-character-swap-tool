"""Real HF cache removal on disposable tiny files, never user models."""
import hashlib
import sys
from types import SimpleNamespace
import pytest
from h3workbench import transcription_resources as resources, store, remote


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(resources, 'DATA', tmp_path / 'app')
    monkeypatch.setattr(resources, 'MODELS', tmp_path / 'shared-models')
    monkeypatch.setattr(resources, 'FILE_SIZES', {name: 3 for name in resources.FILES})
    monkeypatch.setattr(resources, 'SIZE', 12)
    monkeypatch.setattr(resources, 'busy', lambda: False)
    resources.PLANS.clear()
    snapshot = resources.snapshot_path()
    snapshot.mkdir(parents=True)
    blobs = snapshot.parent.parent / 'blobs'
    blobs.mkdir()
    for i, name in enumerate(resources.FILES):
        data = bytes([65+i])*3
        blob = blobs / hashlib.sha256(data).hexdigest()
        blob.write_bytes(data)
        (snapshot / name).symlink_to('../../blobs/' + blob.name)
    return snapshot


def test_review_then_delete_exact_snapshot_and_preserve_unrelated_cache(cache):
    unrelated = resources.cache_root() / 'unrelated.txt'
    unrelated.write_text('preserve')
    assert resources.installed()
    review = resources.removal_plan()
    assert review['reclaimBytes'] == 12
    assert resources.uninstall({'planId':review['id'], 'digest':review['digest'], 'confirm':resources.RESOURCE_ID}) == {'complete':True}
    assert not resources.installed()
    assert unrelated.read_text() == 'preserve'


def test_changed_file_and_busy_transcription_reject_deletion(cache, monkeypatch):
    review = resources.removal_plan()
    (cache / 'model.bin').write_bytes(b'changed')
    with pytest.raises(store.Conflict, match='changed'):
        resources.uninstall({'planId':review['id'], 'digest':review['digest'], 'confirm':resources.RESOURCE_ID})
    assert (cache / 'model.bin').exists()
    monkeypatch.setattr(resources, 'busy', lambda: True)
    with pytest.raises(store.Conflict, match='Wait'):
        resources.removal_plan()


def test_wrong_confirmation_or_expired_review_is_rejected(cache):
    review = resources.removal_plan()
    with pytest.raises(ValueError, match='Review'):
        resources.uninstall({'planId':review['id'], 'digest':review['digest'], 'confirm':'other-model'})
    resources.PLANS[review['id']] = (review['digest'], 0)
    with pytest.raises(store.Conflict, match='expired'):
        resources.uninstall({'planId':review['id'], 'digest':review['digest'], 'confirm':resources.RESOURCE_ID})
    assert resources.installed()


def test_explicit_install_uses_same_cache_and_pinned_files(cache, monkeypatch):
    (cache / 'tokenizer.json').unlink()
    updates, downloads = [], []
    monkeypatch.setattr(resources, 'check_storage', lambda _: None)
    monkeypatch.setattr(remote, 'check_cancel', lambda _: None)
    monkeypatch.setattr(store, 'job_update', lambda *args, **kw: updates.append((args, kw)))
    def download(**kwargs):
        downloads.append(kwargs)
        (cache / 'tokenizer.json').write_bytes(b'abc')
    monkeypatch.setitem(sys.modules, 'huggingface_hub', SimpleNamespace(snapshot_download=download))
    resources.run_install({'id':'test'})
    assert resources.installed()
    assert downloads[0]['revision'] == resources.REVISION
    assert downloads[0]['cache_dir'] == str(resources.cache_root())
    assert downloads[0]['token'] is False
    assert updates[-1][0] == ('test', 'success')


def test_installer_is_dispatched_without_a_clip_snapshot(monkeypatch):
    from h3workbench import pipeline
    job = {'id': 'installer', 'kind': resources.INSTALL_KIND, 'data': {'worker': 'cpu'}}
    called = []
    monkeypatch.setattr(store, 'job_get', lambda _: job)
    monkeypatch.setattr(resources, 'run_install', lambda current: called.append(current))
    pipeline.execute('installer')
    assert called == [job]
    assert pipeline.resource(job) == 'cpu'


@pytest.mark.parametrize('shared_elsewhere', [False, True])
def test_shared_blob_cache_removal_respects_other_model_references(cache, shared_elsewhere):
    from huggingface_hub.utils._shared_blobs import publish_blob_to_shared_store
    blob = (cache / 'model.bin').resolve()
    digest = hashlib.sha256(blob.read_bytes()).hexdigest()
    assert publish_blob_to_shared_store(blob_path=str(blob), xet_hash=digest,
                                       cache_dir=resources.cache_root(), expected_size=3)
    shared = blob.resolve()
    if shared_elsewhere:
        other = resources.cache_root() / 'models--other--model' / 'blobs' / digest
        other.parent.mkdir(parents=True)
        other.write_bytes(shared.read_bytes())
        assert publish_blob_to_shared_store(blob_path=str(other), xet_hash=digest,
                                           cache_dir=resources.cache_root(), expected_size=3)
    review = resources.removal_plan()
    resources.uninstall({'planId': review['id'], 'digest': review['digest'], 'confirm': resources.RESOURCE_ID})
    assert not resources.installed()
    assert shared.exists() == shared_elsewhere
    if shared_elsewhere:
        assert other.read_bytes() == b'BBB'


def test_external_blob_target_remains_blocked(cache, tmp_path):
    blob = (cache / 'model.bin').resolve()
    outside = tmp_path / 'outside.bin'
    outside.write_bytes(blob.read_bytes())
    blob.unlink()
    blob.symlink_to(outside)
    with pytest.raises(ValueError, match='external|outside'):
        resources.removal_plan()
    assert outside.exists()


def test_second_app_reuses_pinned_snapshot_without_download(cache, tmp_path, monkeypatch):
    original = resources.snapshot_path()
    monkeypatch.setattr(resources, 'DATA', tmp_path / 'second-app')
    assert resources.snapshot_path() == original
    assert resources.installed()
    assert resources.request_install() == {'complete': True}
    updates = []
    monkeypatch.setattr(store, 'job_update', lambda *args, **kw: updates.append((args, kw)))
    monkeypatch.setattr(remote, 'check_cancel', lambda _: None)
    def no_download(**kwargs):
        raise AssertionError('The second app must reuse the installed model')
    monkeypatch.setitem(sys.modules, 'huggingface_hub', SimpleNamespace(snapshot_download=no_download))
    resources.run_install({'id': 'second-app-install'})
    assert updates[-1][0] == ('second-app-install', 'success')
    assert original.exists()


def test_existing_app_cache_remains_at_original_location(cache, monkeypatch):
    import shutil
    legacy = resources.DATA / 'models/transcription'
    shutil.copytree(resources.cache_root(), legacy, symlinks=True)
    assert resources.cache_root() == legacy
    assert resources.installed()
    assert not resources.is_shared()


def test_another_process_using_model_blocks_removal(cache):
    import subprocess
    from pathlib import Path
    module = str(Path(resources.cache.__file__).resolve())
    code = """
import importlib.util, sys
spec = importlib.util.spec_from_file_location('cache', sys.argv[1])
cache = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cache)
with cache.cache_use(sys.argv[2], sys.argv[3]):
    print('using', flush=True)
    sys.stdin.readline()
"""
    process = subprocess.Popen([sys.executable, '-c', code, module, str(resources.cache_root()), resources.MODEL],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert process.stdout.readline().strip() == 'using'
        with pytest.raises(store.Conflict, match='another app'):
            resources.removal_plan()
        assert resources.installed()
    finally:
        process.communicate('done\n', timeout=10)
    assert process.returncode == 0
    assert resources.removal_plan()['canUninstall']
