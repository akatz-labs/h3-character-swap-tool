"""Durable, exclusively owned ComfyGit session on one GPU worker.

Only the controller admits prompts. Atomic inbox admission and idle decisions
share a lock. Completed stages have independent immutable output directories.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time
import traceback
import sys
from datetime import datetime, timezone
import urllib.error
import urllib.request


def read(path):
    return json.loads(Path(path).read_text())


def save(path, value):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".pending")
    tmp.write_text(json.dumps(value, indent=2))
    tmp.replace(path)


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as file:
        for chunk in iter(lambda: file.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def collect_finished_inputs(root, env, owner):
    """Reclaim only our verified copies after the whole logical job finishes."""
    root, env = Path(root), Path(env)
    receipts = root / "input-release"
    receipts.mkdir(exist_ok=True)
    spec = read(root / "spec.json") if (root / "spec.json").exists() else {}
    input_root = Path(spec.get("input_directory") or env / "ComfyUI/input").resolve()
    markers = root / "finished_jobs"
    if not markers.exists():
        return
    for marker in markers.glob("*.json"):
        final = read(marker)
        if final.get("owner") != owner or final.get("state") not in {"success", "failed", "cancelled"}:
            continue
        receipt = receipts / marker.name
        if receipt.exists():
            continue
        copies = {}
        for accepted in (root / "inbox").glob("*.accepted"):
            packet = read(accepted)
            if packet["job"] == final["job"]:
                for item in packet.get("staged_inputs", []):
                    if item["created"]:
                        copies[item["target"]] = item
        deleted, retained = [], []
        for item in copies.values():
            target, source = Path(item["target"]), Path(item["source"])
            if not target.exists():
                continue
            if (target.is_symlink() or target.parent.resolve() != input_root or not source.is_file()
                    or sha(source) != item["sha256"] or sha(target) != item["sha256"]):
                retained.append(item)
                continue
            target.unlink()
            deleted.append(item)
        save(receipt, {"job": final["job"], "time": time.time(), "deleted": deleted,
                       "retained": retained, "originals_preserved": True})


def output_references(value, source, target):
    """Rewrite only this stage's output root; preserve all other provenance."""
    if isinstance(value, dict):
        return {k: output_references(v, source, target) for k, v in value.items()}
    if isinstance(value, list):
        return [output_references(v, source, target) for v in value]
    if isinstance(value, str):
        return str(target) if value == str(source) else value.replace(str(source) + "/", str(target) + "/")
    return value


def command(args):
    return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT).strip()


def process_identity(pid):
    root = Path("/proc") / str(pid)
    try:
        text = (root / "stat").read_text()
        fields = text[text.rfind(")") + 2:].split()
        if fields[0] in {"Z", "X"}:
            return None
        start = fields[19]
        return {"pid": pid, "start": start, "boot": Path("/proc/sys/kernel/random/boot_id").read_text().strip()}
    except OSError:
        return None


def runtimes():
    result = []
    for p in Path("/proc").iterdir():
        if p.name.isdigit():
            try:
                args = (p / "cmdline").read_bytes().decode().split("\0")
                if "main.py" in args:
                    result.append((int(p.name), args))
            except (OSError, UnicodeDecodeError):
                pass
    return result


def compute_pids():
    return set(command(["nvidia-smi", "--query-compute-apps=pid", "--format=csv,noheader,nounits"]).splitlines())


def release_idle_orphan(spec):
    """Explicit recovery after a controller crash; never adopt or replay work."""
    root = Path(spec["root"])
    lock = (Path(spec["owner_state_directory"]) / "worker.lock").open("a")
    with lock, (root / "inbox.lock").open("a") as admission:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(admission, fcntl.LOCK_EX)
        state = read(root / "state.json")
        assert state["owner"] == spec["owner"] and state["session_id"] == spec["session_id"]
        assert process_identity(state["controller"]["pid"]) != state["controller"], "Controller is still alive"
        assert not list((root / "inbox").glob("*.json")), "Accepted work remains; inspect the attempt"
        runtime = state["runtime"]
        assert runtime, "No recorded runtime identity"
        if process_identity(runtime["pid"]) != runtime:
            assert str(runtime["pid"]) not in compute_pids(), "GPU process identity requires inspection"
            if state["active"]:
                packet = read(root / "inbox" / (state["active"] + ".accepted"))
                assert (Path(packet["directory"]) / "failure.json").exists() or (Path(packet["directory"]) / "cancelled.json").exists(), "Uncertain active attempt; inspect its receipt"
            receipt = {"stopped": True, "gpu_released": True, "runtime": runtime,
                       "controller": state["controller"], "session_id": spec["session_id"],
                       "time": time.time(), "recovery": "Explicit verification of dead runtime and terminal failed attempt"}
            if (root / "release.json").exists():
                save(root / "release.previous.json", read(root / "release.json"))
            save(root / "release.json", receipt)
            save(root / "state.json", {**state, "state": "stopped", "active": None, "accepting": False, "time": time.time()})
            return receipt
        assert not state["active"], "Active runtime; inspect its attempt"
        url = "http://127.0.0.1:" + str(spec["port"])
        for route in ("/akatz/session", "/queue"):
            request = urllib.request.Request(url + route, headers={"X-Akatz-Session": spec["session_id"]})
            with urllib.request.urlopen(request, timeout=15) as response:
                value = json.load(response)
            if route == "/queue":
                assert not value["queue_running"] and not value["queue_pending"], "Unexpected queue; preserving runtime"
            else:
                assert value["session_id"] == spec["session_id"], "Endpoint ownership changed"
        os.kill(runtime["pid"], signal.SIGTERM)
        for _ in range(120):
            if process_identity(runtime["pid"]) != runtime and str(runtime["pid"]) not in compute_pids():
                break
            time.sleep(.5)
        else:
            raise RuntimeError("Owned orphan has not released")
        receipt = {"stopped": True, "gpu_released": True, "runtime": runtime,
                   "controller": state["controller"], "session_id": spec["session_id"],
                   "time": time.time(), "recovery": "Explicit verified idle orphan release"}
        save(root / "release.json", receipt)
        save(root / "state.json", {**state, "state": "stopped", "accepting": False, "time": time.time()})
        return receipt


def resource_preflight(pid, args):
    root = Path("/proc") / str(pid)
    assert args[args.index("--reserve-vram") + 1] == "0"
    assert "--disable-smart-memory" not in args and "--highvram" in args
    assert "--cache-classic" in args and "--cache-none" not in args
    limits = (root / "limits").read_text()
    for name in ["Max cpu time", "Max data size", "Max resident set", "Max address space"]:
        row = next(x for x in limits.splitlines() if x.startswith(name))
        assert row.split()[-3:-1] == ["unlimited", "unlimited"], row
    groups = {}
    for row in (root / "cgroup").read_text().splitlines():
        _, controllers, relative = row.split(":")
        base = Path("/sys/fs/cgroup") / controllers if controllers else Path("/sys/fs/cgroup")
        q = base / relative.lstrip("/")
        names = ["cpu.max", "memory.max", "memory.high", "memory.swap.max"] if not controllers else ["memory.limit_in_bytes", "memory.memsw.limit_in_bytes", "cpu.cfs_quota_us"]
        while q.is_relative_to(base):
            values = {n: (q / n).read_text().strip() for n in names if (q / n).exists()}
            if values:
                groups[str(q)] = values
            for n, v in values.items():
                assert v in {"max", "-1"} or v.startswith("max ") or (n.endswith("limit_in_bytes") and int(v) > 2**60), (q, n, v)
            if q == base:
                break
            q = q.parent
    return {"pid": pid, "args": args, "process_limits": limits, "parent_cgroups": groups,
            "gpu": command(["nvidia-smi"]), "ram": command(["free", "-b"]),
            "placement": "Unrestricted resources, zero reserved VRAM; per-stage residency recorded separately"}


def wire_status(spec):
    """Translate verified controller/process evidence into the shared v1 wire form."""
    root = Path(spec["root"])
    state = read(root / "state.json") if (root / "state.json").exists() else None
    released = read(root / "release.json") if (root / "release.json").exists() else None
    observed = datetime.now(timezone.utc).isoformat()
    base = {"version": 1, "id": spec["session_id"], "owner": spec["owner"],
            "installationId": spec["installation_id"], "recipeRevision": spec["recipe_revision"],
            "generation": max(1, (state or {}).get("generation", 1)), "phase": "needs_inspection",
            "processIdentity": None, "compatibility": None, "activeAttempt": None,
            "idleSeconds": 0, "unloadSeconds": spec["unload_seconds"], "stopSeconds": spec["stop_seconds"],
            "modelEntries": 0, "cacheHits": 0, "observedAt": observed}
    if not state:
        return base
    assert state["session_id"] == spec["session_id"] and state["owner"] == spec["owner"], "Session ownership mismatch"
    base.update(compatibility=state.get("compatibility"), activeAttempt=state.get("active"),
                idleSeconds=max(0, state.get("idle_seconds", 0)),
                modelEntries=state.get("cache", {}).get("model_entries", 0),
                cacheHits=state.get("cache", {}).get("hits", 0))
    if released and released.get("stopped") and released.get("gpu_released"):
        assert released["session_id"] == spec["session_id"], "Release identity mismatch"
        runtime = released.get("runtime")
        assert not runtime or process_identity(runtime["pid"]) != runtime, "Released runtime remains alive"
        base.update(phase="stopped", activeAttempt=None, modelEntries=0, compatibility=None,
                    releaseProof={"stopped": True, "gpuReleased": True, "observedAt": observed})
        return base
    controller = state.get("controller")
    if not controller or process_identity(controller["pid"]) != controller:
        return base
    runtime = state.get("runtime")
    if runtime and process_identity(runtime["pid"]) == runtime:
        base["processIdentity"] = {"platform": "linux", **runtime}
    elif state["state"] not in {"starting", "stopping"}:
        return base
    base["phase"] = {"running": "active", "unloading": "starting"}.get(state["state"], state["state"])
    return base


def control(action, spec, body):
    """Fixed local operations for the declared trusted driver; no shell passthrough."""
    root = Path(spec["root"])
    root.mkdir(parents=True, exist_ok=True)
    (root / "inbox").mkdir(exist_ok=True)
    if action == "launch":
        assert not (root / "launch.json").exists(), "Launch already attempted; inspect the same identity"
        save(root / "spec.json", spec)
        save(root / "launch.json", {"state": "attempting", "owner": spec["owner"], "session_id": spec["session_id"]})
        with (root / "controller.log").open("a") as log:
            child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), str(root / "spec.json")],
                       stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        save(root / "launch.json", {"state": "started", "pid": child.pid, "owner": spec["owner"], "session_id": spec["session_id"]})
        return {"launched": True, "pid": child.pid}
    if action == "inspect":
        return wire_status(spec)
    if action == "observe":
        directory = Path(body["directory"]).resolve()
        assert directory.is_relative_to(Path(spec["media_root"]).resolve()) and directory != Path(spec["media_root"]).resolve()
        attempt = body.get("attempt")
        accepted = None
        if attempt is not None:
            path = root / "inbox" / (attempt + ".accepted")
            assert path.parent == root / "inbox", "Invalid attempt identity"
            if not path.exists():
                path = path.with_suffix(".json")
            accepted = read(path) if path.exists() else None
        return {"session": wire_status(spec), "accepted": accepted, "stage": {name: read(directory / (name + ".json"))
                if (directory / (name + ".json")).exists() else None for name in ["status", "submission", "complete", "failure", "cancelled"]}}
    with (root / "inbox.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if action == "pending":
            jobs = body["jobs"]
            assert isinstance(jobs, list) and all(isinstance(j, str) and j and len(j) <= 160 and "/" not in j and "\\" not in j for j in jobs)
            save(root / "pending-admissions.json", {"owner": spec["owner"], "session_id": spec["session_id"], "jobs": jobs})
            return {"recorded": True}
        state = read(root / "state.json")
        assert state["session_id"] == spec["session_id"] and state["owner"] == spec["owner"], "Session ownership mismatch"
        if action == "release":
            assert not state.get("active") and state["state"] in {"ready", "warm_idle", "unloaded_idle", "stopped", "stopping"}, "Active or uncertain session cannot be released"
            assert not list((root / "inbox").glob("*.json")), "Accepted work remains"
            if state["state"] not in {"stopped", "stopping"}:
                save(root / "stop.json", {"owner": spec["owner"]})
            return {"requested": True}
        if action == "enqueue":
            packet = body["packet"]
            directory = Path(packet["directory"]).resolve()
            assert directory.is_relative_to(Path(spec["media_root"]).resolve()) and directory != Path(spec["media_root"]).resolve()
            assert sha(packet["graph"]) == packet["graph_sha256"], "Stage graph changed"
            packet_path = root / "inbox" / (packet["attempt"] + ".json")
            assert packet_path.parent == root / "inbox", "Invalid attempt name"
            previous = packet_path if packet_path.exists() else packet_path.with_suffix(".accepted")
            if previous.exists():
                assert read(previous) == packet, "Conflicting duplicate stage"
                return {"accepted": True, "reconciled": True}
            alive = process_identity(state["controller"]["pid"]) == state["controller"]
            if not alive or not state["accepting"] or (root / "stop.json").exists():
                return {"accepted": False}
            save(packet_path, packet)
            return {"accepted": True}
        if action in {"cancel", "finish"}:
            job = body["job"]
            assert isinstance(job, str) and job and "/" not in job and "\\" not in job and job not in {".", ".."}
            for path in (root / "inbox").glob("*"):
                if path.suffix not in {".json", ".accepted"}:
                    continue
                packet = read(path)
                if packet["job"] == job:
                    if action == "cancel":
                        save(Path(packet["directory"]) / "cancel.json", {"job": job})
                    else:
                        marker = root / "finished_jobs" / (job + ".json")
                        marker.parent.mkdir(exist_ok=True)
                        assert body["state"] in {"success", "failed", "cancelled"}
                        save(marker, {"job": job, "state": body["state"], "owner": spec["owner"]})
            return {"recorded": True}
    raise ValueError("Unknown controller action")


class Session:
    def __init__(self, spec):
        self.spec = spec
        self.root = Path(spec["root"]).resolve()
        self.env = Path(spec["environment_path"]).resolve()
        self.media = Path(spec["media_root"]).resolve()
        self.url = "http://127.0.0.1:" + str(spec["port"])
        self.contract = read(spec["contract"])
        self.process = None
        self.runtime_pid = None
        self.runtime_identity = None
        self.ready = False
        self.active = None
        self.state = "starting"
        self.last_activity = time.monotonic()
        self.last_completed = None
        self.unloaded = False
        self.family = None
        self.last_family = None
        self.placement = None
        self.release = None
        self.generation = 0
        self.cache = {}
        self.compatibility = None
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "inbox").mkdir(exist_ok=True)
        self.local = Path(spec["owner_state_directory"])
        self.local.mkdir(parents=True, exist_ok=True)
        self.owner_lock = (self.local / "worker.lock").open("a")
        fcntl.flock(self.owner_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.boot_identity = process_identity(os.getpid())
        self.log = (self.root / "runtime.log").open("a")

    def api(self, path, body=None):
        request = urllib.request.Request(self.url + path,
                    data=None if body is None else json.dumps(body).encode(),
                    headers={"Content-Type": "application/json", "X-Akatz-Session": self.spec["session_id"]})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                data = response.read()
                return json.loads(data) if data else {}
        except urllib.error.HTTPError as error:
            raise RuntimeError(error.read().decode()) from error

    def publish(self, accepting=True):
        idle = max(0, time.monotonic() - self.last_activity)
        state = {
            "session_id": self.spec["session_id"], "owner": self.spec["owner"],
            "controller": self.boot_identity, "runtime": self.runtime_identity,
            "root": str(self.root), "worker": self.spec["worker"], "port": self.spec["port"],
            "environment": self.env.name, "contract_sha256": sha(self.spec["contract"]),
            "generation": self.generation, "cache": self.cache, "compatibility": self.compatibility,
            "state": self.state, "accepting": accepting, "active": self.active,
            "last_completed": self.last_completed, "family": self.family, "time": time.time(),
            "placement": self.placement,
            "idle_seconds": idle if not self.active else 0,
            "unload_seconds": self.spec.get("unload_seconds", 300), "stop_seconds": self.spec.get("stop_seconds", 900),
        }
        save(self.root / "state.json", state)
        try:
            save(self.local / "current.json", state)
        except OSError as error:
            # Shared state is authoritative. A full worker root must not
            # bypass the idle loop or prevent shutdown of the owned runtime.
            state["local_state_warning"] = str(error)
            save(self.root / "state.json", state)

    def verify_idle(self):
        queue = self.api("/queue")
        assert not queue["queue_running"] and not queue["queue_pending"], "Unexpected queue entries; preserving runtime"
        owned = {str(pid) for pid, _ in runtimes() if self.process and os.getpgid(pid) == self.process.pid}
        assert not compute_pids() - owned, "Another GPU workload appeared; coordinate contention"

    def verify_models(self, required=None):
        catalog = self.contract.get("models", {})
        if self.contract.get("stage_model_validation") is True:
            names = set(required or ()) | set(self.contract.get("startup_models", []))
            assert isinstance(required, (list, type(None))) and all(isinstance(n, str) for n in names)
            assert names <= set(catalog), "Stage requires an undeclared model"
        else:
            names = set(catalog)  # Older contracts keep their strict behavior.
        for name in sorted(names):
            record = catalog[name]
            path = self.env / "ComfyUI/models" / name
            stat = path.stat()
            identity = {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns,
                        "inode": stat.st_ino, "device": stat.st_dev}
            if "identity" in record:
                assert identity == record["identity"], (name, "Verified model file changed; recheck its bytes")
            else:
                # A portable image has content pins, not another machine's inode.
                # Required models may be acquired after image construction.
                assert stat.st_size == record["bytes"], (name, "Model size differs from its pinned identity")
                # Volume filesystems may have coarse timestamps. Check content
                # for each requested stage rather than trusting inode/mtime.
                assert sha(path) == record["sha256"], (name, "Model bytes differ from their pinned identity")
                after = path.stat()
                assert identity == {"size": after.st_size, "mtime_ns": after.st_mtime_ns,
                    "inode": after.st_ino, "device": after.st_dev}, "Model changed during content verification"
                verified = getattr(self, "verified_model_identities", {})
                verified[name] = identity
                self.verified_model_identities = verified

    def collect_inputs(self):
        pending = self.local / "pending-input-cleanup"
        if not pending.exists():
            return
        self.verify_idle()
        for entry in pending.glob("*.json"):
            info = read(entry)
            if info["owner"] != self.spec["owner"] or Path(info["environment_path"]).resolve() != self.env:
                continue
            root = Path(info["root"]).resolve()
            if not root.is_relative_to(self.media / "sessions"):
                continue
            released = read(root / "release.json") if (root / "release.json").exists() else {}
            if root != self.root and not (released.get("stopped") and released.get("gpu_released")):
                continue
            collect_finished_inputs(root, self.env, self.spec["owner"])
            if all((root / "input-release" / p.name).exists() for p in (root / "finished_jobs").glob("*.json")):
                entry.unlink()

    def cleanup(self, operation, **kwargs):
        self.verify_idle()
        request = {"operation": operation, "request_id": self.spec["session_id"] + "-" + str(time.monotonic_ns()), **kwargs}
        acknowledgement = self.api("/akatz/session", request)
        start = time.monotonic()
        wake_at = start + .5
        while time.monotonic() - start < 30:
            state = self.api("/akatz/session")
            if state["reset_epoch"] > acknowledgement["reset_after"]:
                # The reset hook runs before the main loop's GC/empty_cache.
                # Allow cleanup to finish before the next prompt or measurement.
                time.sleep(.2)
                self.cache = state
                return state
            if time.monotonic() >= wake_at:
                self.api("/akatz/session", request)
                wake_at = time.monotonic() + .5
            time.sleep(.1)
        raise RuntimeError("Executor cleanup was acknowledged but did not finish")

    def start(self):
        self.generation += 1
        assert not runtimes() and not compute_pids(), "Worker occupied before session launch"
        assert command(["git", "-C", str(self.env / "ComfyUI"), "rev-parse", "HEAD"]) == self.contract["comfy_revision"], "ComfyUI revision differs from pinned contract"
        self.verify_models()
        for name, expected in self.contract.get("environment_metadata_sha256", {}).items():
            assert sha(self.env / ".cec" / name) == expected, (name, "Environment dependency metadata drift")
        for record in self.contract["runtime_sources"].values():
            for relative, expected in record["python_sha256"].items():
                assert sha(Path(record["root"]) / relative) == expected, (relative, "Runtime source drift")
        flags = [f for f in self.contract["required_launch_flags"] if f not in {"--cache-none", "--disable-smart-memory"}]
        if "--cache-classic" not in flags:
            flags.append("--cache-classic")
        if "--highvram" not in flags:
            flags.append("--highvram")
        environment = {**os.environ, **self.contract["launch_environment"],
                       "AKATZ_MANAGED_SESSION_ID": self.spec["session_id"], "AKATZ_MANAGED_SESSION_ROOT": str(self.root)}
        if self.spec.get("allocator") == "native-expandable":
            flags.append("--disable-cuda-malloc")
            environment["PYTORCH_ALLOC_CONF"] = "expandable_segments:True"
        self.publish()
        self.process = subprocess.Popen([self.spec["cg_executable"], "-e", self.env.name,
                      "run", "--no-sync", "--", "--listen", "127.0.0.1", "--port", str(self.spec["port"]),
                      "--disable-auto-launch", *flags, "--output-directory", str(self.root / "output"),
                      "--temp-directory", str(self.root / "temp"),
                      *( ["--input-directory", self.spec["input_directory"]] if self.spec.get("input_directory") else [])], stdout=self.log, stderr=subprocess.STDOUT,
                      env=environment, start_new_session=True)
        for _ in range(150):
            assert self.process.poll() is None, "ComfyGit runtime exited before readiness"
            try:
                self.schemas = self.api("/object_info")
                state = self.api("/akatz/session")
                assert state["session_id"] == self.spec["session_id"]
                break
            except Exception:
                time.sleep(1)
        else:
            raise RuntimeError("Unified runtime startup timed out")
        owned = [(pid, args) for pid, args in runtimes() if os.getpgid(pid) == self.process.pid]
        assert len(owned) == 1, "Cannot identify owned ComfyUI runtime"
        self.runtime_pid, args = owned[0]
        self.runtime_identity = process_identity(self.runtime_pid)
        assert args[args.index("--port") + 1] == str(self.spec["port"])
        self.preflight = resource_preflight(self.runtime_pid, args)
        save(self.root / "preflight.json", self.preflight)
        self.last_activity = time.monotonic()
        self.state = "ready"
        self.ready = True
        self.publish()

    def stage(self, packet):
        directory = Path(packet["directory"]).resolve()
        assert directory.is_relative_to(self.media) and directory != self.media
        directory.mkdir(parents=True, exist_ok=True)
        attempt = directory / "attempt.json"
        assert not attempt.exists(), "Existing stage attempt; inspect receipts before retry"
        graphfile = Path(packet["graph"])
        assert sha(graphfile) == packet["graph_sha256"], "Stage graph changed after admission"
        graph = read(graphfile)
        assert all(n["class_type"] in self.schemas for n in graph.values()), "Required workflow node is missing"
        self.verify_idle()
        self.verify_models(packet.get("required_models"))
        previous_family = getattr(self, "family", None) or getattr(self, "last_family", None)
        if (previous_family and previous_family["label"] in self.contract.get("recycle_after_families", [])
                and (self.unloaded or previous_family["key"] != packet["family"]["key"] or self.placement != packet["placement"])):
            previous = self.runtime_identity
            # A declared, measured transition policy recycles only the owned
            # idle runtime while retaining admission and generation settings.
            self.state = "starting"
            self.publish(accepting=False)
            os.killpg(self.process.pid, signal.SIGTERM)
            self.process.wait(timeout=60)
            for _ in range(120):
                if process_identity(previous["pid"]) != previous and str(previous["pid"]) not in compute_pids():
                    break
                time.sleep(.5)
            else:
                raise RuntimeError("Previous model chain did not release its GPU process")
            self.start()
            self.state = "running"
            self.publish()
            save(directory / "runtime-recycle.json", {
                "reason": self.contract.get("transition_policy_reason", "Declared measured model-transition capacity policy"),
                "previous_runtime": previous, "runtime": self.runtime_identity,
                "previous_family": previous_family, "family": packet["family"],
                "environment": str(self.env), "same_graph_sha256": packet["graph_sha256"], "time": time.time()})
        stage_output = self.root / "output" / packet["attempt"]
        assert not stage_output.exists(), "Stage output directory already exists"
        self.cleanup("prepare", family=packet["family"]["key"], placement=packet["placement"],
                     output_directory=str(stage_output))
        self.compatibility = packet["compatibility"]
        self.family = packet["family"]
        self.last_family = packet["family"]
        self.placement = packet["placement"]
        self.unloaded = False
        save(attempt, {**packet, "time": time.time(), "session": str(self.root),
                       "runtime_pid": self.runtime_pid, "profile": packet["profile"],
                       "runtime_contract": self.contract})
        # The admission digest identifies bytes, including JSON formatting.
        # Reserializing Unicode changes that digest and blocks crash recovery.
        stage_graph = directory / "api.json"
        if stage_graph.resolve() != graphfile.resolve():
            shutil.copyfile(graphfile, stage_graph)
        save(directory / "preflight.json", {**self.preflight, "stage_placement": packet["placement"]})
        save(directory / "schemas.json", {n["class_type"]: self.schemas[n["class_type"]] for n in graph.values()})
        cancellation = directory / "cancel.json"
        if cancellation.exists():
            raise InterruptedError("Cancelled before prompt submission")
        submission = self.api("/prompt", {"prompt": graph, "client_id": "akatz-" + packet["attempt"]})
        save(directory / "submission.json", submission)
        prompt = submission["prompt_id"]
        start = time.monotonic()
        interrupted = False
        while True:
            assert self.process.poll() is None, "Owned runtime exited during prompt"
            if cancellation.exists() and not interrupted:
                queue = self.api("/queue")
                assert all(row[1] == prompt for row in queue["queue_running"] + queue["queue_pending"]), "Unexpected queue; cannot interrupt"
                self.api("/interrupt", {"prompt_id": prompt})
                interrupted = True
            history = self.api("/history/" + prompt)
            if prompt in history:
                break
            status = {"state": "running", "elapsed": time.monotonic() - start, "prompt_id": prompt,
                      "gpu": command(["nvidia-smi", "--query-gpu=memory.used,utilization.gpu", "--format=csv,noheader,nounits"]),
                      "memory": {line.split(":", 1)[0]: line.split(":", 1)[1].strip() for line in
                          (Path("/proc") / str(self.runtime_pid) / "status").read_text().splitlines()
                          if line.startswith(("VmRSS:", "VmHWM:", "VmSwap:"))}}
            save(directory / "status.json", status)
            with (directory / "resources.jsonl").open("a") as log:
                log.write(json.dumps(status) + "\n")
            self.publish()
            time.sleep(.5)
        save(directory / "history.original.json", history)
        save(directory / "history.json", history)
        self.verify_idle()
        if interrupted:
            raise InterruptedError("Owned prompt interrupted")
        result = history[prompt]
        if result["status"]["status_str"] != "success":
            errors = [v for k, v in result["status"]["messages"] if k == "execution_error"]
            error = errors[-1] if errors else {}
            raise RuntimeError(str(error.get("exception_type", "Execution failed")) + ": " + str(error.get("exception_message", "Inspect history")))
        target = directory / "output"
        assert not target.exists(), "Completed output layout already exists"
        output_receipt = []
        for source in sorted(stage_output.rglob("*")):
            if source.is_symlink():
                raise RuntimeError("Symlink in stage output")
            if source.is_file():
                relative = source.relative_to(stage_output)
                dest = target / relative
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, dest)
                digest = sha(source)
                assert sha(dest) == digest
                output_receipt.append({"path": str(relative), "sha256": digest, "bytes": source.stat().st_size})
        target.mkdir(exist_ok=True)
        cache = self.cleanup("settle")
        save(directory / "history.json", output_references(history, stage_output, target))
        save(directory / "output-receipt.json", {"source": str(stage_output), "destination": str(target), "files": output_receipt,
             "history_mapping": {"from": str(stage_output), "to": str(target), "original": "history.original.json"}})
        self.api("/history", {"delete": [prompt]})
        save(directory / "complete.json", {"prompt_id": prompt, "elapsed": time.monotonic() - start,
             "session": str(self.root), "runtime_pid": self.runtime_pid, "queue_empty": True,
             "completed_at": time.time(), "cache": cache, "attempt": packet["attempt"]})
        save(directory / "status.json", {"state": "complete", "elapsed": time.monotonic() - start})
        # Shared scratch duplicates are reclaimed only after the copied files
        # and completion receipt are verified. Final stage outputs remain.
        shutil.rmtree(stage_output)
        self.last_completed = packet["attempt"]

    def run(self):
        self.start()
        while True:
            assert self.process.poll() is None, "Owned session runtime exited"
            packet = None
            with (self.root / "inbox.lock").open("a") as admission:
                fcntl.flock(admission, fcntl.LOCK_EX)
                queued = sorted((self.root / "inbox").glob("*.json"))
                pending_path = self.root / "pending-admissions.json"
                protection = read(pending_path) if pending_path.exists() else {}
                if protection:
                    assert protection["owner"] == self.spec["owner"] and protection["session_id"] == self.spec["session_id"]
                pending = bool(protection.get("jobs"))
                idle = time.monotonic() - self.last_activity
                stopping = not queued and ((self.root / "stop.json").exists() or (not pending and idle >= self.spec.get("stop_seconds", 900)))
                if stopping:
                    self.state = "stopping"
                    self.publish(accepting=False)
                    break
                if queued:
                    packet = read(queued[0])
                    queued[0].rename(queued[0].with_suffix(".accepted"))
                    self.active = packet["attempt"]
                    self.state = "running"
                    self.last_activity = time.monotonic()
                    self.publish()
                elif not pending and idle >= self.spec.get("unload_seconds", 300) and not self.unloaded:
                    self.state = "unloading"
                    self.publish(accepting=False)
                    before = command(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"])
                    cache = self.cleanup("unload")
                    assert cache["model_entries"] == 0, "Model cache did not clear"
                    save(self.root / "unload.json", {"time": time.time(), "idle_seconds": idle, "before_mib": before,
                         "after_mib": command(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"]), "cache": cache})
                    self.unloaded = True
                    self.compatibility = None
                    self.family = None
                    self.state = "unloaded_idle"
                if packet is None:
                    collect_finished_inputs(self.root, self.env, self.spec["owner"])
                    self.collect_inputs()
                self.publish()
                fcntl.flock(admission, fcntl.LOCK_UN)
            if packet:
                try:
                    self.stage(packet)
                except InterruptedError as error:
                    save(Path(packet["directory"]) / "cancelled.json", {"job": packet["job"], "reason": str(error)})
                    # Cancellation recycles the owned process. Remaining jobs
                    # stay in the hub queue rather than adopting unhealthy state.
                    raise
                except BaseException as error:
                    directory = Path(packet["directory"])
                    message = str(error)
                    if self.runtime_pid and process_identity(self.runtime_pid) != self.runtime_identity:
                        try:
                            kernel = command(["journalctl", "-k", "--since", "5 minutes ago", "--no-pager"])
                            lines = [line for line in kernel.splitlines() if f"Killed process {self.runtime_pid} " in line]
                            if lines:
                                message = "Worker host RAM exhausted; kernel killed the owned runtime. Generation settings were retained."
                                save(directory / "host-capacity.json", {"runtime": self.runtime_identity, "kernel": lines,
                                     "memory": command(["free", "-b"]), "full_resources_verified": True})
                        except Exception:
                            pass
                    save(directory / "failure.json", {"error": message, "traceback": traceback.format_exc()})
                    raise
                self.active = None
                self.last_activity = time.monotonic()
                self.state = "warm_idle"
                self.publish()
            else:
                time.sleep(.5)

    def stop(self):
        self.state = "stopping"
        try:
            self.publish(accepting=False)
        except OSError:
            # Even a failed status write must still reach ownership checks
            # and release. The final receipt will be attempted afterwards.
            pass
        try:
            owned = {str(pid) for pid, _ in runtimes() if self.process and os.getpgid(pid) == self.process.pid}
            if self.process and (self.process.poll() is None or owned):
                if self.ready and self.runtime_identity and process_identity(self.runtime_pid) == self.runtime_identity:
                    identity = self.api("/akatz/session")
                    assert identity["session_id"] == self.spec["session_id"], "Runtime ownership changed"
                    queue = self.api("/queue")
                    assert not queue["queue_running"] and not queue["queue_pending"], "Unexpected active queue; preserving runtime"
                os.killpg(self.process.pid, signal.SIGTERM)
                self.process.wait(timeout=60)
                for _ in range(120):
                    if not owned.intersection(compute_pids()):
                        break
                    time.sleep(.5)
                else:
                    raise RuntimeError("Owned GPU process did not release")
            if self.runtime_pid:
                for _ in range(120):
                    if str(self.runtime_pid) not in compute_pids():
                        break
                    time.sleep(.5)
                else:
                    raise RuntimeError("Owned CUDA process still present")
            self.release = {"stopped": True, "gpu_released": True, "session_id": self.spec["session_id"],
                            "runtime": self.runtime_identity, "controller": self.boot_identity, "time": time.time()}
            try:
                collect_finished_inputs(self.root, self.env, self.spec["owner"])
            except Exception as error:
                self.release["input_cleanup_warning"] = str(error)
            self.state = "stopped"
        except BaseException as error:
            self.release = {"stopped": False, "gpu_released": False, "reason": str(error), "time": time.time()}
            self.state = "needs_inspection"
        save(self.root / "release.json", self.release)
        self.publish(accepting=False)
        self.log.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("spec", type=Path)
    parser.add_argument("--release-idle-orphan", action="store_true")
    parser.add_argument("--control", choices=["launch", "inspect", "observe", "pending", "enqueue", "release", "cancel", "finish"])
    args = parser.parse_args()
    if args.control:
        print(json.dumps(control(args.control, read(args.spec), json.load(sys.stdin))))
        raise SystemExit(0)
    if args.release_idle_orphan:
        print(json.dumps(release_idle_orphan(read(args.spec))))
        raise SystemExit(0)
    session = None
    try:
        session = Session(read(args.spec))
        session.run()
    except BaseException as error:
        root = Path(read(args.spec)["root"])
        root.mkdir(parents=True, exist_ok=True)
        save(root / "failure.json", {"error": str(error), "traceback": traceback.format_exc()})
    finally:
        if session:
            session.stop()
