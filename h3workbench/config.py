from pathlib import Path
import os
import shutil

ROOT = Path(__file__).resolve().parents[1]
# Source installations use local state and explicitly configured workers.
PORTABLE = os.environ.get("H3_WORKBENCH_PORTABLE", "1") != "0"
DEFAULT_DATA = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "akatz/apps/h3-workbench"
DATA = Path(os.environ.get("H3_WORKBENCH_STATE", DEFAULT_DATA)).resolve()
MEDIA = Path(os.environ.get("H3_WORKBENCH_MEDIA", DATA / "media")).resolve()
SHARED_ROOT = Path(os.environ.get("H3_WORKBENCH_SHARED_ROOT", MEDIA)).resolve()
MEDIA_MOUNT = Path(os.environ["H3_WORKBENCH_MEDIA_MOUNT"]).resolve() if os.environ.get("H3_WORKBENCH_MEDIA_MOUNT") else None
PILOT = Path(os.environ.get("H3_WORKBENCH_LEGACY_PILOT", DATA / "legacy-imports/pilot"))
REPAIR = PILOT.parent / "repair"
BASELINE = PILOT.parent / "runtime-contract.json"
BASE_PATH = os.environ.get("H3_WORKBENCH_BASE_PATH", "").rstrip("/")
PORT = int(os.environ.get("H3_WORKBENCH_PORT", "8330"))
WORKERS = {}
if os.environ.get("H3_WORKBENCH_WORKERS"):
    import json
    import re
    configured = json.loads(Path(os.environ["H3_WORKBENCH_WORKERS"]).read_text())
    if not isinstance(configured, dict):
        raise ValueError("Worker configuration must be an object")
    for key, item in configured.items():
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,159}", key) or not isinstance(item, dict) or set(item) - {"ssh", "home", "shared", "port", "target"}:
            raise ValueError("Invalid configured worker")
        if "target" not in item and (not isinstance(item.get("ssh"), str) or not re.fullmatch(r"(?:[a-zA-Z0-9_][a-zA-Z0-9_.-]*@)?[a-zA-Z0-9][a-zA-Z0-9_.-]*", item["ssh"])):
            raise ValueError("Configure a shared target or valid SSH alias")
    WORKERS = configured


def check_media_mount():
    if MEDIA_MOUNT is not None:
        if not MEDIA.is_relative_to(MEDIA_MOUNT):
            raise ValueError("Media directory must be inside the configured media mount")
        if not os.path.ismount(MEDIA_MOUNT):
            raise RuntimeError("Configured media drive is not mounted; refusing local fallback")


check_media_mount()
for p in [DATA, MEDIA / "batches", MEDIA / "jobs", MEDIA / "exports"]:
    p.mkdir(parents=True, exist_ok=True)


def check_storage(expected_gib):
    check_media_mount()
    if shutil.disk_usage(MEDIA).free < expected_gib * 1024 ** 3:
        raise ValueError("The selected media drive has insufficient free space for this import")
