"""Owned SAM sessions. Immutable prompts finish independently; runtime releases after idle."""

import json
import shlex
import shutil
import subprocess
import threading
import time

from . import remote, store
from .config import ROOT, WORKERS

SESSIONS = {}
LOCK = threading.RLock()
IDLE_SECONDS = 15


def compatible(job):
    return (
        job["kind"] in {"refine_mask", "mask_edit"}
        and job["data"].get("options", {}).get("mode") == "frame"
    )


def released(session):
    receipt = remote.remote_path(session["out"] / "release.json", session["worker"])
    info = json.loads(
        remote.ssh(
            session["worker"],
            f"from pathlib import Path\np=Path({receipt!r})\nassert p.exists(), 'Missing release receipt; inspect runtime'\nprint(p.read_text())",
        )
    )
    if not info.get("stopped") or not info.get("gpu_released"):
        raise RuntimeError("Warm SAM release needs inspection: " + str(info))


def close(worker):
    with LOCK:
        session = SESSIONS.get(worker)
    if not session:
        return
    if session["process"].poll() is None:
        remote.ssh(
            worker,
            f"from pathlib import Path\nPath({remote.remote_path(session['out'] / 'stop.json', worker)!r}).write_text('{{}}')\nprint('closing')",
        )
        session["process"].wait(timeout=90)
    released(session)
    with LOCK:
        SESSIONS.pop(worker, None)


def enqueue(worker, session, packet):
    out = remote.remote_path(session["out"], worker)
    # This shares the controller's deadline lock. False means it is already closing.
    code = f"""import json,fcntl
from pathlib import Path
root=Path({out!r})
with (root/'inbox.lock').open('a') as lock:
 fcntl.flock(lock,fcntl.LOCK_EX)
 state=root/'warm-state.json'
 accepting=state.exists() and json.loads(state.read_text()).get('accepting') and not (root/'stop.json').exists()
 if accepting:
  packet=json.loads({json.dumps(json.dumps(packet))})
  target=root/'inbox'/(packet['job']+'.json')
  assert not target.exists() and not target.with_suffix('.accepted').exists(), 'Duplicate warm submission'
  temp=target.with_suffix('.pending');temp.write_text(json.dumps(packet));temp.replace(target)
 print(json.dumps(bool(accepting)))
"""
    return json.loads(remote.ssh(worker, code))


def execute(job, graph, files, d):
    worker = job["data"]["worker"]
    w = WORKERS[worker]
    out = d / "runtime"
    out.mkdir(exist_ok=True)
    if (out / "attempt.json").exists():
        raise RuntimeError(
            "Existing warm attempt needs inspection; no automatic replay"
        )
    remote.check_cancel(job)
    with LOCK:
        session = SESSIONS.get(worker)
    if session and session["process"].poll() is not None:
        released(session)
        with LOCK:
            SESSIONS.pop(worker, None)
        session = None
    if session is None:
        live = remote.status(worker)
        if live["state"] != "available":
            raise remote.Busy(f"{worker}: {live['state']}. Waiting for available GPU.")
    for name in files:
        if "/" in name:
            raise ValueError("Invalid staged input")
    mapping = {name: remote.remote_path(path, worker) for name, path in files.items()}
    code = f"""import json,shutil,hashlib
from pathlib import Path
root=Path({w["home"]!r})/'comfygit/environments/h3-character-swap/ComfyUI/input'
for name,src in json.loads({json.dumps(json.dumps(mapping))}).items():
 p=root/name;src=Path(src)
 if not p.exists():shutil.copy2(src,p)
 assert hashlib.sha256(p.read_bytes()).digest()==hashlib.sha256(src.read_bytes()).digest(), 'Input collision'
print('staged')
"""
    remote.ssh(worker, code)
    graphfile = d / "api.json"
    graphfile.write_text(json.dumps(graph, indent=2))
    packet = {
        "job": job["id"],
        "stage": job["kind"],
        "graph": remote.remote_path(graphfile, worker),
        "directory": remote.remote_path(out, worker),
    }
    if session and not enqueue(worker, session, packet):
        close(worker)
        session = None
    if session is None:
        # Recheck after staging / a possible idle shutdown. Never adopt an unrelated runtime.
        if remote.status(worker)["state"] != "available":
            raise remote.Busy(f"{worker}: waiting for GPU ownership")
        node_sha = remote.ensure_workbench_nodes(worker, d)
        script = d / "remote_worker.py"
        shutil.copy2(ROOT / "scripts/remote_worker.py", script)
        script.write_text(script.read_text().replace(str(remote.SHARED_ROOT) + "/", remote.WORKERS[worker]["shared"].rstrip("/") + "/"))
        args = [
            "python3",
            remote.remote_path(script, worker),
            worker,
            job["id"],
            job["kind"],
            packet["graph"],
            packet["directory"],
            str(w["port"]),
            "--profile",
            "full",
            "--warm-idle",
            str(IDLE_SECONDS),
            "--mask-node-sha256",
            node_sha,
        ]
        log = (d / "dispatch.log").open("w")
        process = subprocess.Popen(
            ["ssh", "-o", "BatchMode=yes", w["ssh"], shlex.join(args)],
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        session = {"out": out, "process": process, "worker": worker}
        with LOCK:
            SESSIONS[worker] = session

        def reap():
            process.wait()
            log.close()

        threading.Thread(target=reap, daemon=True).start()
    store.job_update(
        job["id"],
        phase="Preparing warm SAM worker",
        runtime=str(out),
        warm_session=str(session["out"]),
        api=store.asset_add(graphfile),
    )
    sent = False
    while True:
        cancelling = store.job_get(job["id"])["data"].get("cancel_requested")
        if cancelling and not sent:
            marker = remote.remote_path(out / "cancel.json", worker)
            remote.ssh(
                worker,
                f"from pathlib import Path\np=Path({marker!r});t=p.with_suffix('.pending');t.write_text({json.dumps({'job': job['id']})!r});t.replace(p)\nprint('requested')",
            )
            sent = True
        # SSH stdout is written on the hub. NFS negative lookup caches can hide a
        # freshly completed receipt for longer than the entire warm idle window.
        dispatch = session["out"].parent / "dispatch.log"
        done = f"COMPLETE {job['id']} {job['kind']}" in dispatch.read_text()
        if done and not cancelling:
            info = json.loads(
                remote.ssh(
                    worker,
                    f"import json\nfrom pathlib import Path\np=Path({remote.remote_path(out / 'complete.json', worker)!r})\nprint(p.read_text())",
                )
            )
            if not info.get("queue_empty"):
                raise RuntimeError(
                    "Warm correction completion has no queue ownership proof"
                )
            if session["process"].poll() is not None:
                released(session)
            store.job_update(
                job["id"], phase="Merging corrected frame", runtime_status=info
            )
            return out
        if session["process"].poll() is not None:
            released(session)
            if cancelling:
                raise remote.Cancelled(
                    "Owned warm SAM runtime stopped and GPU released"
                )
            failure = out / "failure.json"
            raise RuntimeError(
                json.loads(failure.read_text())["error"]
                if failure.exists()
                else "Warm SAM runtime exited before frame completion"
            )
        status = out / "status.json"
        if status.exists() and not cancelling:
            store.job_update(
                job["id"],
                phase="Updating SAM frame",
                runtime_status=json.loads(status.read_text()),
            )
        time.sleep(0.3)
