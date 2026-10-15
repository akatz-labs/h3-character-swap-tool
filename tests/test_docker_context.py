"""A release context must not pick up credentials or state beside its manifest."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    'docker_context', Path(__file__).parents[1] / 'docker/prepare_context.py')
context = importlib.util.module_from_spec(spec)
spec.loader.exec_module(context)


def payload(root, path='app.py'):
    app = root / 'app.py'
    app.write_bytes(b'print("H3")\n')
    entries = [{'path': path, 'bytes': app.stat().st_size,
                'sha256': hashlib.sha256(app.read_bytes()).hexdigest()}]
    manifest = {'appId': 'h3-workbench', 'gpuDependencies': False, 'files': entries,
                'revision': hashlib.sha256(json.dumps(entries, sort_keys=True,
                                                       separators=(',', ':')).encode()).hexdigest()}
    (root / 'payload-manifest.json').write_text(json.dumps(manifest))
    (root / 'desktop').mkdir()
    (root / 'desktop/launch.json').write_text('{}')
    return manifest


def test_known_launch_metadata_is_allowed(tmp_path):
    manifest = payload(tmp_path)
    assert context.verify_payload(tmp_path) == manifest


@pytest.mark.parametrize('extra', ['.env', 'state/jobs.json'])
def test_unlisted_credentials_or_job_state_are_rejected(tmp_path, extra):
    payload(tmp_path)
    path = tmp_path / extra
    path.parent.mkdir(exist_ok=True)
    path.write_text('private fixture')
    with pytest.raises(ValueError, match='unexpected='):
        context.verify_payload(tmp_path)


@pytest.mark.parametrize('path', ['../app.py', '/app.py', './app.py'])
def test_manifest_cannot_escape_or_alias_paths(tmp_path, path):
    payload(tmp_path, path)
    with pytest.raises(ValueError, match='Invalid or duplicate payload path'):
        context.verify_payload(tmp_path)


def test_modified_payload_is_rejected(tmp_path):
    payload(tmp_path)
    (tmp_path / 'app.py').write_bytes(b'print("XX")\n')
    with pytest.raises(ValueError, match='Payload content differs'):
        context.verify_payload(tmp_path)


def test_symlink_is_rejected_even_if_bytes_match(tmp_path):
    payload(tmp_path)
    original = tmp_path / 'app.py'
    target = tmp_path.parent / (tmp_path.name + '-external.py')
    target.write_bytes(original.read_bytes())
    original.unlink()
    original.symlink_to(target)
    with pytest.raises(ValueError, match='Payload links require review'):
        context.verify_payload(tmp_path)
