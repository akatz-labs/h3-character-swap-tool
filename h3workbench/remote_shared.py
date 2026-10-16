"""H3 domain intents use the existing shared Node client and worker FIFO.

This module has no remote prompt submission or runtime ownership path. A saved
binding/admission is reconciled unchanged across observation and collection retries.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

from . import remote, session_policy, store
from .config import DATA, MEDIA, ROOT, WORKERS

GPU_KINDS = {"mask", "render", "mask_edit", "refine_extract", "refine_mask", "refine_face", "backplate"}
STAGE_PREPARATION = threading.BoundedSemaphore(1)


class Uncertain(RuntimeError):
    pass


def enabled():
    return store.setting("shared_executor", False)


def target(worker):
    configured = store.setting("shared_executor_targets", {})
    return configured.get(worker) or WORKERS[worker].get("target") or {"kind": "ssh", "host": WORKERS[worker]["ssh"], "port": 8841}


def instance():
    with store.LOCK:
        value = store.setting("app_instance_id")
        if not value:
            value = uuid.uuid4().hex
            store.set_setting("app_instance_id", value)
        return value


def status(worker):
    try:
        result = call({"action": "status", "target": target(worker)})
        observed = result["status"]
        jobs = observed["jobs"]
        sessions = [s["status"] for s in observed["sessions"] if s.get("status")]
        live = next((s for s in reversed(sessions) if s["phase"] != "stopped"), None)
        selected = store.setting("shared_executor_installations", {}).get(worker)
        ready = next((r for r in result["installations"] if r["features"] and
                      (not selected or r["installation"]["id"] == selected)), None)
        if any(j["phase"] == "uncertain" for j in jobs) or (live and live["phase"] == "needs_inspection"):
            state = "needs_inspection"
        elif jobs:
            state = "busy"
        elif observed.get("externalGpuCompute"):
            state = "external_busy"
        elif observed.get("gpuError") or not observed.get("gpus"):
            state = "needs_inspection"
        elif live:
            state = {"active": "running", "starting": "starting", "stopping": "stopping"}.get(live["phase"], live["phase"])
        elif not ready:
            state = "needs_setup"
        elif "gpuCompute" not in observed:
            state = "needs_inspection"
        else:
            state = "available"
        gpu = next(iter(observed.get("gpus", [])), {})
        return {"worker": worker, "state": state, "gpu": gpu.get("name"), "features": ready["features"] if ready else [],
                "owned": bool(live and live.get("processIdentity")),
                "executor_id": result["health"]["executorId"], "shared_jobs": jobs,
                "auto_available": "auto-placement-v1" in result["health"].get("capabilities", []),
                "session_id": live["id"] if live else None,
                "model_entries": live["modelEntries"] if live else 0,
                "cache_hits": live["cacheHits"] if live else 0,
                "idle_seconds": live["idleSeconds"] if live else 0,
                "unload_seconds": live["unloadSeconds"] if live else 300,
                "stop_seconds": live["stopSeconds"] if live else 900,
                "placement": (live.get("compatibility") or {}).get("residency") if live else None,
                "compatibility": live.get("compatibility") if live else None,
                "error": "Worker needs the current compute-ownership observation" if ready and not live and "gpuCompute" not in observed else None}
    except Exception as error:
        return {"worker": worker, "state": "offline", "error": str(error)[-300:]}


def all_status():
    with ThreadPoolExecutor(max_workers=max(1, len(WORKERS))) as pool:
        return dict(zip(WORKERS, pool.map(status, WORKERS)))


def release_idle(worker):
    return call({"action": "release-idle", "target": target(worker)})


def call(value, job=None, staging_done=None):
    script = ROOT / "dist/runtime/bridge.js"
    if not script.is_file():
        raise RuntimeError("Build the pinned shared execution bridge before using this backend")
    directory = Path(value.get("directory") or DATA / "shared-bridge")
    directory.mkdir(parents=True, exist_ok=True)
    done = threading.Event()
    cancel_marker = directory / "bridge-cancel"

    def watch():
        while not done.wait(.2):
            if store.job_get(job["id"])["data"].get("cancel_requested"):
                cancel_marker.write_text("requested\n")
                return

    watcher = threading.Thread(target=watch, daemon=True) if job else None
    if watcher:
        watcher.start()
    with (directory / "bridge.log").open("a") as log:
        process = subprocess.Popen([os.environ.get("H3_WORKBENCH_NODE", "node"), str(script)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, text=True)
        process.stdin.write(json.dumps(value)); process.stdin.close()
        result, error = None, None
        try:
            for line in process.stdout:
                if len(line) > 8 * 1024 ** 2:
                    raise Uncertain("Shared bridge response exceeded its bound; inspect its saved admission")
                message = json.loads(line)
                if message["type"] == "result":
                    result = message["value"]
                elif message["type"] == "error":
                    error = message["value"]
                elif job and message["type"] in {"accepted", "phase"}:
                    data = message["value"]
                    phase = data.get("phase")
                    store.job_update(job["id"], shared_job_id=data.get("jobId"), shared_phase=phase,
                        execution_resource="cpu" if phase in {"finalizing", "completed"} else job["data"]["worker"],
                        gpu_waiting=phase in {"queued", "preparing"},
                        phase={"queued": "Queued on shared worker", "preparing": "Preparing shared runtime",
                               "rendering": "Rendering on shared worker", "finalizing": "Collecting shared output"}.get(phase, "Shared worker accepted"))
                    if staging_done:
                        staging_done()
            process.wait(timeout=10)
        finally:
            done.set()
            if watcher:
                watcher.join(timeout=1)
        if error:
            if error.get("cancelled") and error.get("terminal"):
                raise remote.Cancelled(error["message"])
            if error.get("status") == 409 and not (directory / "shared-request.json").exists():
                raise remote.Busy(error["message"])
            raise Uncertain(error["message"]) if (directory / "shared-request.json").exists() else RuntimeError(error["message"])
        if process.returncode or result is None:
            raise Uncertain("Shared observation ended without a receipt; preserve and reconcile the saved binding")
        return result


def operation(job, directory):
    if job["kind"] == "render":
        return "h3-render-segments-v1" if job["data"].get("segment_plan") else "h3-render"
    if job["kind"] == "mask":
        return "sam-recover" if directory.name == "recovery" else "sam-track"
    if job["kind"] in {"mask_edit", "refine_mask"}:
        return "sam-frame" if job["data"]["options"]["mode"] == "frame" else "sam-track"
    if job["kind"] == "refine_extract":
        return {"sam": "sam-track", "sam-recovery": "sam-recover", "alpha": "alpha-extract", "face": "face-track"}[directory.name]
    return {"refine_extract": "alpha-extract", "refine_face": "face-track", "backplate": "person-remove"}[job["kind"]]


def execute(job, graph, files, directory, profile="full"):
    while not STAGE_PREPARATION.acquire(timeout=.2):
        remote.check_cancel(job)
    released = False
    def staging_done():
        nonlocal released
        if not released:
            released = True
            STAGE_PREPARATION.release()
    try:
        return _execute(job, graph, files, directory, profile, staging_done)
    finally:
        staging_done()


def _execute(job, graph, files, directory, profile, staging_done):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    worker = job["data"]["worker"]
    feature = operation(job, directory)
    family = session_policy.family(graph)
    profile = "full" if profile == "legacy" else profile
    binding_file = directory / "shared-binding.json"
    if binding_file.exists():
        binding = json.loads(binding_file.read_text())
        worker = binding.get("worker", worker)
    else:
        remote.check_cancel(job)
        selection = store.setting("shared_executor_installations", {}).get(worker)
        automatic = job["data"].get("requested_worker") == "auto" and not job["data"].get("shared_stages")
        if automatic:
            waiting = {j["data"].get("worker") for j in store.pending_jobs(("running", "cancelling"))
                       if j["id"] != job["id"] and j["data"].get("gpu_waiting")}
            installations = store.setting("shared_executor_installations", {})
            candidates = [{"worker": w, "target": target(w), **({"installationId": installations[w]} if w in installations else {})}
                          for w in WORKERS if w not in waiting]
            if not candidates:
                raise remote.Busy("Prepared stages already wait on all allowed workers")
            selected = call({"action": "bind-auto", "candidates": candidates, "feature": feature,
                "compatibility": {"modelConfigurationDigest": family["key"], "precision": "pinned-operation-model-dtypes",
                                  "residency": session_policy.placement(profile), "allocator": session_policy.allocator(profile)}})
            worker = selected["worker"]
            binding = {**selected["binding"], "worker": worker, "placement": selected["placement"]}
            store.job_update(job["id"], worker=worker, auto_placement=selected["placement"])
        else:
            binding = {**call({"action": "bind", "target": target(worker), "feature": feature,
                             **({"installationId": selection} if selection else {})}), "worker": worker}
        temporary = binding_file.with_suffix(".pending")
        temporary.write_text(json.dumps(binding, indent=2)); temporary.replace(binding_file)
    if any(j["id"] != job["id"] and j["data"].get("gpu_waiting") and j["data"].get("worker") == worker
           for j in store.pending_jobs(("running", "cancelling"))):
        raise remote.Busy("A prepared stage already waits on this worker")
    graph_file = directory / "api.json"
    if graph_file.exists() and json.loads(graph_file.read_text()) != graph:
        raise Uncertain("Captured graph changed under a shared admission identity")
    if not graph_file.exists():
        graph_file.write_text(json.dumps(graph, indent=2))
    # Recovery stages explicitly transfer any earlier named input rather than
    # relying on a previous worker's scratch directory or a shared mount.
    if not files and feature == "sam-recover":
        previous = json.loads((directory.parent / "shared-files.json").read_text())
        files = {name: Path(path) for name, path in previous.items()}
    mapping = {name: str(Path(path).resolve()) for name, path in files.items()}
    (directory / "shared-files.json").write_text(json.dumps(mapping, indent=2))
    graph_digest = hashlib.sha256(graph_file.read_bytes()).hexdigest()
    job["data"]["worker"] = worker
    c, shot = job["data"]["snapshot"], job["data"]["shot"]
    spec = job["data"].get("render_spec") or {"dimensions": c["dimensions"]}
    chain = {"recipeRevision": binding["recipeRevision"], "adapterRevision": binding["revision"],
             "modelDigest": binding["recipeRevision"], "modelConfigurationDigest": family["key"],
             "precision": "pinned-operation-model-dtypes", "residency": session_policy.placement(profile),
             "allocator": session_policy.allocator(profile)}
    inputs = {"version": 1, "operation": feature, "operationRevision": binding["revision"],
              "graphFile": "captured-api.json", "graphDigest": graph_digest, "runtimeCompatibility": chain,
              "family": family, "profile": profile, "frames": shot["end"] - shot["start"], "dimensions": spec["dimensions"]}
    stages = list(store.job_get(job["id"])["data"].get("shared_stages") or [])
    if str(directory) not in stages:
        stages.append(str(directory))
    store.job_update(job["id"], worker=worker, shared_stages=stages, runtime=str(directory / "runtime"),
                     execution_resource=worker, gpu_waiting=True, phase="Staging shared inputs")
    request = {"action": "execute", "directory": str(directory), "binding": binding, "inputs": inputs,
               "files": {**mapping, "captured-api.json": str(graph_file)}, "instanceId": instance(),
               "sourceJobId": session_policy.attempt_id(job["id"], directory), "targetId": worker}
    result = call(request, job, staging_done)
    store.job_update(job["id"], shared_phase=result["phase"], gpu_waiting=False, execution_resource="cpu",
                     shared_diagnostics=result.get("diagnostics"), runtime=result.get("directory", str(directory / "runtime")))
    if result["phase"] == "cancelled":
        raise remote.Cancelled(result.get("error") or "Shared worker verified cancellation")
    if result["phase"] == "uncertain":
        raise Uncertain(result.get("error") or "Shared worker attempt requires inspection")
    if result["phase"] != "completed":
        raise RuntimeError(result.get("error") or "Shared worker operation failed")
    return Path(result["directory"])


def recoverable(job):
    if job["data"].get("execution_backend") != "shared":
        return False
    stages = job["data"].get("shared_stages") or []
    # A persisted immutable admission can be reconciled even if its response was
    # lost; the shared source identity never creates another worker job.
    return bool(stages) and all((Path(path) / "shared-binding.json").exists() and
        (Path(path) / "shared-request.json").exists() for path in stages)
