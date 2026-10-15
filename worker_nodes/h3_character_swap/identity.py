"""Hash input tensors and model ancestry without a persistent media cache."""
import hashlib
import json
from pathlib import Path
CACHE_VERSION = 1
def tensor_hash(tensor):
    import torch
    digest = hashlib.sha256()
    digest.update(str((tuple(tensor.shape), str(tensor.dtype))).encode())
    # Avoid a second full-video byte allocation, including for CUDA inputs.
    for frame in tensor:
        digest.update(frame.detach().cpu().contiguous().view(torch.uint8).numpy().tobytes())
    return digest.hexdigest()


def is_graph_link(value, graph):
    """Recognize API connections without mistaking JSON widget lists for links.

    ComfyUI connections are [string node ID, nonnegative integer output index].
    Validate types before dictionary membership: two-item literal lists can
    start with an unhashable dict or list. Keep literals intact in the digest.
    """
    return (isinstance(value, list) and len(value) == 2
            and isinstance(value[0], str)
            and type(value[1]) is int and value[1] >= 0
            and value[0] in graph)


def configuration_key(source, anchor, settings, execution_prompt, node_id):
    """Hash actual pixels plus the model/encoder/VAE ancestry and file versions."""
    import folder_paths
    graph = execution_prompt or {}
    fields = {"unet_name": "diffusion_models", "clip_name": "text_encoders",
              "vae_name": "vae", "lora_name": "loras", "ckpt_name": "checkpoints"}

    def ancestry(link, visited=()):
        if not is_graph_link(link, graph):
            return link
        key = link[0]
        if key in visited:
            raise ValueError("Model graph contains a cycle")
        node = graph[key]
        inputs = {k: ancestry(v, visited + (key,)) for k, v in sorted(node["inputs"].items())}
        versions = {}
        for field, category in fields.items():
            name = node["inputs"].get(field)
            if isinstance(name, str):
                path = folder_paths.get_full_path(category, name)
                if path:
                    stat = Path(path).stat()
                    versions[field] = [stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]
        return {"class": node["class_type"], "inputs": inputs, "files": versions, "output": link[1]}

    inputs = graph.get(str(node_id), {}).get("inputs", {})
    models = {k: ancestry(inputs.get(k)) for k in ("model", "clip", "video_vae", "audio_vae")}
    data = {"version": CACHE_VERSION, "source": tensor_hash(source), "anchor": tensor_hash(anchor[:1]),
            "settings": settings, "models": models}
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

