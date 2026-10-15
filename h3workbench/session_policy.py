"""Pure lifecycle and compatibility policy shared by hub and worker controllers."""
import hashlib
import json

UNLOAD_SECONDS = 300
STOP_SECONDS = 900


def idle_action(idle_seconds, unloaded, *, pending=False):
    if pending:
        return None
    if idle_seconds >= STOP_SECONDS:
        return "stop"
    if idle_seconds >= UNLOAD_SECONDS and not unloaded:
        return "unload"
    return None


def placement(profile):
    if profile in {"full", "alpha"}:
        return "full"
    if profile in {"legacy", "full-offload", "alpha-offload"}:
        return "offload"
    if profile == "full-stream":
        return "stream"
    if profile == "full-layer-stream":
        return "layer-stream"
    raise ValueError("Unknown runtime profile")


def allocator(profile):
    return "native-expandable" if profile in {"full-stream", "full-layer-stream"} else "default"


def family(graph):
    """Model chains, not source/seed/output identities, determine compatibility."""
    model_classes = {
        "UNETLoader", "CLIPLoader", "VAELoader", "CheckpointLoaderSimple",
        "LoraLoaderModelOnly", "LTXICLoRALoaderModelOnly", "MiniMaxH3SigmaShift",
        "ModelAttentionBackend",
    }
    models = []
    for node in graph.values():
        if node["class_type"] in model_classes:
            inputs = {k: v for k, v in node["inputs"].items()
                      if not (isinstance(v, list) and len(v) == 2)}
            models.append((node["class_type"], inputs))
    stable = sorted(models, key=lambda n: json.dumps(n, sort_keys=True))
    digest = hashlib.sha256(json.dumps(stable, sort_keys=True).encode()).hexdigest()
    names = " ".join(str(n[1]) for n in stable).lower()
    label = "SAM" if "sam3" in names else "AlphaGen" if "ltx" in names else "H3" if "minimax" in names else "Face tracking"
    loras = sorted(n[1]["lora_name"] for n in stable if "lora_name" in n[1])
    return {"label": label, "key": digest, "loras": loras}


def choose_worker(job, workers, excluded=()):
    """FIFO dispatch with an available compatible warm worker preference."""
    label = {"mask": "SAM", "mask_edit": "SAM", "refine_mask": "SAM",
             "render": "H3", "refine_extract": "AlphaGen", "backplate": "H3",
             "refine_face": "Face tracking"}.get(job["kind"])
    candidates = []
    for worker, value in workers.items():
        if worker in excluded or value["state"] not in {"available", "ready", "warm_idle", "unloaded_idle"}:
            continue
        warm = value["state"] == "warm_idle" and (value.get("family") or {}).get("label") == label
        expected_placement = "offload" if job["kind"] == "refine_extract" or (job["data"].get("options") or {}).get("offload_after") or (job["data"].get("options") or {}).get("model_offloading") else "full"
        if value.get("placement") and value["placement"] != expected_placement:
            warm = False
        if warm and job["kind"] == "render":
            target = (job["data"].get("lora") or {}).get("filename")
            current = (value.get("family") or {}).get("loras", [])
            warm = target in current if target else bool(current) and all(
                name.rsplit("/", 1)[-1] == "minimax_h3_fl2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors"
                for name in current)
        candidates.append((0 if warm else 1 if value["state"] in {"ready", "unloaded_idle", "warm_idle"} else 2, worker))
    return min(candidates)[1] if candidates else None


def attempt_id(job_id, directory):
    return job_id + "-" + hashlib.sha256(str(directory).encode()).hexdigest()[:16]
