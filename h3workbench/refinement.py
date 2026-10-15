"""Optional, revisioned source/character pairs on one exact backplate timeline."""

import copy
from functools import lru_cache
import hashlib
import json
import math
import os
import shutil
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from scipy.ndimage import binary_fill_holes, distance_transform_edt, label

from . import masks, media, store
from .config import DATA

CATALOG = Path(os.environ.get("H3_WORKBENCH_LEGACY_CATALOG", DATA / "legacy-imports/catalog.json"))
SIDES = ("source", "character")
KINDS = {
    "refine_plate",
    "refine_extract",
    "refine_mask",
    "refine_face",
    "refine_compose",
    "backplate_anchor",
    "backplate",
}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def binding(c, s):
    r = next((r for r in s["renders"] if r["id"] == s.get("selected_render")), None)
    return {
        "source": c.get("render_source") or c["source"],
        "start": s["start"],
        "end": s["end"],
        "dimensions": c["dimensions"],
        "render_id": r["id"] if r else None,
        "render_asset": r["asset"] if r else None,
    }


def source_binding(c, s):
    return {k: v for k, v in binding(c, s).items() if not k.startswith("render_")}


def defaults():
    value = store.setting("refine_defaults", {"character_padding_px": 8})
    return {**value, "revision": digest(value)}


def set_defaults(body):
    if set(body) != {"revision", "character_padding_px"}:
        raise ValueError("Choose character SAM padding")
    finite(body["character_padding_px"], 0, 64, "SAM padding")
    with store.LOCK:
        if body["revision"] != defaults()["revision"]:
            raise store.Conflict("Foreground defaults changed elsewhere. Reload before saving.")
        store.set_setting("refine_defaults", {"character_padding_px": body["character_padding_px"]})
        return defaults()


def foreground(mask=None):
    return {
        "mask": mask,
        "mask_base": mask,
        "mask_points": [],
        "mask_applied_points": [],
        "alpha": None,
        "alpha_seed": 1234,
        "auto_face": True,
        "mask_text": "person",
        "selection": "largest",
        "subject_index": 0,
        "gate_px": 3,
        "core_px": 2,
        "reviewed": False,
        "face": {"enabled": False, "keyframes": [], "feather_px": 4, "guard_px": 3},
    }


def draft(c, s):
    old = copy.deepcopy(s.get("refine", {}))
    key = binding(c, s)
    if old.get("binding") == key:
        for side in SIDES:
            old[side] = {**foreground(), **old[side]}
        return old
    result = {
        "binding": key,
        "plate_id": None,
        "plate_reviewed": False,
        "source": foreground(s.get("mask")),
        "character": foreground(),
        "anchor": None,
        "anchor_reviewed": False,
        "removal_seed": 904234,
        "removal_steps": 12,
        "removal_strength": 2.0,
    }
    result["character"]["gate_px"] = defaults()["character_padding_px"]
    if {
        k: v for k, v in old.get("binding", {}).items() if not k.startswith("render_")
    } == source_binding(c, s):
        for k in [
            "source",
            "plate_id",
            "plate_reviewed",
            "anchor",
            "anchor_reviewed",
            "removal_seed",
            "removal_steps",
            "removal_strength",
        ]:
            if k in old:
                result[k] = old[k]
    return result


def finite(value, lo, hi, name):
    if (
        type(value) not in {int, float}
        or not math.isfinite(value)
        or not lo <= value <= hi
    ):
        raise ValueError(f"Invalid {name}")


def validate_face(face, frames):
    if not isinstance(face, dict) or set(face) != {
        "enabled",
        "keyframes",
        "feather_px",
        "guard_px",
    }:
        raise ValueError("Invalid face protection settings")
    if (
        type(face["enabled"]) is not bool
        or not isinstance(face["keyframes"], list)
        or len(face["keyframes"]) > max(128, frames)
    ):
        raise ValueError("Invalid face keyframes")
    finite(face["feather_px"], 0, 64, "face feather")
    finite(face["guard_px"], 0, 64, "face boundary guard")
    previous, vertices = -1, None
    for item in face["keyframes"]:
        if not isinstance(item, dict) or set(item) != {"frame", "polygon"}:
            raise ValueError("Invalid face keyframe")
        if type(item["frame"]) is not int or not previous < item["frame"] < frames:
            raise ValueError("Face keyframes must be ordered within this shot")
        previous = item["frame"]
        poly = item["polygon"]
        if (
            not isinstance(poly, list)
            or not 3 <= len(poly) <= 32
            or (vertices is not None and len(poly) != vertices)
        ):
            raise ValueError("Face keyframes must have the same 3–32 vertices")
        vertices = len(poly)
        for point in poly:
            if not isinstance(point, list) or len(point) != 2:
                raise ValueError("Invalid face polygon")
            for value in point:
                finite(value, 0, 1, "face coordinate")
        area = (
            abs(
                sum(
                    poly[i][0] * poly[(i + 1) % len(poly)][1]
                    - poly[(i + 1) % len(poly)][0] * poly[i][1]
                    for i in range(len(poly))
                )
            )
            / 2
        )
        if area < 0.00001:
            raise ValueError("Face protection region has no area")
    if face["enabled"] and not face["keyframes"]:
        raise ValueError("Draw a face region before enabling protection")


def settings_key(f):
    return digest(
        {
            k: f.get(k)
            for k in [
                "mask",
                "alpha",
                "mask_points",
                "mask_applied_points",
                "gate_px",
                "core_px",
                "face",
            ]
        }
    )


def patch(c, s, changes):
    r = draft(c, s)
    allowed = {
        "plate_id",
        "plate_reviewed",
        "source",
        "character",
        "anchor",
        "anchor_reviewed",
        "removal_seed",
        "removal_steps",
        "removal_strength",
    }
    if not isinstance(changes, dict) or not set(changes) <= allowed:
        raise ValueError("Unknown refinement field")
    for side in SIDES:
        if side not in changes:
            continue
        edits = changes[side]
        if not isinstance(edits, dict) or not set(edits) <= {
            "mask_points",
            "face",
            "gate_px",
            "core_px",
            "reviewed",
            "mask_text",
            "selection",
            "subject_index",
            "alpha_seed",
            "auto_face",
        }:
            raise ValueError("Unknown foreground field")
        if "reviewed" in edits and type(edits["reviewed"]) is not bool:
            raise ValueError("Reviewed must be true or false")
        if "alpha_seed" in edits and (type(edits["alpha_seed"]) is not int or not 0 <= edits["alpha_seed"] <= 2**53-1):
            raise ValueError("Seed must be a whole number from 0 to 9007199254740991")
        if "auto_face" in edits and type(edits["auto_face"]) is not bool:
            raise ValueError("Automatic face protection must be true or false")
        f = r[side]
        before = settings_key(f)
        if "mask_points" in edits:
            masks.validate_points(edits["mask_points"], s["end"] - s["start"])
        if "face" in edits:
            validate_face(edits["face"], s["end"] - s["start"])
        for k in ["gate_px", "core_px"]:
            if k in edits:
                finite(edits[k], 0, 64, k)
        if "mask_text" in edits and (
            not isinstance(edits["mask_text"], str) or len(edits["mask_text"]) > 200
        ):
            raise ValueError("Target description is too long")
        if "selection" in edits and edits["selection"] not in {
            "largest",
            "leftmost",
            "rightmost",
            "index",
        }:
            raise ValueError("Unknown subject selection")
        if "subject_index" in edits and (
            type(edits["subject_index"]) is not int
            or not 0 <= edits["subject_index"] <= 100
        ):
            raise ValueError("Invalid subject index")
        targeting_changed = any(
            k in edits and edits[k] != f.get(k)
            for k in ["mask_text", "selection", "subject_index"]
        )
        f.update(copy.deepcopy(edits))
        if targeting_changed:
            f.update(mask=None, mask_base=None, mask_applied_points=[], reviewed=False)
        if settings_key(f) != before:
            f["reviewed"] = False
        if edits.get("reviewed"):
            if not f.get("alpha") or not f.get("mask") or masks.pending_frames(f):
                raise ValueError(
                    "Extract and apply foreground corrections before reviewing"
                )
            if f.get("empty_frames"):
                raise ValueError(
                    "Foreground has empty frames; correct it before reviewing"
                )
            f["reviewed"] = True
            f["review_key"] = settings_key(f)
        elif "reviewed" in edits and type(edits["reviewed"]) is not bool:
            raise ValueError("Reviewed must be true or false")
    for k in allowed - set(SIDES):
        if k in changes:
            r[k] = changes[k]
    if "plate_id" in changes:
        plate = next(
            (p for p in s.get("backplates", []) if p["id"] == r["plate_id"]), None
        )
        if not plate or plate["binding"] != source_binding(c, s):
            raise ValueError("Choose a backplate belonging to this source shot")
        if r["plate_id"] != draft(c, s).get("plate_id"):
            r["plate_reviewed"] = False
    for k in ["plate_reviewed", "anchor_reviewed"]:
        if type(r[k]) is not bool:
            raise ValueError("Reviewed must be true or false")
    if r["plate_reviewed"] and not r["plate_id"]:
        raise ValueError("Choose a backplate first")
    if "anchor" in changes and r["anchor"] not in [
        x["asset"] for x in s.get("backplate_anchors", [])
    ]:
        raise ValueError("Unknown clean anchor")
    if "anchor" in changes and r["anchor"] != draft(c, s).get("anchor"):
        r["anchor_reviewed"] = False
    if r["anchor_reviewed"] and not r["anchor"]:
        raise ValueError("Create or upload a clean anchor first")
    for name, lo, hi in [("removal_seed", 0, 2**63 - 1), ("removal_steps", 1, 50)]:
        if type(r[name]) is not int or not lo <= r[name] <= hi:
            raise ValueError(f"Invalid {name}")
    finite(r["removal_strength"], 0, 3, "removal strength")
    s["refine"] = r


def selected(c, s):
    id = s.get("selected_finish")
    if not id:
        return None
    finish = next((f for f in s.get("finishes", []) if f["id"] == id), None)
    if not finish or finish["binding"] != binding(c, s):
        raise ValueError(
            "Selected refinement is stale. Review this shot or choose Use raw render."
        )
    return finish


def catalog(c):
    if not CATALOG.exists() or not c.get("original"):
        return []
    source_sha = store.asset(c["original"])["sha256"]
    entries = json.loads(CATALOG.read_text())["clips"]
    rows = []
    for item in entries:
        if item["source_sha256"] != source_sha:
            continue
        for variant, v in item["variants"].items():
            p = Path(v["local_master"])
            if not p.is_file():
                continue
            rows.append(
                {
                    "id": item["key"] + ":" + variant,
                    "variant": variant,
                    "source_sha256": source_sha,
                    "path": str(p),
                    "label": f"{item['id']} · {variant}",
                    "prior_review": v.get("human_review"),
                    "cuts": v.get("cuts", []),
                }
            )
    return rows


def plate_record(c, s, asset, directory, origin, offset):
    info = media.probe(store.path(asset))
    count = s["end"] - s["start"]
    if abs(info["fps"] - 24) > 0.001:
        raise ValueError("Backplate must use the exact 24 fps source timeline")
    if info["frames"] < offset + count:
        raise ValueError(
            f"Backplate has {info['frames']} frames; this range needs through frame {offset + count - 1}. Repair missing frames first."
        )
    if (
        abs(info["width"] / info["height"] - c["dimensions"][0] / c["dimensions"][1])
        > 0.025
    ):
        raise ValueError(
            "Backplate aspect ratio does not match the shot; align its framing before import"
        )
    out = directory / "backplate.mkv"
    w, h = c["dimensions"]
    media.ff(
        [
            "-i",
            store.path(asset),
            "-vf",
            f"trim=start_frame={offset}:end_frame={offset + count},setpts=PTS-STARTPTS,scale={w}:{h}:flags=lanczos,setsar=1",
            "-an",
            "-c:v",
            "ffv1",
            "-r",
            "24",
            "-pix_fmt",
            "bgr0",
            out,
        ]
    )
    if media.probe(out)["frames"] != count:
        raise ValueError("Backplate frame count changed during alignment")
    return {
        "id": store.uid("plate-"),
        "asset": store.asset_add(out),
        "original_asset": asset,
        "binding": source_binding(c, s),
        "origin": origin,
        "offset": offset,
        "frames": count,
        "dimensions": c["dimensions"],
        "fps": 24,
        "created": store.now(),
    }


def import_catalog(c, s, candidate, d):
    rec = next((p for p in catalog(c) if p["id"] == candidate), None)
    if rec is None:
        raise ValueError("No matching source backplate")
    path = Path(rec["path"])
    # Historical masters may be on a worker archive outside this app's asset roots.
    # A verified copy keeps serving and job inputs inside this app's shared storage.
    target = d / ("original" + path.suffix)
    before = store.sha(path)
    shutil.copy2(path, target)
    if store.sha(target) != before or store.sha(path) != before:
        raise ValueError("Backplate changed during import")
    return plate_record(c, s, store.asset_add(target), d, rec, s["start"])


def polygon_at(face, frame):
    keys = face["keyframes"]
    if not keys:
        return []
    a = next((k for k in reversed(keys) if k["frame"] <= frame), keys[0])
    b = next((k for k in keys if k["frame"] >= frame), keys[-1])
    t = (
        (frame - a["frame"]) / (b["frame"] - a["frame"])
        if a["frame"] != b["frame"]
        else 0
    )
    return (np.asarray(a["polygon"]) * (1 - t) + np.asarray(b["polygon"]) * t).tolist()


def protect_face(alpha, face, frame):
    if not face["enabled"]:
        return alpha
    h, w = alpha.shape
    roi = Image.new("L", (w, h))
    ImageDraw.Draw(roi).polygon(
        [(round(x * (w - 1)), round(y * (h - 1))) for x, y in polygon_at(face, frame)],
        fill=255,
    )
    feather, guard_width = face["feather_px"], face["guard_px"]
    # Only the polygon and its feather can change opacity. Keep global component
    # and hole topology, but avoid distance fields over the empty image outside it.
    x0, y0, x1, y1 = roi.getbbox()
    margin = int(np.ceil(feather)) + 1
    x0, y0 = max(0, x0-margin), max(0, y0-margin)
    x1, y1 = min(w, x1+margin), min(h, y1+margin)
    area = np.s_[y0:y1, x0:x1]
    inside = np.asarray(roi)[area] > 0
    region = np.where(
        inside,
        1.0,
        np.clip(1 - distance_transform_edt(~inside) / max(feather, 0.001), 0, 1),
    )
    components, count = label(alpha >= 16 / 255)
    if not count:
        return alpha
    sizes = np.bincount(components.ravel())
    sizes[0] = 0
    foreground = components == sizes.argmax()
    holes, count = label(binary_fill_holes(foreground) & ~foreground)
    safe = distance_transform_edt(inside) >= max(1, guard_width * 2)
    # Reject a whole hole if any of its pixels fall outside the safe face core.
    # Compare whole-hole counts with safe-interior counts without per-hole scans.
    hole_sizes = np.bincount(holes.ravel(), minlength=count+1)
    safe_sizes = np.bincount(holes[area][safe], minlength=count+1)
    allowed = hole_sizes == safe_sizes
    allowed[0] = False
    accepted = allowed[holes]
    silhouette = foreground | accepted
    depth = distance_transform_edt(silhouette)
    guard = np.clip((depth[area] - guard_width) / max(guard_width, 1), 0, 1) * silhouette[area]
    result = alpha.astype(np.float32, copy=True)
    result[area] = np.maximum(alpha[area], np.minimum(region, guard))
    return result


def sam_support(sam, padding):
    sam = sam.astype(bool)
    return distance_transform_edt(~sam) <= padding if sam.any() else np.zeros_like(sam)


def matte_frame(raw, sam, f, frame):
    sam = sam.astype(bool)
    if not sam.any():
        return np.zeros_like(raw, dtype=np.float32)
    # Everything outside padded SAM is zero. Include a zero border where the
    # image has one so Euclidean distances are identical to the full-frame EDT.
    ys, xs = np.flatnonzero(sam.any(axis=1)), np.flatnonzero(sam.any(axis=0))
    margin = int(np.ceil(f["gate_px"])) + 1
    area = np.s_[max(0,ys[0]-margin):min(sam.shape[0],ys[-1]+margin+1),
                 max(0,xs[0]-margin):min(sam.shape[1],xs[-1]+margin+1)]
    local = sam[area]
    distance = distance_transform_edt(local)
    core = np.clip((distance - 0.5) / max(f["core_px"] - 0.5, 0.5), 0, 1)
    support = sam_support(local, f["gate_px"])
    alpha = np.zeros_like(raw, dtype=np.float64)
    alpha[area] = np.maximum(np.clip(raw[area].astype(np.float32), 0, 1), core) * support
    alpha = protect_face(alpha, f["face"], frame)
    alpha[alpha < 1 / 255] = 0
    alpha[alpha > 254 / 255] = 1
    return alpha


def raw_alpha(asset, frames, dimensions):
    return _raw_alpha(asset, frames, tuple(dimensions))


@lru_cache(maxsize=2)
def _raw_alpha(asset, frames, dimensions):
    with np.load(store.path(asset), allow_pickle=False) as z:
        a = z["alpha"]
    if a.ndim != 3 or len(a) != frames:
        raise ValueError("Alpha does not match this shot")
    if np.issubdtype(a.dtype, np.integer):
        a = a.astype(np.float32) / 255
    if not np.isfinite(a).all() or a.min() < 0 or a.max() > 1:
        raise ValueError("Invalid alpha values")
    w, h = dimensions
    if a.shape[1:] != (h, w):
        a = np.stack(
            [
                np.asarray(
                    Image.fromarray(x.astype(np.float32)).resize(
                        (w, h), Image.Resampling.BILINEAR
                    )
                )
                for x in a
            ]
        )
    return a


def decode(asset, start, end, dimensions):
    w, h = dimensions
    data = subprocess.check_output(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(store.path(asset)),
            "-vf",
            f"trim=start_frame={start}:end_frame={end},setpts=PTS-STARTPTS,scale={w}:{h}:flags=lanczos",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "pipe:1",
        ]
    )
    if len(data) != (end - start) * w * h * 3:
        raise ValueError("Video does not cover the exact shot range")
    return np.frombuffer(data, np.uint8).reshape(end - start, h, w, 3)


def encode(frames, path):
    _n, h, w, _ = frames.shape
    p = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-nostdin",
            "-y",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-s",
            f"{w}x{h}",
            "-r",
            "24",
            "-i",
            "pipe:0",
            "-an",
            "-c:v",
            "ffv1",
            "-r",
            "24",
            "-pix_fmt",
            "bgr0",
            str(path),
        ],
        check=False,
        input=frames.tobytes(),
        capture_output=True,
    )
    if p.returncode:
        raise RuntimeError(p.stderr.decode()[-2000:])


def compose(c, s, job, d, progress=None):
    report = progress or (lambda *args, **kwargs: None)
    r = draft(c, s)
    validate_job("refine_compose", c, s, {})
    plate = next(p for p in s["backplates"] if p["id"] == r["plate_id"])
    count = s["end"] - s["start"]
    dims = c["dimensions"]
    report("Loading shared background")
    bg = decode(plate["asset"], 0, count, dims)
    alphas = {}
    outputs = {}
    proof = {}
    for side in SIDES:
        report(f"Loading {side} foreground")
        f = r[side]
        rgb = decode(
            r["binding"]["source"]
            if side == "source"
            else r["binding"]["render_asset"],
            s["start"] if side == "source" else 0,
            s["end"] if side == "source" else count,
            dims,
        )
        raw = raw_alpha(f["alpha"], count, dims)
        sam = masks.read(f["mask"], count, dims)
        matte_frames = []
        for i, (a, m) in enumerate(zip(raw, sam)):
            matte_frames.append(matte_frame(a, m, f, i))
            if (i + 1) % 12 == 0 or i + 1 == count:
                report(f"Compositing {side}", (0 if side == "source" else count) + i + 1, count * 2)
        alpha = np.stack(matte_frames)
        if np.any(alpha.sum((1, 2)) == 0):
            raise ValueError(f"{side} foreground has an empty frame")
        result = np.rint(rgb * alpha[..., None] + bg * (1 - alpha[..., None])).astype(
            np.uint8
        )
        path = d / (side + ".mkv")
        report(f"Encoding {side} lossless master")
        encode(result, path)
        asset = store.asset_add(path)
        report(f"Verifying {side} pixels")
        if not np.array_equal(decode(asset, 0, count, dims), result):
            raise ValueError("Lossless composition verification failed")
        inside = alpha == 1
        outside = alpha == 0
        if not np.array_equal(result[inside], rgb[inside]) or not np.array_equal(
            result[outside], bg[outside]
        ):
            raise ValueError(
                "Composition did not preserve foreground/background pixels"
            )
        alpha_path = d / (side + "-alpha.npz")
        np.savez_compressed(alpha_path, alpha=alpha)
        proof[side] = {
            "opaque_core_rgb_exact": True,
            "outside_matte_equals_plate": True,
        }
        outputs[side] = asset
        alphas[side] = alpha
    report("Verifying shared background")
    outside = (alphas["source"] == 0) & (alphas["character"] == 0)
    a = decode(outputs["source"], 0, count, dims)
    b = decode(outputs["character"], 0, count, dims)
    changed = int(np.any(a[outside] != b[outside], axis=-1).sum())
    if changed:
        raise ValueError("Pair backgrounds are not identical outside the matte union")
    receipt = {
        "frames": count,
        "fps": 24,
        "dimensions": dims,
        "same_plate": plate["asset"],
        "outside_union_changed_pixels": changed,
        "outside_union_pixels": int(outside.sum()),
        "foreground": proof,
        "lossless_roundtrips_verified": True,
        "note": "Pixel equality applies to lossless masters; preview MP4 compression is lossy.",
    }
    (d / "verification.json").write_text(json.dumps(receipt, indent=2))
    result = {
        "id": job["id"],
        "binding": binding(c, s),
        "recipe": r,
        "plate": plate,
        "source": outputs["source"],
        "character": outputs["character"],
        "source_alpha": store.asset_add(d / "source-alpha.npz"),
        "character_alpha": store.asset_add(d / "character-alpha.npz"),
        "verification": store.asset_add(d / "verification.json"),
        "created": store.now(),
        "label": "Shared backplate",
        "reviewed": False,
    }
    ids = {
        r["binding"]["source"],
        r["binding"]["render_asset"],
        plate["asset"],
        *outputs.values(),
        result["source_alpha"],
        result["character_alpha"],
    }
    for side in SIDES:
        for name in ["alpha", "mask", "alpha_proof", "mask_proof"]:
            if r[side].get(name):
                ids.add(r[side][name])
    provenance = {
        "finish": result,
        "assets": {id: store.asset(id) for id in ids},
        "verification": receipt,
        "source_audio_asset": store.asset(c["source"]),
        "audio_policy": "Original source audio is remuxed at assembly, with no offset or retiming.",
    }
    (d / "provenance.json").write_text(json.dumps(provenance, indent=2))
    result["provenance"] = store.asset_add(d / "provenance.json")
    return result


def validate_job(kind, c, s, options):
    if kind not in KINDS or not s or s["mode"] != "generate":
        raise ValueError("Choose a generated shot")
    r = draft(c, s)
    if kind in {"refine_extract", "refine_mask", "refine_face"}:
        if not isinstance(options, dict) or options.get("side") not in SIDES:
            raise ValueError("Choose Source actor or Rendered character")
        if not set(options) <= {"side", "mode", "frame", "offload_after"}:
            raise ValueError("Unknown foreground job option")
        if options["side"] == "character" and not r["binding"]["render_asset"]:
            raise ValueError("Select a character render first")
        if kind == "refine_extract" and masks.pending_frames(r[options["side"]]):
            raise ValueError("Apply the SAM point corrections before extracting alpha")
        if kind == "refine_face":
            if not r[options["side"]].get("mask"):
                raise ValueError("Extract a foreground before tracking its face")
            if "frame" in options and (type(options["frame"]) is not int or not 0 <= options["frame"] < s["end"]-s["start"]):
                raise ValueError("Choose a valid correction frame")
        if kind == "refine_mask":
            if (
                options.get("mode") not in {"frame", "shot"}
                or type(options.get("frame")) is not int
                or not 0 <= options["frame"] < s["end"] - s["start"]
            ):
                raise ValueError("Choose a valid frame or shot correction")
            if not r[options["side"]]["mask_points"] and not r[options["side"]]["mask"]:
                raise ValueError("Add correction points first")
    elif kind == "refine_plate":
        if set(options) != {"candidate"} or not any(
            p["id"] == options["candidate"] for p in catalog(c)
        ):
            raise ValueError("Choose a matching backplate")
    elif options and not (kind == "backplate" and set(options) == {"offload_after"}):
        raise ValueError("This stage has no extra options")
    if options.get("offload_after"):
        validate_offload(kind, c, s, options)
    if kind == "backplate" and (
        not r["anchor"]
        or not s.get("mask")
        or masks.pending_frames(s)
        or s.get("mask_empty_frames")
    ):
        raise ValueError("Select a clean opening anchor and complete the source person mask first")
    if kind == "refine_compose":
        if (
            not r["binding"]["render_asset"]
            or not r["plate_id"]
        ):
            raise ValueError("Select a render and backplate first")
        if not any(
            p["id"] == r["plate_id"] and p["binding"] == source_binding(c, s)
            for p in s.get("backplates", [])
        ):
            raise ValueError("Backplate is stale for this shot")
        for side in SIDES:
            f = r[side]
            if (
                not f.get("alpha")
                or not f.get("mask")
                or f.get("empty_frames")
                or masks.pending_frames(f)
            ):
                raise ValueError(f"Complete the {side} foreground and apply corrections before composing")
    return r


def validate_view(s, r, side, view):
    """Reject unavailable views before decoding or scheduling preview work."""
    if side not in SIDES or view not in {"rgb", "sam", "alpha", "checker", "black", "white", "plate", "comparison"}:
        raise ValueError("Unknown foreground view")
    if side == "character" and not r["binding"]["render_asset"]:
        raise ValueError("Select a render first")
    f = r[side]
    if view not in {"rgb", "sam"} and not f["alpha"]:
        raise ValueError("AlphaGen extraction is needed for this view. A SAM mask or face track alone does not provide an alpha matte.")
    if view != "rgb" and not f["mask"]:
        raise ValueError("Create a SAM support mask first")
    if view == "plate" and not any(p["id"] == r["plate_id"] for p in s.get("backplates", [])):
        raise ValueError("Select a backplate first")


def frame_image(c, s, side, frame, view):
    if side not in SIDES or view not in {
        "rgb",
        "alpha",
        "sam",
        "checker",
        "black",
        "white",
        "plate",
    }:
        raise ValueError("Unknown foreground view")
    count = s["end"] - s["start"]
    if type(frame) is not int or not 0 <= frame < count:
        raise ValueError("Frame is outside the shot")
    r = draft(c, s)
    validate_view(s, r, side, view)
    f = r[side]
    dims = c["dimensions"]
    asset = r["binding"]["source"] if side == "source" else r["binding"]["render_asset"]
    if not asset:
        raise ValueError("Select a render first")
    start = s["start"] + frame if side == "source" else frame
    rgb = decode(asset, start, start + 1, dims)[0]
    if view == "rgb":
        return Image.fromarray(rgb)
    sam = masks.read(f["mask"], count, dims)[frame]
    if view == "sam":
        return Image.fromarray(sam_support(sam, f["gate_px"]).astype(np.uint8) * 255)
    if not f["alpha"]:
        raise ValueError("Extract the foreground first")
    a = matte_frame(raw_alpha(f["alpha"], count, dims)[frame], sam, f, frame)
    if view == "alpha":
        return Image.fromarray(np.rint(a * 255).astype(np.uint8))
    h, w = a.shape
    if view == "plate":
        plate = next(
            (p for p in s.get("backplates", []) if p["id"] == r["plate_id"]), None
        )
        if not plate:
            raise ValueError("Select a backplate first")
        bg = decode(plate["asset"], frame, frame + 1, dims)[0]
    elif view == "checker":
        y, x = np.indices((h, w))
        bg = np.repeat((80 + ((x // 24 + y // 24) % 2) * 80)[..., None], 3, axis=2)
    else:
        bg = np.full_like(rgb, 255 if view == "white" else 0)
    return Image.fromarray(
        np.rint(rgb * a[..., None] + bg * (1 - a[..., None])).astype(np.uint8)
    )


def capacity_failure(job):
    runtime = job["data"].get("runtime")
    if not runtime or job["state"] != "failed":
        return None
    directory = Path(runtime)
    history = directory / "history.json"
    preflight = directory / "preflight.json"
    if not history.exists() or not preflight.exists():
        return None
    try:
        h = next(iter(json.loads(history.read_text()).values()))
        errors = [v for k, v in h["status"]["messages"] if k == "execution_error"]
        if not errors:
            return None
        error = errors[-1]
        if "OutOfMemoryError" not in error.get("exception_type", ""):
            return None
        measured = json.loads(preflight.read_text())
        placement = measured.get("stage_placement")
        full = placement == "full" if placement else "--highvram" in measured["args"]
        prior = (job["data"].get("options") or {}).get("offload_after")
        explicit_offload = placement == "offload" and (job["kind"] == "refine_extract" or
            (job["data"].get("options") or {}).get("model_offloading") is True)
        if not full and not explicit_offload and (not prior or not capacity_failure(store.job_get(prior))):
            return None
        return {
            "job": job["id"],
            "worker": job["data"]["worker"],
            "message": error["exception_message"],
            "stage": directory.parent.name,
            "full_resources_verified": True,
            "offloading": not full,
            "streaming": placement in {"stream", "layer-stream"} or "--lowvram" in measured["args"]
            or "--novram" in measured["args"],
            "layer_streaming": placement == "layer-stream" or "--novram" in measured["args"],
        }
    except (ValueError, KeyError, StopIteration):
        return None


def validate_offload(kind, c, s, options):
    previous = store.job_get(options["offload_after"])
    failure = capacity_failure(previous)
    if (
        not failure
        or previous["kind"] != kind
        or previous["clip_id"] != c["id"]
        or previous["shot_id"] != s["id"]
        or binding(previous["data"]["snapshot"], previous["data"]["shot"])
        != binding(c, s)
        or previous["data"].get("options", {}).get("side") != options.get("side")
    ):
        raise ValueError(
            "Offloading requires a saved full-resource memory failure for these exact inputs"
        )
    return failure
