"""Hub admission to durable Workbench-owned ComfyGit sessions."""
import fcntl
import hashlib
import json
from pathlib import Path
import shutil
import threading
import time
import uuid

from . import remote, store, session_policy
from .config import DATA, MEDIA, ROOT, WORKERS

LOCK = threading.RLock()


def enabled():
    return store.setting("unified_workers", False)


def runtime_signature(worker):
    sources = [ROOT / "scripts/worker_session.py", ROOT / "templates" / f"unified-runtime-{worker}.json"]
    sources += sorted((ROOT / "worker_nodes/h3_workbench_masks").glob("*.py"))
    return hashlib.sha256(b"".join(p.name.encode() + b"\0" + p.read_bytes() for p in sources)).hexdigest()


def registry(worker):
    path = DATA / "worker-sessions" / (worker + ".json")
    return json.loads(path.read_text()) if path.exists() else None


def owner():
    path = DATA / "worker-sessions" / "owner.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with LOCK:
        if not path.exists():
            with path.open("x") as file:
                file.write(json.dumps({"id": uuid.uuid4().hex}))
        return json.loads(path.read_text())["id"]


def save_registry(worker, info):
    path = DATA / "worker-sessions" / (worker + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".pending")
    temporary.write_text(json.dumps(info, indent=2))
    temporary.replace(path)


def inspect(worker, info=None, directory=None):
    info = info or registry(worker)
    if not info:
        return None
    root = remote.remote_path(info["root"], worker)
    stage = remote.remote_path(directory, worker) if directory else None
    code = f'''from pathlib import Path
import json
root=Path({root!r})
def read(p):return json.loads(p.read_text()) if p.exists() else None
state=read(root/'state.json')
alive=False
if state and state.get('controller'):
 identity=state['controller'];p=Path('/proc')/str(identity['pid'])
 try:
  raw=(p/'stat').read_text();fields=raw[raw.rfind(')')+2:].split();start=fields[19]
  alive=fields[0] not in ['Z','X'] and start==identity['start'] and Path('/proc/sys/kernel/random/boot_id').read_text().strip()==identity['boot']
 except OSError:pass
runtime_alive=False
if state and state.get('runtime'):
 identity=state['runtime'];p=Path('/proc')/str(identity['pid'])
 try:
  raw=(p/'stat').read_text();fields=raw[raw.rfind(')')+2:].split();start=fields[19]
  runtime_alive=fields[0] not in ['Z','X'] and start==identity['start'] and Path('/proc/sys/kernel/random/boot_id').read_text().strip()==identity['boot']
 except OSError:pass
stage=Path({stage!r}) if {stage is not None!r} else None
print(json.dumps({{'state':state,'alive':alive,'runtime_alive':runtime_alive,'release':read(root/'release.json'),'failure':read(root/'failure.json'),
 'stage':{{n:read(stage/(n+'.json')) for n in ['status','complete','failure','cancelled']}} if stage else None}}))
'''
    result = json.loads(remote.ssh(worker, code, 20))
    state = result["state"]
    if state:
        if state["session_id"] != info["session_id"] or state["owner"] != owner():
            raise RuntimeError("Worker session identity mismatch; inspect ownership")
    return result


def status(worker):
    info = registry(worker)
    if not info:
        return None
    value = inspect(worker, info)
    state = value["state"]
    if state and value["alive"] and (value["runtime_alive"] or state["state"] == "starting") and state["state"] not in {"stopped", "needs_inspection"}:
        return {"worker": worker, "state": state["state"], "environment": state["environment"],
                "family": state["family"], "active_attempt": state["active"],
                "placement": state.get("placement"),
                "session_id": state["session_id"], "idle_seconds": state["idle_seconds"],
                "runtime_pid": (state.get("runtime") or {}).get("pid"),
                "unload_seconds": state["unload_seconds"], "stop_seconds": state["stop_seconds"],
                "owned": True}
    if value["release"] and value["release"].get("stopped") and value["release"].get("gpu_released"):
        return None
    if state or value["failure"]:
        return {"worker": worker, "state": "needs_inspection", "owned": True,
                "error": "Session ended without verified release; inspect its receipt"}
    return None


def release_idle(worker):
    info = registry(worker)
    if not info:
        return {"released": True, "already_stopped": True}
    root = remote.remote_path(info["root"], worker)
    session_id = info["session_id"]
    code = f'''from pathlib import Path
import json,fcntl
root=Path({root!r})
with (root/'inbox.lock').open('a') as lock:
 fcntl.flock(lock,fcntl.LOCK_EX)
 state=json.loads((root/'state.json').read_text())
 assert state['session_id']=={session_id!r} and state['owner']=={owner()!r}, 'Session ownership mismatch'
 assert not state.get('active') and state['state'] in ['ready','warm_idle','unloaded_idle','stopped'], 'Worker is active; use job cancellation'
 assert not list((root/'inbox').glob('*.json')), 'Accepted work is pending'
 if state['state']!='stopped':
  p=root/'stop.json';t=p.with_suffix('.pending');t.write_text('{{}}');t.replace(p)
 print(json.dumps({{'requested':True,'session_id':state['session_id']}}))
'''
    return json.loads(remote.ssh(worker, code))


def close(worker, timeout=90):
    if not registry(worker):
        return
    value = inspect(worker)
    if value["release"] and value["release"].get("stopped") and value["release"].get("gpu_released"):
        return
    if (value.get("state") or {}).get("state") != "stopping":
        try:
            release_idle(worker)
        except RuntimeError:
            # A prior idle release may win between inspection and admission.
            # Observe that shutdown rather than sending another stop request.
            value = inspect(worker)
            if (value.get("state") or {}).get("state") != "stopping":
                raise
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        value = inspect(worker)
        if value["release"]:
            if value["release"].get("stopped") and value["release"].get("gpu_released"):
                return
            raise RuntimeError("Session release needs inspection")
        time.sleep(.5)
    raise RuntimeError("Owned idle session did not release before timeout")


def launch(worker, directory, job=None, profile="full", *, test_idle_policy=None):
    # Raw status is intentional: adoption requires our registry, and a fresh
    # session must never interpret an arbitrary runtime as its warm worker.
    live = remote.status(worker, owned=False)
    if live["state"] != "available":
        raise remote.Busy(f"{worker}: {live['state']}; waiting for GPU ownership")
    contract = json.loads((ROOT / "templates" / f"unified-runtime-{worker}.json").read_text())
    node_sha, bundle = remote.workbench_node_bundle()
    target = remote.remote_path(bundle, worker)
    home = WORKERS[worker]["home"]
    installation = json.loads(remote.ssh(worker, f'''from pathlib import Path
import subprocess,json
target=Path({target!r})
command=[{str(Path(home)/'.local/bin/cg')!r},'-e',{contract['comfygit_environment']!r},'node','dev-link','h3_workbench_masks','--path',str(target),'--name','h3_workbench_masks','--force']
r=subprocess.run(command,capture_output=True,text=True)
assert r.returncode==0,r.stderr or r.stdout
print(json.dumps({{'target':str(target),'sha256':{node_sha!r}}}))''', 120))
    (directory / "workbench-node-install.json").write_text(json.dumps(installation, indent=2))
    contract["runtime_sources"]["h3_workbench_masks"] = {"root": target, "python_sha256": {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in bundle.glob("*.py")}}
    session_id = "session-" + uuid.uuid4().hex
    root = MEDIA / "sessions" / session_id
    root.mkdir(parents=True)
    script = root / "worker_session.py"
    shutil.copy2(ROOT / "scripts/worker_session.py", script)
    contract_path = root / "runtime-contract.json"
    contract_path.write_text(json.dumps(contract, indent=2))
    spec = {"session_id": session_id, "owner": owner(), "worker": worker,
            "root": remote.remote_path(root, worker), "media_root": remote.remote_path(MEDIA, worker),
            "environment_path": contract["environment_path"], "port": WORKERS[worker]["port"],
            "input_directory": remote.remote_path(MEDIA / "runtime/inputs" / worker, worker),
            "contract": remote.remote_path(contract_path, worker), "allocator": session_policy.allocator(profile),
            "unload_seconds": session_policy.UNLOAD_SECONDS, "stop_seconds": session_policy.STOP_SECONDS}
    if test_idle_policy:
        assert job is None, "Test clock overrides cannot be applied to application jobs"
        spec.update(unload_seconds=test_idle_policy[0], stop_seconds=test_idle_policy[1], isolated_lifecycle_test=True)
    (root / "spec.json").write_text(json.dumps(spec, indent=2))
    info = {"root": str(root), "session_id": session_id, "contract_sha256": hashlib.sha256(contract_path.read_bytes()).hexdigest(),
            "signature": runtime_signature(worker), "allocator": session_policy.allocator(profile)}
    save_registry(worker, info)
    remote_script = remote.remote_path(script, worker)
    remote_spec = remote.remote_path(root / "spec.json", worker)
    started = json.loads(remote.ssh(worker, f'''from pathlib import Path
import subprocess,json
root=Path({spec['root']!r});log=(root/'controller.log').open('a')
p=subprocess.Popen(['python3',{remote_script!r},{remote_spec!r}],stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
print(json.dumps({{'controller_pid':p.pid}}))'''))
    (root / "launch.json").write_text(json.dumps(started, indent=2))
    deadline = time.monotonic() + 180
    cancelling = False
    while time.monotonic() < deadline:
        if job and store.job_get(job["id"])["data"].get("cancel_requested") and not cancelling:
            remote_root = spec["root"]
            remote.ssh(worker, f"from pathlib import Path\nPath({remote_root!r},'stop.json').write_text('{{}}')\nprint('stop requested')")
            cancelling = True
        value = inspect(worker, info)
        if cancelling and value["release"]:
            if value["release"].get("stopped") and value["release"].get("gpu_released"):
                raise remote.Cancelled("Owned startup stopped and GPU released")
            raise RuntimeError("Startup cancellation requires release inspection")
        if value["failure"]:
            raise RuntimeError(value["failure"]["error"])
        if not cancelling and value["state"] and value["state"]["state"] == "ready" and value["alive"]:
            return info
        time.sleep(.5)
    raise RuntimeError("Unified worker launch timed out; inspect existing session before retry")


def enqueue(worker, info, packet):
    root = remote.remote_path(info["root"], worker)
    code = f'''from pathlib import Path
import json,fcntl
root=Path({root!r});packet=json.loads({json.dumps(json.dumps(packet))})
with (root/'inbox.lock').open('a') as lock:
 fcntl.flock(lock,fcntl.LOCK_EX)
 state=json.loads((root/'state.json').read_text())
 assert state['session_id']=={info['session_id']!r} and state['owner']=={owner()!r}, 'Session ownership mismatch'
 accepting=state['accepting'] and not (root/'stop.json').exists() and not state.get('active')
 if accepting:
  target=root/'inbox'/(packet['attempt']+'.json')
  previous=target if target.exists() else target.with_suffix('.accepted')
  if previous.exists():assert json.loads(previous.read_text())==packet,'Conflicting duplicate stage'
  else:
   p=target.with_suffix('.pending');p.write_text(json.dumps(packet));p.replace(target)
 print(json.dumps({{'accepted':bool(accepting)}}))
'''
    return json.loads(remote.ssh(worker, code))["accepted"]


def execute(job, graph, files, directory, profile="full"):
    worker = job["data"]["worker"]
    lockroot = DATA / "worker-sessions"
    lockroot.mkdir(parents=True, exist_ok=True)
    store.job_update(job["id"], execution_resource=worker, gpu_waiting=True,
                     phase="Waiting for " + ("Arch" if worker == "arch" else "Fey"))
    with (lockroot / (worker + ".lock")).open("a") as lock:
        try:
            while True:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    remote.check_cancel(job)
                    time.sleep(.1)
            store.job_update(job["id"], gpu_waiting=False)
            return _execute(job, graph, files, Path(directory), profile)
        finally:
            store.job_update(job["id"], execution_resource="cpu", gpu_waiting=False)


def existing_stage(job, out, info):
    """Prove an accepted stage identity; an arbitrary live endpoint is insufficient."""
    worker = job["data"]["worker"]
    root = remote.remote_path(info["root"], worker)
    expected = session_policy.attempt_id(job["id"], out)
    # Multi-stage IDs were derived from the hub directory, not its Fey alias.
    code = f'''from pathlib import Path
import json
root=Path({root!r})
p=root/'inbox'/({expected!r}+'.accepted')
if not p.exists():p=p.with_suffix('.json')
packet=json.loads(p.read_text()) if p.exists() else None
print(json.dumps(packet))'''
    packet = json.loads(remote.ssh(worker, code))
    if not packet:
        return None
    canonical = out.parent / "api.json"
    if (packet["job"] != job["id"] or packet["directory"] != remote.remote_path(out, worker)
            or not canonical.exists() or packet["graph_sha256"] != hashlib.sha256(canonical.read_bytes()).hexdigest()):
        raise RuntimeError("Existing accepted stage identity changed; inspect before retry")
    return packet


def recoverable(job):
    if job["kind"] not in {"mask", "render", "mask_edit", "refine_extract", "refine_mask", "refine_face", "backplate"}:
        return False
    worker = job["data"].get("worker")
    out = Path(job["data"].get("runtime") or "")
    if worker not in WORKERS or not out.is_absolute() or not out.is_relative_to(MEDIA / "jobs" / job["id"]):
        return False
    info = registry(worker)
    if not info:
        return False
    try:
        if not existing_stage(job, out, info):
            return False
        value = inspect(worker, info, out)
        stage = value["stage"] or {}
        if stage.get("failure") or stage.get("cancelled"):
            return False
        if stage.get("complete"):
            return bool(stage["complete"].get("queue_empty"))
        return value["alive"] and value["runtime_alive"] and value["state"]["state"] in {"ready", "running", "warm_idle"}
    except Exception:
        return False


def _execute(job, graph, files, directory, profile):
    worker = job["data"]["worker"]
    out = directory / "runtime"
    out.mkdir(exist_ok=True)
    info = registry(worker)
    canonical = directory / "api.json"
    if info and canonical.exists() and json.loads(canonical.read_text()) == graph:
        packet = existing_stage(job, out, info)
        if packet:
            value = inspect(worker, info, out)
            if (value["stage"] or {}).get("complete") or (value["alive"] and value["runtime_alive"] and not (value["stage"] or {}).get("failure")):
                return monitor(job, info, out)
    if (out / "attempt.json").exists():
        if (out / "complete.json").exists() and json.loads((directory / "api.json").read_text()) == graph:
            manifest = json.loads((out / "output-receipt.json").read_text())
            if all(hashlib.sha256((out / "output" / f["path"]).read_bytes()).hexdigest() == f["sha256"] for f in manifest["files"]):
                return out
        raise RuntimeError("Existing session attempt needs inspection; no automatic replay")
    remote.check_cancel(job)
    if info:
        value = inspect(worker, info)
        released = value["release"] or {}
        if released.get("stopped") and released.get("gpu_released"):
            info = None
        elif not value["alive"]:
            raise RuntimeError("Previous worker controller ended without release proof; inspect session")
        elif value["state"]["state"] == "needs_inspection":
            raise RuntimeError("Previous worker session requires inspection")
        elif info.get("signature") != runtime_signature(worker) or info.get("allocator", "default") != session_policy.allocator(profile):
            (directory / "session-restart.json").write_text(json.dumps({
                "previous": info["session_id"], "reason": "Runtime source or explicit allocator policy changed",
                "previous_allocator": info.get("allocator", "default"), "allocator": session_policy.allocator(profile)}, indent=2))
            close(worker)
            info = None
    if job["kind"] == "render":
        from . import render_models
        model = render_models.identify(graph)
        if model is None:
            raise ValueError("Unknown generation checkpoint")
        verified = render_models.verify_worker(worker, model)
        (directory / "generation-model.json").write_text(json.dumps({"lora": model, "verified": verified}, indent=2))
    mapping = {name: remote.remote_path(path, worker) for name, path in files.items()}
    if any("/" in name or name in {".", ".."} for name in mapping):
        raise ValueError("Invalid staged input name")
    contract = json.loads((ROOT / "templates" / f"unified-runtime-{worker}.json").read_text())
    stage_root = Path(remote.remote_path(MEDIA / "runtime/inputs" / worker, worker))
    staging = json.loads(remote.ssh(worker, f'''from pathlib import Path
import json,shutil,hashlib
root=Path({str(stage_root)!r});root.mkdir(parents=True,exist_ok=True)
receipt=[]
for name,source in json.loads({json.dumps(json.dumps(mapping))}).items():
 source=Path(source);target=root/name
 created=not target.exists()
 if created:shutil.copy2(source,target)
 digest=hashlib.sha256(source.read_bytes()).hexdigest()
 assert hashlib.sha256(target.read_bytes()).hexdigest()==digest,'Input collision'
 receipt.append({{'name':name,'source':str(source),'target':str(target),'sha256':digest,'created':created}})
print(json.dumps(receipt))'''))
    graphfile = directory / "api.json"
    graphfile.write_text(json.dumps(graph, indent=2))
    if info is None:
        store.job_update(job["id"], phase="Starting unified ComfyUI")
        info = launch(worker, directory, job, profile)
    packet = {"job": job["id"], "stage": job["kind"],
              "attempt": session_policy.attempt_id(job["id"], out),
              "directory": remote.remote_path(out, worker), "graph": remote.remote_path(graphfile, worker),
              "graph_sha256": hashlib.sha256(graphfile.read_bytes()).hexdigest(),
              "profile": "full" if profile == "legacy" else profile,
              "placement": session_policy.placement("full" if profile == "legacy" else profile),
              "family": session_policy.family(graph), "staged_inputs": staging}
    if not enqueue(worker, info, packet):
        raise remote.Busy(f"{worker}: owned session is completing an idle transition; waiting")
    roots = list(store.job_get(job["id"])["data"].get("session_roots") or [])
    if info["root"] not in roots:
        roots.append(info["root"])
    store.job_update(job["id"], phase="Running on unified worker", runtime=str(out),
                     warm_session=info["root"], session_roots=roots, api=store.asset_add(graphfile))
    return monitor(job, info, out)


def finish_job(job):
    """Notify live sessions that verified input copies may be reclaimed."""
    worker = job["data"].get("worker")
    if worker not in WORKERS or job["state"] not in {"success", "failed", "cancelled"}:
        return
    for root in job["data"].get("session_roots") or []:
        target = remote.remote_path(Path(root) / "finished_jobs" / (job["id"] + ".json"), worker)
        remote.ssh(worker, f'''from pathlib import Path
import json,fcntl,importlib.util,os
p=Path({target!r});p.parent.mkdir(exist_ok=True)
root=p.parent.parent
with (root/'inbox.lock').open('a') as lock:
 fcntl.flock(lock,fcntl.LOCK_EX)
 t=p.with_suffix('.pending');t.write_text(json.dumps({{'job':{job['id']!r},'state':{job['state']!r},'owner':{owner()!r}}}));t.replace(p)
 spec=json.loads((root/'spec.json').read_text())
 assert spec['owner']=={owner()!r},'Cleanup ownership mismatch'
 pending=Path.home()/'.local/state/h3-workbench/pending-input-cleanup'
 pending.mkdir(exist_ok=True)
 entry=pending/(spec['session_id']+'.json');tmp=entry.with_suffix('.pending.'+str(os.getpid()))
 tmp.write_text(json.dumps({{'root':str(root),'owner':spec['owner'],'environment_path':spec['environment_path']}}));tmp.replace(entry)
 release=root/'release.json'
 if release.exists() and json.loads(release.read_text()).get('stopped'):
  module_spec=importlib.util.spec_from_file_location('completed_worker_session',root/'worker_session.py')
  module=importlib.util.module_from_spec(module_spec);module_spec.loader.exec_module(module)
  if not module.runtimes() and hasattr(module,'collect_finished_inputs'):
   module.collect_finished_inputs(root,Path(spec['environment_path']),spec['owner'])
print('recorded')''')


def monitor(job, info, out):
    """Observe an already accepted stage; never resubmit its graph."""
    worker = job["data"]["worker"]
    sent = False
    while True:
        cancelling = store.job_get(job["id"])["data"].get("cancel_requested")
        if cancelling and not sent:
            marker = remote.remote_path(out / "cancel.json", worker)
            remote.ssh(worker, f"from pathlib import Path\np=Path({marker!r});p.write_text('{{}}')\nprint('requested')")
            sent = True
        value = inspect(worker, info, out)
        stage = value["stage"] or {}
        if stage.get("complete") and not cancelling:
            assert stage["complete"].get("queue_empty"), "No stage queue ownership proof"
            manifest = json.loads((out / "output-receipt.json").read_text())
            for entry in manifest["files"]:
                path = (out / "output" / entry["path"]).resolve()
                assert path.is_relative_to((out / "output").resolve()) and not path.is_symlink(), "Invalid completed output path"
                assert hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"], "Completed output changed; inspect the artifact"
            return out
        if stage.get("cancelled") or (cancelling and stage.get("complete")):
            if stage.get("complete"):
                # Completion raced cancellation. Release the verified idle
                # session to preserve the current stop-running-job promise.
                close(worker)
                raise remote.Cancelled("Owned completed session released after stop request")
            if value["release"] and value["release"].get("gpu_released") and value["release"].get("stopped"):
                (out / "release.json").write_text(json.dumps(value["release"], indent=2))
                raise remote.Cancelled("Owned session stopped and GPU released")
        if stage.get("failure") or value.get("failure"):
            # Wait for release so a failed job cannot report a free worker while
            # the controller is still cleaning up its owned runtime.
            if value.get("release"):
                (out / "release.json").write_text(json.dumps(value["release"], indent=2))
                raise RuntimeError((stage.get("failure") or value["failure"])["error"])
        if not value["alive"] and not stage.get("complete"):
            raise RuntimeError("Worker controller disappeared; inspect attempt before retry")
        if stage.get("status"):
            store.job_update(job["id"], phase="Stopping owned runtime" if cancelling else
                "Rendering" if job["kind"] == "render" else "Processing on unified worker",
                runtime_status=stage["status"])
        time.sleep(.5)
