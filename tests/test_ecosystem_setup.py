"""Default-path installation publication, reuse and failure preservation."""
import argparse
import importlib.util
import json
from pathlib import Path
import pytest

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location('source_setup', ROOT / 'scripts/setup_source_worker.py')
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


@pytest.fixture
def installation(tmp_path, monkeypatch):
    monkeypatch.setenv('AKATZ_DATA_ROOT', str(tmp_path / 'shared'))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    monkeypatch.delenv('AKATZ_MODELS_ROOT', raising=False)
    calls = []
    def materialize(root, models, port, cache, tools_root):
        calls.append(root)
        for name in ['binding/installation.json', 'binding/runtime-contract.json', 'binding/h3-adapter.mjs',
                'binding/resources.json', 'runtime/installation-receipt.json', 'runtime/runtime-contract.json',
                'runtime/tools/managed-comfy-session.py', 'runtime/tools/managed-comfy-cache.py', 'resource-catalog.json']:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('{}')
        (root / 'settings.json').write_text(json.dumps({'cgExecutable': __file__, 'pythonExecutable': __file__}))
        return 'test-installation'
    monkeypatch.setattr(setup, 'prepare', materialize)
    return argparse.Namespace(root=None, models=None, cache=None, comfy_port=8188, executor_port=18841), calls


def test_default_setup_is_selected_and_idempotent(installation):
    args, calls = installation
    setup.setup(args)
    first = setup.shared_paths()
    saved = first['installation']
    assert Path(saved['root']).parent == Path(first['runtimesRoot'])
    assert saved['modelsRoot'] == first['modelsRoot']
    setup.setup(args)
    assert setup.shared_paths()['installation'] == saved
    assert len(calls) == 1


def test_modified_or_partial_runtime_is_preserved(installation):
    args, _ = installation
    setup.setup(args)
    saved = setup.shared_paths()['installation']
    changed = Path(saved['root']) / 'binding/h3-adapter.mjs'
    changed.write_text('external modification')
    with pytest.raises(ValueError, match='changed'):
        setup.setup(args)
    assert changed.read_text() == 'external modification'
    assert setup.shared_paths()['installation'] == saved


def test_failed_install_does_not_publish_selection(installation, monkeypatch):
    args, _ = installation
    def fail(root, *_):
        root.mkdir(parents=True)
        raise RuntimeError('dependency installation failed')
    monkeypatch.setattr(setup, 'prepare', fail)
    with pytest.raises(RuntimeError, match='dependency'):
        setup.setup(args)
    assert setup.shared_paths()['installation'] is None
    with pytest.raises(ValueError, match='incomplete'):
        setup.setup(args)
