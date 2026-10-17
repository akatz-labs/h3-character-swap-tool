import json
from pathlib import Path

import pytest
from h3workbench import relay_contract
from runtime import relay_dependency


def test_app_planner_and_installed_worker_have_identical_upstream_source(tmp_path):
    lock = relay_dependency.extract(tmp_path / 'relay')
    assert len(lock['commit']) == 40
    planner = relay_dependency.pure_module('planning')
    assert 'torch' not in vars(planner)
    source = (tmp_path / 'relay/h3_relay/planning.py').read_text()
    scope = {}
    exec(source, scope)
    for count in (22, 43, 120, 480):
        assert planner.plan_windows(count, 124, 18) == scope['plan_windows'](count, 124, 18)
    template = json.loads((Path(__file__).parents[1] / 'templates/render.json').read_text())
    assert template == relay_contract.recipe()


def test_changed_dependency_is_rejected_before_import(tmp_path, monkeypatch):
    lock, _ = relay_dependency.source()
    (tmp_path / 'runtime').mkdir()
    (tmp_path / 'vendor').mkdir()
    (tmp_path / lock['archive']).write_bytes(b'changed source')
    (tmp_path / 'runtime/relay-lock.json').write_text(json.dumps(lock))
    monkeypatch.setattr(relay_dependency, 'ROOT', tmp_path)
    relay_dependency.source.cache_clear()
    try:
        with pytest.raises(ValueError, match='integrity'):
            relay_dependency.source()
    finally:
        relay_dependency.source.cache_clear()


def test_native_video_and_continuation_descriptors_are_required_and_confined(tmp_path):
    (tmp_path / 'video.mp4').touch()
    (tmp_path / 'history.mkv').touch()
    report = {'complete': True, 'source_frames': 43, 'history_frames': 18}
    video = {'filename': 'video.mp4', 'subfolder': '', 'type': 'output'}
    master = {**video, 'filename': 'history.mkv'}
    result = {'807': {'h3_relay_report': [report]}, '809': {'images': [video]},
              'save_history': {'h3_relay_history': [master]}}
    history = {'prompt': {'outputs': result}}
    assert relay_contract.outputs(history, tmp_path, continuation=True) == (
        report, tmp_path / 'video.mp4', tmp_path / 'history.mkv')
    video['filename'] = '../outside.mp4'
    with pytest.raises(ValueError, match='missing or outside'):
        relay_contract.outputs(history, tmp_path)
    video['filename'] = 'video.mp4'
    del result['save_history']
    with pytest.raises(ValueError, match='continuation master'):
        relay_contract.outputs(history, tmp_path, continuation=True)
