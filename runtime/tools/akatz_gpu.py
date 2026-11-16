"""Standard-library client for one bounded GPU task.

The process calls acquire before touching CUDA and close only after its owned
GPU children have exited. A longer background queue invokes this per item; this
is the cooperative boundary, with no SIGSTOP or automatic replay.
"""
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request


class GpuBusy(BlockingIOError):
    pass


class GpuJob:
    def __init__(self, *, key, purpose, app, gpu_uuid, priority=50,
                 protection_seconds=3600, interruptibility="never",
                 minimum_free_mib=0, origin=None):
        self.origin = origin or os.environ.get("AKATZ_GPU_COORDINATOR", "http://127.0.0.1:8871")
        url = urllib.parse.urlsplit(self.origin)
        if url.scheme != "http" or url.hostname not in ("127.0.0.1", "localhost", "::1") or url.username or url.path not in ("", "/") or url.query or url.fragment:
            raise ValueError("Coordinator must be worker loopback")
        self.definition = dict(key=key, purpose=purpose, owner={"app": app}, gpuUuid=gpu_uuid,
            priority=priority, protectionSeconds=protection_seconds,
            interruptibility=interruptibility, minimumFreeMiB=minimum_free_mib)
        self.claim = None
        self._stop = threading.Event()
        self._thread = None
        self.heartbeat_error = None

    def _request(self, path, body=None):
        request = urllib.request.Request(self.origin + path,
            data=None if body is None else json.dumps(body).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.load(response)

    def refresh(self):
        self.claim = self._request("/v1/claims/" + self.claim["id"])
        return self.claim

    def _action(self, action, **fields):
        return self._request("/v1/claims/" + self.claim["id"] + "/" + action,
                             dict(generation=self.claim["generation"], **fields))

    def acquire(self, wait_seconds=0):
        self.claim = self._request("/v1/claims", self.definition)
        if self.claim["phase"] == "released":
            self.claim = self._action("resume")
        deadline = time.monotonic() + wait_seconds
        while not (self.claim["holds"] and self.claim["phase"] == "granted"):
            if self.claim["phase"] in ("completed", "retired", "cancelled"):
                raise RuntimeError("GPU job already terminal; inspect its receipt before retrying")
            if time.monotonic() >= deadline:
                reason = self.claim.get("blockedReason", "another GPU owner has priority")
                self.refresh()
                self.claim = self._action("release")
                raise GpuBusy("busy: " + reason)
            time.sleep(1)
            self.refresh()
            self.claim = self._action("heartbeat")
        self.claim = self._action("heartbeat", pid=os.getpid())
        self.claim = self._action("begin")
        self._thread = threading.Thread(target=self._heartbeat, daemon=True)
        self._thread.start()
        return self

    def _heartbeat(self):
        while not self._stop.wait(5):
            try:
                self.refresh()
                self.claim = self._action("heartbeat")
            except Exception as error:
                # Losing the coordinator does not kill work or grant anything.
                self.heartbeat_error = str(error)

    def attach(self, pid):
        """Attach a task-owned child before sending it GPU work."""
        self.refresh()
        self.claim = self._action("heartbeat", pid=pid)

    def close(self, *, complete=True, outcome="succeeded"):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=25)
        if not self.claim or self.claim["phase"] in ("completed", "retired", "cancelled", "released"):
            return
        self.refresh()
        if self.claim["phase"] in ("busy", "granted", "idle"):
            self.claim = self._action("idle")
        self.claim = self._action("release", complete=complete, outcome={"status": outcome})

    @property
    def receipt(self):
        return None if self.claim is None else {
            "claim_id": self.claim["id"], "generation": self.claim["generation"],
            "coordinator_id": self.claim["coordinatorId"], "phase": self.claim["phase"],
            "heartbeat_error": self.heartbeat_error,
        }
