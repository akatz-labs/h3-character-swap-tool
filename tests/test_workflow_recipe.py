"""Release recipe is self-contained and cannot recapture unrelated environments."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('build_recipe', Path(__file__).parents[1] / 'runtime/build_recipe.py')
recipe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recipe)


def test_focused_recipe_has_exact_node_and_model_inventory(tmp_path):
    target = tmp_path / 'recipe'
    recipe.build(target)
    receipt = json.loads((target / 'capture-receipt.json').read_text())
    assert receipt['profile'] == 'h3-relay-toolkit-v2'
    assert {Path(p).parts[1] for p in receipt['files']} == {'h3_relay','h3_workbench_runtime'}
    assert {'nodes/h3_workbench_runtime/remake.py',
            'nodes/h3_workbench_runtime/remake_region.py',
            'nodes/h3_workbench_runtime/LICENSE-VIGGLE-CODE'} <= receipt['files'].keys()
    assert not any('__pycache__' in p or p.endswith('.pyc') for p in receipt['files'])
    for name, expected in receipt['files'].items():
        data = (target / name).read_bytes()
        assert len(data) == expected['bytes']
        assert hashlib.sha256(data).hexdigest() == expected['sha256']
    models = json.loads((target / 'model-identities.json').read_text())
    catalog = json.loads((Path(__file__).parents[1] / 'docker/resource-catalog.json').read_text())
    assert set(models['models']) == {item['relativePath'] for item in catalog['models']}
    assert len(models['models']) == 8
    required = {m['id'] for m in catalog['models'] if m['requirement'] == 'required'}
    assert len(required) == 6
    assert required == {m for f in catalog['features'] if not f['optional'] for m in f['models']}
    assert not any('whisper' in m['relativePath'] for m in catalog['models'])
    # Executor resource registration requires both SHA-256 and ComfyGit identity.
    for model in catalog['models']:
        assert len(model['shortHash']) == 16
        int(model['shortHash'], 16)
    assert next(m for m in catalog['models'] if m['id'] == 'model-remake-swap')['shortHash'] == '6eb342239da65204'
    assert models['models']['loras/viggle-remake/comfyui/viggle_remake_swap_comfyui.safetensors']['sha256'] == '9967f1cd71a81d9263d1148e3eebb951e24579d9388b9ae671b966ca1f0a1c3b'
    assert models['comfy_revision'] == receipt['comfy_revision']
    assert not any('ltx' in name.lower() or 'onnx' in name.lower() for name in models['models'])
    before = (target / 'capture-receipt.json').read_bytes()
    with pytest.raises(ValueError, match='immutable'):
        recipe.build(target)
    assert (target / 'capture-receipt.json').read_bytes() == before


def test_focused_graph_cannot_fall_back_to_an_old_lab_worker(tmp_path, monkeypatch):
    from h3workbench import remote
    monkeypatch.setattr(remote, 'ssh', lambda *a, **k: pytest.fail('Must not touch an old worker'))
    with pytest.raises(RuntimeError, match='matching Relay worker recipe'):
        remote.execute({'data': {}}, {'1': {'class_type': 'H3RelayWindowPlan', 'inputs': {}}}, {}, tmp_path)
