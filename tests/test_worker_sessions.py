import copy
import hashlib
import importlib.util
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

import pytest

from h3workbench import remote_sessions, session_policy, store


@pytest.fixture
def controller():
    source = Path(__file__).parents[1] / "scripts/worker_session.py"
    spec = importlib.util.spec_from_file_location("worker_session", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_idle_policy_and_new_work():
    assert session_policy.idle_action(299, False) is None
    assert session_policy.idle_action(300, False) == "unload"
    assert session_policy.idle_action(899, True) is None
    assert session_policy.idle_action(900, True) == "stop"
    assert session_policy.idle_action(90000, False, pending=True) is None


def test_model_family_changes_on_lora_but_not_job_inputs():
    graph = {
        "33": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sam3.1.safetensors"}},
        "1": {"class_type": "LoadImage", "inputs": {"image": "a.png"}},
    }
    other = copy.deepcopy(graph)
    other["1"]["inputs"]["image"] = "b.png"
    other["999"] = other.pop("33")
    assert session_policy.family(graph) == session_policy.family(other)
    other["3"] = {"class_type": "LoraLoaderModelOnly", "inputs": {"lora_name": "v2.safetensors", "strength_model": 1}}
    assert session_policy.family(graph)["key"] != session_policy.family(other)["key"]
    assert session_policy.attempt_id("job-1", "stage-a") != session_policy.attempt_id("job-1", "stage-b")


def test_residency_preserves_explicit_fallbacks():
    assert session_policy.placement("full") == "full"
    assert session_policy.placement("alpha-offload") == "offload"
    assert session_policy.placement("full-layer-stream") == "layer-stream"
    with pytest.raises(ValueError):
        session_policy.placement("invented")


def test_pending_queue_is_not_limited_by_browser_history(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA", tmp_path)
    store.init()
    old = store.job_create("mask", "old", "s", {"worker": "arch"})
    with store.db() as db:
        for n in range(310):
            db.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?)", (f"new-{n}", "mask", str(n), "s", "success", store.now()+n+1, store.now(), "{}"))
    assert old not in {j["id"] for j in store.jobs()}
    assert [j["id"] for j in store.pending_jobs()] == [old]


def test_atomic_admission_dedup_and_closed_session(tmp_path, monkeypatch):
    monkeypatch.setattr(remote_sessions, "DATA", tmp_path / "data")
    root = tmp_path / "session"
    (root / "inbox").mkdir(parents=True)
    state = {"session_id": "owned", "owner": remote_sessions.owner(), "active": None, "accepting": True}
    (root / "state.json").write_text(json.dumps(state))
    monkeypatch.setattr(remote_sessions.remote, "remote_path", lambda p, w: str(p))

    def local_ssh(worker, code, *args):
        output = io.StringIO()
        with redirect_stdout(output):
            exec(code, {})
        return output.getvalue()

    monkeypatch.setattr(remote_sessions.remote, "ssh", local_ssh)
    info = {"root": str(root), "session_id": "owned"}
    packet = {"attempt": "job-stage", "graph_sha256": "a"}
    assert remote_sessions.enqueue("arch", info, packet)
    assert remote_sessions.enqueue("arch", info, packet)
    assert len(list((root / "inbox").glob("*.json"))) == 1
    with pytest.raises(AssertionError, match="Conflicting"):
        remote_sessions.enqueue("arch", info, {**packet, "graph_sha256": "b"})
    (root / "stop.json").write_text("{}")
    assert not remote_sessions.enqueue("arch", info, {"attempt": "new"})


def test_multi_stage_output_is_verified_and_isolated(controller, tmp_path):
    session = controller.Session.__new__(controller.Session)
    session.root = tmp_path / "session"
    session.root.mkdir()
    session.media = tmp_path
    session.schemas = {"SaveImage": {}}
    session.runtime_pid = 123
    session.preflight = {"args": ["--highvram"]}
    session.process = SimpleNamespace(poll=lambda: None)
    session.publish = lambda *args, **kwargs: None
    session.verify_idle = lambda: None
    calls = []

    def cleanup(operation, **kwargs):
        calls.append(operation)
        if operation == "prepare":
            p = Path(kwargs["output_directory"])
            p.mkdir(parents=True)
            (p / "image.png").write_bytes(b"distinct stage output")
        return {"model_entries": 2}

    session.cleanup = cleanup
    session.api = lambda path, body=None: ({"prompt_id": "p"} if path == "/prompt" else
            {"p": {"status": {"status_str": "success"}, "outputs": {"1": {"images": [{"filename": "image.png", "subfolder": ""}]}}}} if path == "/history/p" else {})
    directory = tmp_path / "job" / "alpha"
    graphfile = tmp_path / "api.json"
    graphfile.write_text(json.dumps({"1": {"class_type": "SaveImage", "inputs": {}}}))
    packet = {"directory": str(directory), "graph": str(graphfile), "graph_sha256": controller.sha(graphfile),
              "attempt": "job-alpha", "job": "job", "profile": "alpha-offload", "placement": "offload", "family": {"key": "alpha", "label": "AlphaGen"}}
    session.contract = {"pinned": True}
    session.stage(packet)
    assert (directory / "output/image.png").read_bytes() == b"distinct stage output"
    receipt = json.loads((directory / "output-receipt.json").read_text())
    assert receipt["files"][0]["sha256"] == hashlib.sha256(b"distinct stage output").hexdigest()
    complete = json.loads((directory / "complete.json").read_text())
    assert complete["runtime_pid"] == 123 and complete["queue_empty"]
    assert not (session.root / "output/job-alpha").exists()
    assert calls == ["prepare", "settle"]
    with pytest.raises(AssertionError, match="Existing stage"):
        session.stage(packet)


def test_inbox_beats_idle_shutdown_boundary(controller, tmp_path, monkeypatch):
    clock = [900.0]
    monkeypatch.setattr(controller.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(controller.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0]+1000))
    session = controller.Session.__new__(controller.Session)
    session.root = tmp_path
    (tmp_path / "inbox").mkdir()
    (tmp_path / "inbox/queued.json").write_text(json.dumps({"attempt": "new", "directory": str(tmp_path)}))
    session.spec = {"stop_seconds": 900, "unload_seconds": 300}
    session.spec["owner"] = "test"
    session.env = tmp_path / "env"
    session.local = tmp_path / "local"
    session.last_activity = 0
    session.active = None
    session.unloaded = False
    session.process = SimpleNamespace(poll=lambda: None)
    session.start = lambda: None
    session.publish = lambda *args, **kwargs: None
    ran = []

    def stage(packet):
        ran.append(packet["attempt"])
        # A long active job is handled wholly within stage(), never by idle.
        clock[0] += 5000

    session.stage = stage
    session.run()
    assert ran == ["new"]
    assert session.state == "stopping"
    assert session.last_activity == 5900


def test_cleanup_retries_lost_notification_with_same_identity(controller, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(controller.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(controller.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0]+seconds))
    session = controller.Session.__new__(controller.Session)
    session.spec = {"session_id": "owned"}
    session.verify_idle = lambda: None
    requests = []

    def api(path, body=None):
        if body:
            requests.append(body)
            return {"reset_after": 0}
        return {"reset_epoch": 1 if len(requests) > 1 else 0}

    session.api = api
    assert session.cleanup("settle")["reset_epoch"] == 1
    assert len(requests) == 2 and requests[0] == requests[1]


def test_output_reference_mapping_preserves_input_paths(controller):
    original = {"mask_files": [{"path": "/session/output/stage/mask.npz"}],
                "input": "/input/source.mkv", "text": '{"output":"/session/output/stage/out.mp4"}'}
    mapped = controller.output_references(original, "/session/output/stage", "/job/runtime/output")
    assert mapped["mask_files"][0]["path"] == "/job/runtime/output/mask.npz"
    assert mapped["input"] == original["input"]
    assert mapped["text"] == '{"output":"/job/runtime/output/out.mp4"}'


def test_auto_placement_prefers_warm_and_never_waits_for_busy_worker():
    workers = {"arch": {"state": "available"}, "fey": {"state": "warm_idle", "family": {"label": "SAM"}}}
    job = {"kind": "mask", "data": {}}
    assert session_policy.choose_worker(job, workers) == "fey"
    assert session_policy.choose_worker(job, workers, excluded={"fey"}) == "arch"
    workers["fey"]["state"] = "running"
    assert session_policy.choose_worker(job, workers) == "arch"
    assert session_policy.choose_worker(job, workers, excluded={"arch"}) is None


def test_claim_is_exclusive_and_cancelled_rows_cannot_be_started(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA", tmp_path)
    store.init()
    id = store.job_create("mask", "clip", "shot", {"worker": "auto"})
    assert store.claim_job(id, "arch", "prep")
    assert not store.claim_job(id, "fey", "prep")
    job = store.job_get(id)
    assert job["data"]["worker"] == "arch" and job["data"]["execution_resource"] == "prep"
    another = store.job_create("mask", "other", "shot", {"worker": "fey"})
    store.job_update(another, "cancelled")
    assert not store.claim_job(another)


def test_input_cleanup_preserves_originals_and_changed_or_unowned_files(controller, tmp_path):
    root = tmp_path / "session"
    (root / "inbox").mkdir(parents=True)
    (root / "finished_jobs").mkdir()
    env = tmp_path / "env"
    target_root = env / "ComfyUI/input"
    target_root.mkdir(parents=True)
    source = tmp_path / "original.bin"
    source.write_bytes(b"original")
    items = []
    for name, data, created in [("own", b"original", True), ("changed", b"changed", True), ("preexisting", b"original", False)]:
        target = target_root / name
        target.write_bytes(data)
        items.append({"target": str(target), "source": str(source), "created": created, "sha256": controller.sha(source)})
    (root / "inbox/stage.accepted").write_text(json.dumps({"job": "job", "staged_inputs": items}))
    (root / "finished_jobs/job.json").write_text(json.dumps({"owner": "owned", "state": "success", "job": "job"}))
    controller.collect_finished_inputs(root, env, "owned")
    assert source.read_bytes() == b"original"
    assert not (target_root / "own").exists()
    assert (target_root / "changed").exists() and (target_root / "preexisting").exists()
    receipt = json.loads((root / "input-release/job.json").read_text())
    assert len(receipt["deleted"]) == 1 and len(receipt["retained"]) == 1


def test_prepared_inputs_keep_exact_bytes_and_reject_changes(tmp_path):
    from h3workbench import media
    target = tmp_path / "source.mkv"
    calls = []

    def build():
        calls.append(True)
        target.write_bytes(b"frozen prepared video")

    recipe = {"source": "immutable", "range": [0, 26]}
    assert media.frozen_file(target, recipe, build) == target
    assert media.frozen_file(target, recipe, build) == target
    assert len(calls) == 1
    with pytest.raises(ValueError, match="Prepared input changed"):
        media.frozen_file(target, {**recipe, "range": [1, 27]}, build)
    target.write_bytes(b"external modification")
    with pytest.raises(ValueError, match="Prepared input changed"):
        media.frozen_file(target, recipe, build)


def test_local_status_storage_failure_keeps_shared_state(controller, tmp_path, monkeypatch):
    session = controller.Session.__new__(controller.Session)
    session.root = tmp_path / 'shared'
    session.local = tmp_path / 'local'
    session.root.mkdir(); session.local.mkdir()
    session.spec = {'session_id': 'owned', 'owner': 'owner', 'worker': 'arch', 'port': 8341, 'contract': str(tmp_path/'contract')}
    (tmp_path/'contract').write_text('{}')
    session.boot_identity = {'pid': 1}; session.runtime_identity = {'pid': 2}
    session.env = tmp_path / 'environment'; session.last_activity = controller.time.monotonic()
    session.state = 'warm_idle'; session.active = None; session.last_completed = 'stage'; session.family = {'label': 'SAM'}; session.placement = 'full'
    original = controller.save
    def save(path, value):
        if Path(path).is_relative_to(session.local):
            raise OSError(28, 'No space left on device')
        original(path, value)
    monkeypatch.setattr(controller, 'save', save)
    session.publish()
    state = json.loads((session.root/'state.json').read_text())
    assert state['state'] == 'warm_idle' and 'No space' in state['local_state_warning']


@pytest.mark.parametrize('queue_active', [False, True])
def test_stop_releases_despite_failed_status_write_but_preserves_foreign_queue(controller, tmp_path, monkeypatch, queue_active):
    session = controller.Session.__new__(controller.Session)
    session.root = tmp_path; session.env = tmp_path/'env'; session.spec = {'session_id': 'owned', 'owner': 'owner'}
    session.runtime_pid = 123; session.runtime_identity = {'pid': 123, 'start': 'one', 'boot': 'boot'}
    session.boot_identity = {'pid': 1}; session.ready = True; session.log = io.StringIO()
    session.process = SimpleNamespace(pid=122, poll=lambda: None, wait=lambda timeout: 0)
    session.publish = lambda **kwargs: (_ for _ in ()).throw(OSError(28, 'No space'))
    session.api = lambda path: {'session_id': 'owned'} if path.endswith('session') else {'queue_running': [[1,'foreign']] if queue_active else [], 'queue_pending': []}
    monkeypatch.setattr(controller, 'process_identity', lambda pid: session.runtime_identity)
    monkeypatch.setattr(controller, 'runtimes', lambda: [(123, [])])
    monkeypatch.setattr(controller.os, 'getpgid', lambda pid: 122)
    monkeypatch.setattr(controller, 'compute_pids', lambda: set())
    kills = []; monkeypatch.setattr(controller.os, 'killpg', lambda *args: kills.append(args))
    # The final publication can fail too; the shared release receipt precedes it.
    with pytest.raises(OSError): session.stop()
    receipt = json.loads((tmp_path/'release.json').read_text())
    assert receipt['gpu_released'] is (not queue_active)
    assert bool(kills) is (not queue_active)


def test_restart_reconciles_verified_attempt_and_preserves_cancellation(tmp_path, monkeypatch):
    from h3workbench import pipeline
    monkeypatch.setattr(store, 'DATA', tmp_path); store.init()
    recovered = store.job_create('mask', 'c', 's', {'worker': 'arch', 'cancel_requested': True})
    uncertain = store.job_create('render', 'c', 's', {'worker': 'fey'})
    store.job_update(recovered, 'cancelling'); store.job_update(uncertain, 'running')
    monkeypatch.setattr(remote_sessions, 'enabled', lambda: True)
    monkeypatch.setattr(remote_sessions, 'recoverable', lambda j: j['id'] == recovered)
    monkeypatch.setattr(pipeline.threading, 'Thread', lambda **kw: SimpleNamespace(start=lambda: None))
    pipeline.start()
    assert store.job_get(recovered)['state'] == 'queued'
    assert store.job_get(recovered)['data']['cancel_requested']
    assert store.job_get(uncertain)['state'] == 'interrupted'


def test_preparation_is_bounded_while_gpu_and_cpu_work_continue(tmp_path, monkeypatch):
    from h3workbench import pipeline
    monkeypatch.setattr(store, 'DATA', tmp_path); store.init()
    store.set_setting('unified_workers', True)
    active = store.job_create('mask', 'c', 's', {'worker': 'arch', 'execution_resource': 'arch'})
    store.job_update(active, 'running')
    first = store.job_create('mask', 'first', 's', {'worker': 'arch'})
    second = store.job_create('mask', 'second', 's', {'worker': 'arch'})
    cpu = store.job_create('cuts', 'c', None, {'worker': 'arch'})
    future = SimpleNamespace(done=lambda: False)
    monkeypatch.setattr(pipeline, 'ACTIVE', {active: future})
    cycles = [0]
    def wait(seconds):
        cycles[0] += 1
        return cycles[0] > 3
    monkeypatch.setattr(pipeline, 'STOP', SimpleNamespace(wait=wait))
    submitted = []
    def submit(fn, id):
        submitted.append(id)
        if id == first:
            store.job_update(id, execution_resource='arch', gpu_waiting=True)
        return future
    monkeypatch.setattr(pipeline, 'EXEC', SimpleNamespace(submit=submit))
    pipeline.scheduler()
    assert submitted == [first, cpu]
    assert store.job_get(second)['state'] == 'queued'


def test_streaming_allocator_is_an_explicit_restart_policy():
    assert session_policy.allocator('full') == session_policy.allocator('full-offload') == 'default'
    assert session_policy.allocator('full-stream') == session_policy.allocator('full-layer-stream') == 'native-expandable'


@pytest.mark.parametrize('resource,waiting', [('prep',False),('arch',True),('cpu',False)])
def test_cancel_remaining_processing_does_not_stop_another_gpu_job(tmp_path, monkeypatch, resource, waiting):
    from fastapi.testclient import TestClient
    from h3workbench.app import app
    monkeypatch.setattr(store,'DATA',tmp_path);store.init()
    id=store.job_create('mask','queued-clip','s',{'worker':'arch','execution_resource':resource,'gpu_waiting':waiting})
    other=store.job_create('render','active-clip','s',{'worker':'arch','execution_resource':'arch'})
    store.job_update(id,'running');store.job_update(other,'running')
    client=TestClient(app)
    assert client.post(f'/api/jobs/{id}/cancel',json={}).status_code==400
    assert client.post(f'/api/jobs/{id}/cancel',json={'confirm_running':True}).status_code==200
    assert store.job_get(id)['state']=='cancelling'
    assert store.job_get(other)['state']=='running' and not store.job_get(other)['data'].get('cancel_requested')


def test_close_observes_an_already_requested_shutdown(monkeypatch):
    monkeypatch.setattr(remote_sessions,'registry',lambda worker:{'session_id':'owned'})
    values=iter([{'state':{'state':'stopping'},'release':None},
                 {'state':{'state':'stopped'},'release':{'stopped':True,'gpu_released':True}}])
    monkeypatch.setattr(remote_sessions,'inspect',lambda worker:next(values))
    monkeypatch.setattr(remote_sessions,'release_idle',lambda worker:pytest.fail('Do not request shutdown twice'))
    remote_sessions.close('arch')


@pytest.mark.parametrize('compute,runtimes', [('999, other, 1000',[123]),('',[123,999])])
def test_owned_idle_status_preserves_external_contention(monkeypatch, compute, runtimes):
    from h3workbench import remote
    monkeypatch.setattr(remote,'ssh',lambda *a,**kw:json.dumps({'compute':compute,'runtimes':runtimes,'gpu':'4090'}))
    monkeypatch.setattr(remote_sessions,'enabled',lambda:True)
    monkeypatch.setattr(remote_sessions,'status',lambda worker:{'state':'warm_idle','runtime_pid':123,'owned':True})
    assert remote.status('arch')['state']=='external_busy'


def test_none_render_affinity_does_not_reuse_a_person_remover_chain():
    workers={'fey':{'state':'warm_idle','family':{'label':'H3','loras':['h3-removal-continuation/B-step2000.safetensors']}},'arch':{'state':'warm_idle','family':{'label':'H3','loras':['minimax_h3_fl2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors']}}}
    assert session_policy.choose_worker({'kind':'render','data':{'lora':{'filename':None}}},workers)=='arch'


def test_warm_affinity_requires_compatible_placement():
    workers={'fey':{'state':'warm_idle','placement':'offload','family':{'label':'SAM'}},'arch':{'state':'warm_idle','placement':'full','family':{'label':'SAM'}}}
    assert session_policy.choose_worker({'kind':'mask','data':{}},workers)=='arch'
