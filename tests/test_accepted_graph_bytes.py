"""A runtime failure must leave the exact admitted workflow available to recovery."""
import importlib.util
import json
from pathlib import Path

import pytest


@pytest.mark.parametrize('controller_path', ['runtime/tools/managed-comfy-session.py', 'scripts/worker_session.py'])
@pytest.mark.parametrize('graph_in_stage', [False, True])
def test_submitted_graph_preserves_admitted_bytes_after_failure(tmp_path, controller_path, graph_in_stage):
    spec = importlib.util.spec_from_file_location('graph_byte_controller', Path(__file__).parents[1] / controller_path)
    controller = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(controller)
    directory = tmp_path / 'job'
    directory.mkdir()
    graphfile = directory / 'api.json' if graph_in_stage else tmp_path / 'accepted.json'
    graph = {'1': {'class_type': 'SaveImage', 'inputs': {'filename_prefix': 'été · 第1'}}}
    # Non-ASCII bytes and intentional compact formatting match a valid admission.
    original = json.dumps(graph, ensure_ascii=False, separators=(',', ':')).encode()
    graphfile.write_bytes(original)
    session = controller.Session.__new__(controller.Session)
    session.root = tmp_path / 'session'
    session.root.mkdir()
    session.media = tmp_path
    session.schemas = {'SaveImage': {}}
    session.runtime_pid = 123
    session.preflight = {}
    session.contract = {}
    session.verify_idle = lambda: None
    session.verify_models = lambda *args: None
    session.cleanup = lambda *args, **kwargs: None

    def lost_runtime(path, body=None):
        assert path == '/prompt'
        assert body['prompt'] == graph
        raise ConnectionResetError('owned runtime exited')

    session.api = lost_runtime
    packet = {'directory': str(directory), 'graph': str(graphfile),
              'graph_sha256': controller.sha(graphfile), 'attempt': 'job-stage',
              'job': 'job', 'profile': 'full-offload', 'placement': 'offload',
              'family': {'key': 'h3-test', 'label': 'H3'}, 'compatibility': {}}
    with pytest.raises(ConnectionResetError, match='owned runtime exited'):
        session.stage(packet)
    assert graphfile.read_bytes() == original
    assert (directory / 'api.json').read_bytes() == original
    assert controller.sha(directory / 'api.json') == packet['graph_sha256']
