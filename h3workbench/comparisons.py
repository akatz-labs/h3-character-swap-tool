"""Cached, shot-aligned two-column previews for the shared H3 Atlas player."""

import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor

from . import media, store, previews
from .config import MEDIA, check_storage

POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="h3-compare")
LOCK = threading.Lock()
PENDING = {}


def specification(c, s, left, right):
    choices = {"source": c.get("render_source") or c["source"]}
    choices.update({r["id"]: r["asset"] for r in s["renders"]})
    for f in s.get("finishes", []):
        choices["finish-source:" + f["id"]] = f["source"]
        choices["finish-character:" + f["id"]] = f["character"]
    for p in s.get("backplates", []):
        choices["plate:" + p["id"]] = p["asset"]
    if left not in choices or right not in choices:
        raise ValueError("Choose the source or a saved render from this shot")
    # A swapped pair shares the same file; only the canvas columns change.
    columns = sorted(set([left, right]))
    spec = {
        "version": 2,
        "columns": columns,
        "assets": [choices[x] for x in columns],
        "source": c.get("render_source") or c["source"],
        "start": s["start"],
        "end": s["end"],
        "dimensions": c["dimensions"],
        "audio": c["audio"],
        "fps": 24,
    }
    aligned = [
        r["id"]
        for r in s["renders"]
        if r.get("dimensions")
        and r["dimensions"] != c["dimensions"]
        and r["id"] in columns
    ]
    if aligned:
        spec["canvas_aligned"] = aligned
    return spec


def key_for(spec):
    return hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()


def prepare(c, s, left, right, retry=False):
    spec = specification(c, s, left, right)
    key = key_for(spec)
    directory = MEDIA / "comparisons" / key
    receipt = directory / "preview.json"
    with LOCK:
        if retry and PENDING.get(key, {}).get("state") == "failed":
            PENDING.pop(key)
        if receipt.exists():
            result = json.loads(receipt.read_text())
            if store.path(result["asset"]).is_file():
                return {"state": "ready", "key": key, **result}
        if key not in PENDING:
            PENDING[key] = {"state": "preparing", "key": key}
            POOL.submit(build, key, spec, directory)
        return dict(PENDING[key])


def build(key, spec, directory):
    def phase(message):
        with LOCK:
            PENDING[key] = {"state": "preparing", "key": key, "phase": message}

    try:
        check_storage(1)
        directory.mkdir(parents=True, exist_ok=True)
        frames = spec["end"] - spec["start"]
        width, height = previews.size(spec["dimensions"])
        phase("Preparing small source preview…")
        source = previews.proxy(
            spec["source"],
            spec["start"],
            spec["end"],
            spec["dimensions"],
            spec["audio"],
        )
        args, filters = [], []
        for i, (choice, asset) in enumerate(zip(spec["columns"], spec["assets"])):
            phase("Preparing small render preview…")
            path = (
                source
                if choice == "source"
                else previews.proxy(asset, 0, frames, spec["dimensions"], canvas_aligned=choice in spec.get("canvas_aligned", []))
            )
            args += ["-i", path]
        count = len(spec["columns"])
        filters.append("[0:v][1:v]hstack=inputs=2[v]" if count == 2 else "[0:v]null[v]")
        if spec["audio"]:
            audio_index = (
                spec["columns"].index("source")
                if "source" in spec["columns"]
                else count
            )
            if audio_index == count:
                args += ["-i", source]
        phase("Packing synchronized preview…")
        pending = directory / "preview.pending.mp4"
        args += ["-filter_complex", ";".join(filters), "-map", "[v]"]
        if spec["audio"]:
            args += ["-map", f"{audio_index}:a:0", "-c:a", "copy"]
        args += [
            "-frames:v",
            str(frames),
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "24",
            "-g",
            "12",
            "-keyint_min",
            "12",
            "-sc_threshold",
            "0",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            pending,
        ]
        media.ff(args)
        info = media.probe(pending)
        if info["frames"] != frames or abs(info["fps"] - 24) > 0.001:
            raise RuntimeError("Comparison preview frame validation failed")
        output = directory / "preview.mp4"
        pending.replace(output)
        result = {
            "asset": store.asset_add(output),
            "columns": spec["columns"],
            "frames": frames,
            "fps": 24,
            "width": width,
            "height": height,
            "source_start": spec["start"],
            "audio": info["audio"],
            "spec": spec,
            "bytes": output.stat().st_size,
        }
        receipt = directory / "preview.pending.json"
        receipt.write_text(json.dumps(result, indent=2))
        receipt.replace(directory / "preview.json")
        with LOCK:
            PENDING.pop(key, None)
    except Exception as error:
        with LOCK:
            PENDING[key] = {"state": "failed", "key": key, "error": str(error)}


def review_specification(c, layout):
    if not c.get("result"):
        raise ValueError("Assemble selected shots before previewing the final clip")
    if layout not in {"pair", "16x9", "fit"}:
        raise ValueError("Choose a final comparison preview format")
    source = c.get("source_pair") or c["source"]
    if layout == "pair":
        return dict(
            version=3,
            columns=["source", "result"],
            assets=[source, c["result"]],
            source=source,
            start=0,
            end=c["frames"],
            dimensions=c["dimensions"],
            audio=c["audio"],
            fps=24,
        )
    asset = c.get("comparison" if layout == "16x9" else "comparison_fit")
    if not asset:
        raise ValueError("Assemble the clip to create this comparison export")
    return dict(
        version=1,
        layout=layout,
        asset=asset,
        frames=c["frames"],
        audio=c["audio"],
        max_side=960,
    )


def prepare_review(c, layout, retry=False):
    spec = review_specification(c, layout)
    key = key_for(spec)
    directory = MEDIA / "comparisons" / key
    receipt = directory / "preview.json"
    with LOCK:
        if receipt.exists():
            result = json.loads(receipt.read_text())
            if store.path(result["asset"]).is_file():
                return {"state": "ready", "key": key, **result}
        if retry and PENDING.get(key, {}).get("state") == "failed":
            PENDING.pop(key)
        if key not in PENDING:
            PENDING[key] = {
                "state": "preparing",
                "key": key,
                "phase": "Preparing final clip preview…",
            }
            POOL.submit(
                build if layout == "pair" else build_review_export, key, spec, directory
            )
        return dict(PENDING[key])


def build_review_export(key, spec, directory):
    try:
        raw = json.loads(
            media.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_streams",
                    "-of",
                    "json",
                    store.path(spec["asset"]),
                ]
            )
        )
        stream = next(s for s in raw["streams"] if s["codec_type"] == "video")
        dimensions = [stream["width"], stream["height"]]
        output = previews.proxy(
            spec["asset"],
            0,
            spec["frames"],
            dimensions,
            spec["audio"],
            max_side=spec["max_side"],
        )
        width, height = previews.size(dimensions, spec["max_side"])
        result = {
            "asset": store.asset_add(output),
            "columns": ["report"],
            "frames": spec["frames"],
            "fps": 24,
            "width": width,
            "height": height,
            "source_start": 0,
            "audio": spec["audio"],
            "spec": spec,
            "bytes": output.stat().st_size,
        }
        directory.mkdir(parents=True, exist_ok=True)
        receipt = directory / "preview.pending.json"
        receipt.write_text(json.dumps(result, indent=2))
        receipt.replace(directory / "preview.json")
        with LOCK:
            PENDING.pop(key, None)
    except Exception as error:
        with LOCK:
            PENDING[key] = {"state": "failed", "key": key, "error": str(error)}
