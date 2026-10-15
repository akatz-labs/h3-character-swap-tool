"""Pinned Whisper in the shared Hugging Face cache, with legacy-path preservation."""
from contextlib import contextmanager
from pathlib import Path
import hashlib
import json
import shutil
import re
import threading
import time
import uuid

from . import store
from .config import DATA
from .ecosystem import shared_models_root, cache

MODELS = shared_models_root()
from .transcription import MODEL, REVISION, FILES

RESOURCE_ID = "transcription-whisper-medium-en"
INSTALL_KIND = "transcription_model_install"
FILE_SIZES = {"config.json": 2643, "model.bin": 1527904330,
              "tokenizer.json": 2128466, "vocabulary.txt": 422309}
SIZE = sum(FILE_SIZES.values())
LOCK = threading.RLock()
PLANS = {}


def cache_root():
    legacy = DATA / "models" / "transcription"
    if (legacy / ("models--" + MODEL.replace("/", "--"))).exists():
        return legacy
    return MODELS / "huggingface" / "hub"


def is_shared():
    return cache_root() == MODELS / "huggingface" / "hub"


def check_storage(expected_gib):
    root = cache_root()
    root.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(root).free < expected_gib * 1024 ** 3:
        raise ValueError("The shared model drive has insufficient free space")


def snapshot_path():
    return cache.snapshot_path(cache_root(), MODEL, REVISION)


def installed():
    root = cache_root().resolve()
    try:
        return all((snapshot_path() / name).resolve().is_relative_to(root)
                   and (snapshot_path() / name).stat().st_size == FILE_SIZES[name]
                   for name in FILES)
    except OSError:
        return False


def inventory():
    return {"id": RESOURCE_ID, "name": "Whisper Medium.en · transcription",
            "requirement": "optional", "state": "installed" if installed() else "missing",
            "sizeBytes": SIZE, "relativePath": str(snapshot_path()),
            "locationLabel": "Shared models · Hugging Face cache" if is_shared() else "Existing app cache · preserved",
            "sourceNote": "English transcription · about 1.5 GB. Reused by Akatz apps at this pinned revision.",
            "acquisition": "download", "removable": True}


def resource_jobs():
    rows = [j for j in store.jobs() if j["kind"] == INSTALL_KIND]
    return [job_view(j) for j in rows]


def job_view(job):
    state = job["state"]
    status = {"success": "succeeded", "completed": "succeeded", "running": "running",
              "queued": "queued", "waiting": "queued", "cancelled": "cancelled"}.get(state, "failed")
    return {"id": job["id"], "kind": "download", "status": status,
            "message": job["data"].get("phase", "Whisper transcription model"),
            "progress": 1 if status == "succeeded" else None,
            "completedBytes": SIZE if status == "succeeded" else 0,
            "totalBytes": SIZE, "error": job["data"].get("error")}


def busy():
    return any(j["kind"] in {"transcribe", INSTALL_KIND}
               for j in store.pending_jobs(("queued", "waiting", "running", "cancelling")))


@contextmanager
def model_use(*, exclusive=False, maintenance=False):
    if not LOCK.acquire(blocking=False):
        raise store.Conflict("Whisper is busy. Wait for transcription or model installation to finish.")
    try:
        with cache.cache_use(cache_root(), MODEL, exclusive=exclusive, maintenance=maintenance):
            yield
    except cache.CacheBusy as error:
        raise store.Conflict(str(error)) from error
    finally:
        LOCK.release()


def request_install():
    with model_use():
        if installed():
            return {"complete": True}
        return job_view(store.job_get(store.job_create(INSTALL_KIND, data={"worker": "cpu"})))


def run_install(job):
    from huggingface_hub import snapshot_download
    from .remote import check_cancel
    try:
        with model_use(exclusive=True):
            check_cancel(job)
            if installed():
                store.job_update(job["id"], "success", phase="Reusing shared Whisper model")
                return
            check_storage(2)
            store.job_update(job["id"], phase="Downloading Whisper Medium.en · about 1.5 GB")
            snapshot_download(repo_id=MODEL, revision=REVISION, cache_dir=str(cache_root()),
                              allow_patterns=FILES, token=False)
            check_cancel(job)
            if not installed():
                raise ValueError("Whisper download is incomplete. Install again to resume the cached files.")
            store.job_update(job["id"], "success", phase="Whisper installed")
    except Exception as error:
        from .remote import Cancelled
        store.job_update(job["id"], "cancelled" if isinstance(error, Cancelled) else "failed",
                         phase="Whisper installation stopped", error=str(error))


def deletion():
    from huggingface_hub import scan_cache_dir
    root = cache_root().resolve()
    repo_path = root / ("models--" + MODEL.replace("/", "--"))
    info = scan_cache_dir(root)
    repo = next((r for r in info.repos if r.repo_id == MODEL and r.repo_type == "model"), None)
    if not repo or repo.repo_path.resolve() != repo_path:
        raise ValueError("Whisper cache was not found. Refresh Resources.")
    revision = next((r for r in repo.revisions if r.commit_hash == REVISION), None)
    if not revision:
        raise ValueError("The installed Whisper revision changed. Refresh Resources.")
    plan = info.delete_revisions(REVISION)
    def contained(path):
        resolved = path.resolve()
        # Current HF caches deduplicate Xet weights under the cache's own
        # blobs/<prefix>/<hash>. Let HF remove them only when unreferenced.
        shared = (bool(re.fullmatch(r"[a-f0-9]{64}", resolved.name))
                  and resolved.parent == root / "blobs" / resolved.name[:2])
        return resolved.is_relative_to(repo_path) or shared
    # Never let malformed cache links widen this reviewed cache removal.
    paths = set(plan.blobs) | set(plan.refs) | set(plan.snapshots) | set(plan.repos)
    identity = []
    for path in sorted(paths):
        path = Path(path)
        if not contained(path):
            raise ValueError("Whisper cache links point outside its model folder")
        stat = path.stat()
        identity.append((str(path), stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns))
    for file in revision.files:
        path = file.blob_path
        if not contained(path):
            raise ValueError("Whisper cache contains an external model file")
        stat = path.stat()
        identity.append((str(path), stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns))
    digest = hashlib.sha256(json.dumps(sorted(identity)).encode()).hexdigest()
    return plan, digest


def removal_plan():
    with model_use(exclusive=True, maintenance=True):
        if busy():
            raise store.Conflict("Wait for transcription and Whisper downloads before uninstalling.")
        plan, digest = deletion()
        now = time.time()
        for key in list(PLANS):
            if PLANS[key][1] < now:
                del PLANS[key]
        id = "whisper-" + uuid.uuid4().hex
        PLANS[id] = (digest, now + 600)
        return {"id": id, "digest": digest, "resourceId": RESOURCE_ID,
                "name": inventory()["name"], "path": str(snapshot_path()), "sizeBytes": SIZE,
                "reclaimBytes": plan.expected_freed_size, "usedByApps": ["All Akatz apps using this shared model" if is_shared() else "This app's transcription"],
                "environments": [], "blockers": [], "canUninstall": True}


def uninstall(body):
    with model_use(exclusive=True, maintenance=True):
        if set(body) != {"planId", "digest", "confirm"} or body["confirm"] != RESOURCE_ID:
            raise ValueError("Review the Whisper model before uninstalling")
        prior = PLANS.get(body["planId"])
        if not prior or prior[0] != body["digest"] or prior[1] < time.time():
            raise store.Conflict("Whisper removal review expired. Review again.")
        if busy():
            raise store.Conflict("Wait for transcription and Whisper downloads before uninstalling.")
        plan, digest = deletion()
        if digest != prior[0]:
            raise store.Conflict("Whisper model files changed. Review again.")
        plan.execute()
        del PLANS[body["planId"]]
        if installed():
            raise ValueError("Whisper removal did not finish. Refresh Resources.")
        return {"complete": True}
