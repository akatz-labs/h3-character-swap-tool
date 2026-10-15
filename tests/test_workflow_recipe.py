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
    assert receipt['profile'] == 'h3-character-swap-v1'
    assert {Path(p).parts[1] for p in receipt['files']} == {'h3_character_swap'}
    assert not any('__pycache__' in p or p.endswith('.pyc') for p in receipt['files'])
    for name, expected in receipt['files'].items():
        data = (target / name).read_bytes()
        assert len(data) == expected['bytes']
        assert hashlib.sha256(data).hexdigest() == expected['sha256']
    models = json.loads((target / 'model-identities.json').read_text())
    assert len(models['models']) == 7
    assert models['comfy_revision'] == receipt['comfy_revision']
    assert not any('ltx' in name.lower() or 'onnx' in name.lower() for name in models['models'])
    before = (target / 'capture-receipt.json').read_bytes()
    with pytest.raises(ValueError, match='immutable'):
        recipe.build(target)
    assert (target / 'capture-receipt.json').read_bytes() == before


def test_focused_graph_cannot_fall_back_to_an_old_lab_worker(tmp_path, monkeypatch):
    from h3workbench import remote
    monkeypatch.setattr(remote, 'ssh', lambda *a, **k: pytest.fail('Must not touch an old worker'))
    with pytest.raises(RuntimeError, match='matching focused worker recipe'):
        remote.execute({'data': {}}, {'1': {'class_type': 'H3CharacterSwapShot', 'inputs': {}}}, {}, tmp_path)
