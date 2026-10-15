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
    lock = (Path.home() / ".local/state/h3-workbench/worker.lock").open("a")
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
        for route in ("/h3-workbench/session", "/queue"):
            request = urllib.request.Request(url + route, headers={"X-H3-Session": spec["session_id"]})
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
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "inbox").mkdir(exist_ok=True)
        self.local = Path.home() / ".local/state/h3-workbench"
        self.local.mkdir(parents=True, exist_ok=True)
        self.owner_lock = (self.local / "worker.lock").open("a")
        fcntl.flock(self.owner_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.boot_identity = process_identity(os.getpid())
        self.log = (self.root / "runtime.log").open("a")

    def api(self, path, body=None):
        request = urllib.request.Request(self.url + path,
                    data=None if body is None else json.dumps(body).encode(),
                    headers={"Content-Type": "application/json", "X-H3-Session": self.spec["session_id"]})
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

    def verify_models(self):
        for name, record in self.contract.get("models", {}).items():
            path = self.env / "ComfyUI/models" / name
            stat = path.stat()
            identity = {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns,
                        "inode": stat.st_ino, "device": stat.st_dev}
            assert identity == record["identity"], (name, "Verified model file changed; recheck its bytes")

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
        acknowledgement = self.api("/h3-workbench/session", request)
        start = time.monotonic()
        wake_at = start + .5
        while time.monotonic() - start < 30:
            state = self.api("/h3-workbench/session")
            if state["reset_epoch"] > acknowledgement["reset_after"]:
                # The reset hook runs before the main loop's GC/empty_cache.
                # Allow cleanup to finish before the next prompt or measurement.
                time.sleep(.2)
                return state
            if time.monotonic() >= wake_at:
                self.api("/h3-workbench/session", request)
                wake_at = time.monotonic() + .5
            time.sleep(.1)
        raise RuntimeError("Executor cleanup was acknowledged but did not finish")

    def start(self):
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
                       "H3_WORKBENCH_SESSION_ID": self.spec["session_id"], "H3_WORKBENCH_SESSION_ROOT": str(self.root)}
        if self.spec.get("allocator") == "native-expandable":
            flags.append("--disable-cuda-malloc")
            environment["PYTORCH_ALLOC_CONF"] = "expandable_segments:True"
        self.publish()
        self.process = subprocess.Popen([str(Path.home() / ".local/bin/cg"), "-e", self.env.name,
                      "run", "--no-sync", "--", "--listen", "127.0.0.1", "--port", str(self.spec["port"]),
                      "--disable-auto-launch", *flags, "--output-directory", str(self.root / "output"),
                      "--temp-directory", str(self.root / "temp"),
                      *( ["--input-directory", self.spec["input_directory"]] if self.spec.get("input_directory") else [])], stdout=self.log, stderr=subprocess.STDOUT,
                      env=environment, start_new_session=True)
        for _ in range(150):
            assert self.process.poll() is None, "ComfyGit runtime exited before readiness"
            try:
                self.schemas = self.api("/object_info")
                state = self.api("/h3-workbench/session")
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
        self.verify_models()
        previous_family = getattr(self, "family", None) or getattr(self, "last_family", None)
        if (self.contract.get("family_transition_policy") == "recycle-after-h3"
                and previous_family and previous_family["label"] == "H3"
                and (self.unloaded or previous_family["key"] != packet["family"]["key"] or self.placement != packet["placement"])):
            previous = self.runtime_identity
            # This worker's measured 62 GiB RAM failures occur when replacing
            # an H3 model chain. Recycle only this verified idle runtime, keep
            # the controller/admission lock, and preserve the accepted graph.
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
                "reason": "Measured host RAM exhaustion replacing H3 chains on Fey; retained generation settings",
                "previous_runtime": previous, "runtime": self.runtime_identity,
                "previous_family": previous_family, "family": packet["family"],
                "environment": str(self.env), "same_graph_sha256": packet["graph_sha256"], "time": time.time()})
        stage_output = self.root / "output" / packet["attempt"]
        assert not stage_output.exists(), "Stage output directory already exists"
        self.cleanup("prepare", family=packet["family"]["key"], placement=packet["placement"],
                     output_directory=str(stage_output))
        self.family = packet["family"]
        self.last_family = packet["family"]
        self.placement = packet["placement"]
        self.unloaded = False
        save(attempt, {**packet, "time": time.time(), "session": str(self.root),
                       "runtime_pid": self.runtime_pid, "profile": packet["profile"],
                       "runtime_contract": self.contract})
        save(directory / "api.json", graph)
        save(directory / "preflight.json", {**self.preflight, "stage_placement": packet["placement"]})
        save(directory / "schemas.json", {n["class_type"]: self.schemas[n["class_type"]] for n in graph.values()})
        cancellation = directory / "cancel.json"
        if cancellation.exists():
            raise InterruptedError("Cancelled before prompt submission")
        submission = self.api("/prompt", {"prompt": graph, "client_id": "h3workbench-" + packet["attempt"]})
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
                idle = time.monotonic() - self.last_activity
                stopping = not queued and ((self.root / "stop.json").exists() or idle >= self.spec.get("stop_seconds", 900))
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
                elif idle >= self.spec.get("unload_seconds", 300) and not self.unloaded:
                    self.state = "unloading"
                    self.publish(accepting=False)
                    before = command(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"])
                    cache = self.cleanup("unload")
                    assert cache["model_entries"] == 0, "Model cache did not clear"
                    save(self.root / "unload.json", {"time": time.time(), "idle_seconds": idle, "before_mib": before,
                         "after_mib": command(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"]), "cache": cache})
                    self.unloaded = True
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
                    identity = self.api("/h3-workbench/session")
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
    args = parser.parse_args()
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
