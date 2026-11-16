import threading
from concurrent.futures import ThreadPoolExecutor
from h3workbench import remote_shared
import pytest


def test_refinement_substages_bind_their_actual_named_operations(tmp_path):
    job={"kind":"refine_extract"}
    expected={"sam":"sam-track","sam-recovery":"sam-recover","alpha":"alpha-extract","face":"face-track"}
    for stage,operation in expected.items():
        assert remote_shared.operation(job,tmp_path/stage)==operation
    with pytest.raises(KeyError):remote_shared.operation(job,tmp_path/"unexpected-stage")


def test_preparation_is_bounded_but_independent_gpu_observation_can_overlap(monkeypatch, tmp_path):
    monkeypatch.setattr(remote_shared, "STAGE_PREPARATION", threading.BoundedSemaphore(1))
    first_prepared, second_prepared, admit_first, finish_gpu = [threading.Event() for _ in range(4)]
    monkeypatch.setattr(remote_shared.remote, "check_cancel", lambda _: None)
    def operation(job, graph, files, directory, profile, admitted):
        if job["id"] == "one":
            first_prepared.set()
            assert admit_first.wait(3)
            admitted()
            assert finish_gpu.wait(3)
        else:
            second_prepared.set()
            admitted()
        return job["id"]
    monkeypatch.setattr(remote_shared, "_execute", operation)
    with ThreadPoolExecutor(max_workers=2) as pool:
        one = pool.submit(remote_shared.execute, {"id": "one"}, {}, {}, tmp_path)
        assert first_prepared.wait(1)
        two = pool.submit(remote_shared.execute, {"id": "two"}, {}, {}, tmp_path)
        assert not second_prepared.wait(.1)
        admit_first.set()
        assert second_prepared.wait(1)
        assert not one.done(), "A second worker can prepare while the first GPU attempt is observed"
        finish_gpu.set()
        assert one.result() == "one" and two.result() == "two"


@pytest.mark.parametrize('selected,expected', [(None, ['h3-swap-pika-v1', 'sam-frame']), ('mask', ['sam-frame'])])
def test_worker_status_combines_operation_installations_without_overriding_selection(monkeypatch, selected, expected):
    monkeypatch.setattr(remote_shared, 'target', lambda _: {'kind': 'local', 'port': 8841})
    monkeypatch.setattr(remote_shared.store, 'setting', lambda *_: {'local': selected} if selected else {})
    monkeypatch.setattr(remote_shared, 'call', lambda _: {
        'health': {'executorId': 'test-executor'},
        'status': {'jobs': [], 'sessions': [], 'gpus': [{'name': 'test GPU'}], 'gpuCompute': []},
        'installations': [
            {'installation': {'id': 'mask'}, 'features': ['sam-frame']},
            {'installation': {'id': 'swap'}, 'features': ['h3-swap-pika-v1']},
        ],
    })
    result = remote_shared.status('local')
    assert result['state'] == 'available'
    assert result['features'] == expected
