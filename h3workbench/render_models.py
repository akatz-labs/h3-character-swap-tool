"""Known swap checkpoints and immutable generation settings."""

import copy
import hashlib
import json
import runpy
from pathlib import Path

from .config import WORKERS

# Use the worker's pure planner without importing its ComfyUI/Torch package.
_windowing = runpy.run_path(str(Path(__file__).resolve().parents[1] /
                              "worker_nodes/h3_character_swap/planning.py"))
WINDOW_FRAMES = _windowing["WINDOWS"]
HISTORY_FRAMES = _windowing["HISTORY"]


def window_plan(shot, config=None):
    config = settings(shot) if config is None else config
    count = shot["end"] - shot["start"]
    windows = _windowing["plan_windows"](count, config["window_frames"], config["history_frames"])
    return {"frames": count, "fps": 24, "window_frames": config["window_frames"],
            "history_frames": config["history_frames"], "windows": windows,
            "window_choices": WINDOW_FRAMES, "history_choices": HISTORY_FRAMES}

LORAS = {
    "none": {"id": "none", "label": "No character-swap LoRA", "filename": None, "sha256": None},
    "v1-1000": {
        "id": "v1-1000",
        "label": "Character Swap LoRA V1",
        "filename": "h3-character-swap-v1/h3_character_swap_pro4500_1000.safetensors",
        "sha256": "4b2a3f420ae804c0aa3422761ff84dbd1bf52eef6900ffab6d2e66df63cb4e79",
    },
    "v2-1000": {
        "id": "v2-1000",
        "label": "Character Swap LoRA V1.5",
        "filename": "h3-v2-challenge-eval-20261013/v2-1000.safetensors",
        "sha256": "8f2c3c3eba161df8d0c6099598b4730f641f5ce01e04cd16d2427cff8a35218f",
    },
}


def sha(path):
    with Path(path).open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def settings(shot):
    result = {"lora_id": shot.get("lora_id", "v1-1000"),
              "use_anchor": shot.get("use_anchor", True),
              "use_mask": shot.get("use_mask", True),
              "window_frames": shot.get("window_frames", 124),
              "history_frames": shot.get("history_frames", 18)}
    if not isinstance(result["lora_id"], str) or result["lora_id"] not in LORAS:
        raise ValueError("Choose V1, V1.5, or no character-swap LoRA")
    if any(type(result[k]) is not bool for k in ("use_anchor", "use_mask")):
        raise ValueError("Conditioning switches must be true or false")
    if any(type(result[k]) is not int for k in ("window_frames", "history_frames")):
        raise ValueError("Diffusion window and history must be whole frame counts")
    _windowing["validate_settings"](result["window_frames"], result["history_frames"])
    return result


def identify(graph):
    filename = graph.get("850", {}).get("inputs", {}).get("lora_name")
    return next((copy.deepcopy(x) for x in LORAS.values() if x["filename"] == filename), None)


def verify_worker(worker, model):
    from . import remote

    if model["filename"] is None:
        return {"path": None, "sha256": None}
    path = Path(WORKERS[worker]["home"]) / "comfygit/environments/h3-character-swap/ComfyUI/models/loras" / model["filename"]
    result = json.loads(remote.ssh(worker, f"""import hashlib,json
from pathlib import Path
p=Path({str(path)!r})
h=hashlib.sha256()
with p.open('rb') as f:
 for block in iter(lambda:f.read(1024*1024),b''): h.update(block)
digest=h.hexdigest()
print(json.dumps({{'path':str(p.resolve()),'sha256':digest}}))
"""))
    if result["sha256"] != model["sha256"]:
        raise ValueError("Worker LoRA does not match the recorded training checkpoint")
    return result


def offloading(job):
    """Read the accepted execution choice; old jobs retain their original policy."""
    options = job["data"].get("options") or {}
    return bool(options.get("offload_after") or options.get("model_offloading", False))


def retry_offload(id):
    from . import store, refinement

    previous = store.job_get(id)
    failure = refinement.capacity_failure(previous)
    if previous["kind"] != "render" or not failure or failure["offloading"]:
        raise ValueError("Model offloading requires this render's recorded full-resource GPU memory failure")
    data = previous["data"]
    current = store.clip_get(previous["clip_id"])
    shot = next((s for s in current["shots"] if s["id"] == previous["shot_id"]), None)
    if not shot or any(shot.get(k) != data["shot"].get(k) for k in ("start", "end")):
        raise ValueError("Shot boundaries changed; the failed render cannot be retried here")
    queued = {k: copy.deepcopy(data[k]) for k in ("snapshot", "shot", "worker", "render_spec", "generation", "lora", "segment_plan") if k in data}
    options = {**copy.deepcopy(data.get("options") or {}), "model_offloading": True, "offload_after": id}
    queued.update(options=options, phase="Queued · model offloading after measured GPU limit")
    queued.update(execution_backend="shared" if store.setting("shared_executor", False) else "direct",
                  requested_worker=data.get("requested_worker", data["worker"]))
    return store.job_create("render", previous["clip_id"], previous["shot_id"], queued)
