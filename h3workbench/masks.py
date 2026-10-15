"""Versioned mask corrections: point prompts are local to a shot's exact frames."""

import json
import math
from collections import defaultdict

import numpy as np
from PIL import Image
from scipy.ndimage import distance_transform_edt

from . import media, store


def validate_points(points, frames):
    if not isinstance(points, list) or len(points) > 256:
        raise ValueError("Use at most 256 correction points per shot")
    ids = set()
    for p in points:
        if not isinstance(p, dict) or set(p) != {"id", "frame", "x", "y", "label"}:
            raise ValueError("Invalid correction point")
        if (
            not isinstance(p["id"], str)
            or not 1 <= len(p["id"]) <= 80
            or p["id"] in ids
        ):
            raise ValueError("Point IDs must be unique")
        ids.add(p["id"])
        if type(p["frame"]) is not int or not 0 <= p["frame"] < frames:
            raise ValueError("Point frame is outside this shot")
        if type(p["label"]) is not int or p["label"] not in {0, 1}:
            raise ValueError("Point must include or exclude")
        for axis in ["x", "y"]:
            if (
                type(p[axis]) not in {int, float}
                or not math.isfinite(p[axis])
                or not 0 <= p[axis] <= 1
            ):
                raise ValueError("Point coordinates must be between 0 and 1")
    return points


def groups(points):
    result = defaultdict(list)
    for p in points:
        # IDs and order are editor conveniences; model inputs define a correction.
        result[p["frame"]].append((p["x"], p["y"], p["label"]))
    return {f: sorted(items) for f, items in result.items()}


def pending_frames(s):
    current = groups(s.get("mask_points", []))
    applied = groups(s.get("mask_applied_points", []))
    return sorted(
        f for f in current.keys() | applied.keys() if current.get(f) != applied.get(f)
    )


def read(asset, frames, dimensions):
    w, h = dimensions
    if not asset:
        return np.zeros((frames, h, w), dtype=bool)
    with np.load(store.path(asset), allow_pickle=False) as z:
        mask = z["mask"] > 0.5
    if mask.ndim != 3 or len(mask) != frames:
        raise ValueError("Saved mask does not match this shot's frame range")
    if mask.shape[1:] == (h, w):
        return mask
    return np.stack(
        [
            np.asarray(Image.fromarray(m).resize((w, h), Image.Resampling.NEAREST))
            for m in mask
        ]
    )


def coverage(mask):
    values = mask.sum(axis=(1, 2)).tolist()
    return {
        "coverage": values,
        "empty_frames": [i for i, n in enumerate(values) if n == 0],
    }


def preserve(s):
    if not s.get("mask"):
        return
    s.setdefault("mask_base", s["mask"])
    versions = s.setdefault("mask_versions", [])
    if not any(v["asset"] == s["mask"] for v in versions):
        versions.append(
            {
                "asset": s["mask"],
                "label": "Original mask" if not versions else "Saved mask",
                "points": s.get("mask_applied_points", []),
                "created": store.now(),
            }
        )


def install(s, result, label, points, current=True):
    preserve(s)
    s.setdefault("mask_base", s.get("mask"))
    versions = s.setdefault("mask_versions", [])
    if not any(v["asset"] == result["asset"] for v in versions):
        versions.append(
            {
                "asset": result["asset"],
                "label": label,
                "points": points,
                "created": store.now(),
                "job": result.get("job"),
                "coverage": result["coverage"],
                "empty_frames": result["empty_frames"],
            }
        )
    if current:
        s.update(
            mask=result["asset"],
            mask_coverage=result["coverage"],
            mask_empty_frames=result["empty_frames"],
            mask_approved=False,
            mask_applied_points=points,
        )
        s.setdefault("mask_base", result["asset"])


def select(s, asset, dimensions):
    preserve(s)
    version = next((v for v in s["mask_versions"] if v["asset"] == asset), None)
    if version is None:
        raise ValueError("Unknown mask version")
    info = coverage(read(asset, s["end"] - s["start"], dimensions))
    s.update(
        mask=asset,
        mask_points=version["points"],
        mask_applied_points=version["points"],
        mask_approved=False,
        mask_coverage=info["coverage"],
        mask_empty_frames=info["empty_frames"],
    )


def point_inputs(points, prior, dimensions):
    w, h = dimensions
    pos = [
        {"x": min(w - 1, round(p["x"] * w)), "y": min(h - 1, round(p["y"] * h))}
        for p in points
        if p["label"]
    ]
    neg = [
        {"x": min(w - 1, round(p["x"] * w)), "y": min(h - 1, round(p["y"] * h))}
        for p in points
        if not p["label"]
    ]
    derived = None
    if not pos:
        # Negative-only edits need a positive target anchor, not a fresh empty prediction.
        interior = prior.copy()
        yy, xx = np.ogrid[:h, :w]
        for p in neg:
            interior[
                (xx - p["x"]) ** 2 + (yy - p["y"]) ** 2 <= max(5, min(w, h) // 40) ** 2
            ] = False
        distance = distance_transform_edt(interior)
        if distance.max() < 1:
            raise ValueError(
                "Add an include point on this frame; there is no prior target mask"
            )
        y, x = np.unravel_index(distance.argmax(), distance.shape)
        derived = {"x": int(x), "y": int(y), "from": "prior_mask_interior"}
        pos.append({"x": int(x), "y": int(y)})
    return {
        "positive_coords": json.dumps(pos),
        "negative_coords": json.dumps(neg),
    }, derived


def graph(c, s, job, directory):
    """Point-only SAM detection; tracking resets at correction frames, never across cuts."""
    options = job["data"]["options"]
    nframes = s["end"] - s["start"]
    points = s.get("mask_points", [])
    grouped = defaultdict(list)
    for p in points:
        grouped[p["frame"]].append(p)
    chosen = [options["frame"]] if options["mode"] == "frame" else sorted(grouped)
    chosen = [f for f in chosen if grouped[f]]
    if not chosen:
        return {}, {}, {"segments": [], "derived": []}
    single = options["mode"] == "frame"
    if single:
        source = directory / "frame.png"
        w, h = c["dimensions"]
        media.ff(
            [
                "-i",
                store.path(c.get("render_source") or c["source"]),
                "-vf",
                f"select=eq(n\\,{s['start'] + chosen[0]}),scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1",
                "-frames:v",
                "1",
                source,
            ]
        )
    else:
        source = media.shot_media(c, s, directory)
    jid = job["id"]
    files = {f"{jid}-source.mkv": source}
    if s.get("mask"):
        files[f"{jid}-prior.npz"] = store.path(s["mask"])

    def node(t, **inputs):
        return {"class_type": t, "inputs": inputs}

    g = {
        "30": node("LoadVideo", file=f"{jid}-source.mkv"),
        "31": node(
            "H3CharacterSwapPrepareVideo",
            resolution="768",
            duration_seconds=0,
            video=["30", 0],
        ),
        "33": node(
            "CheckpointLoaderSimple", ckpt_name="sam3.1_multiplex_fp16.safetensors"
        ),
    }
    if single:
        files.pop(f"{jid}-source.mkv")
        files[f"{jid}-frame.png"] = source
        g.pop("30"); g.pop("31")
        g["31"] = node("LoadImage", image=f"{jid}-frame.png")
    prior = read(s.get("mask"), nframes, c["dimensions"])
    segments = []
    derived = []
    for i, f in enumerate(chosen):
        image_id, detect_id = str(100 + i * 10), str(101 + i * 10)
        g[image_id] = node("ImageFromBatch", image=["31", 0], batch_index=0 if single else f, length=1)
        coords, anchor = point_inputs(grouped[f], prior[f], c["dimensions"])
        if anchor:
            derived.append({"frame": f, **anchor})
        # Do not add text conditioning: the node unions text detections with point masks,
        # which would undo exclusion clicks.
        g[detect_id] = node(
            "H3CharacterSwapRefineMask",
            model=["33", 0],
            image=[image_id, 0],
            prior_file=f"{jid}-prior.npz" if s.get("mask") else "",
            frame=f,
            **coords,
        )
        prefix = f"{jid}-edit-{f:04}"
        if options["mode"] == "frame":
            g[str(102 + i * 10)] = node(
                "H3CharacterSwapSaveMask", masks=[detect_id, 0], filename_prefix=prefix, frames=1
            )
            segments.append({"file": prefix + ".npz", "start": f, "end": f + 1})
            continue
        end = chosen[i + 1] if i + 1 < len(chosen) else nframes
        batch_id, track_id, mask_id, save_id = [str(102 + i * 10 + k) for k in range(4)]
        g[batch_id] = node(
            "ImageFromBatch", image=["31", 0], batch_index=f, length=end - f
        )
        g[track_id] = node(
            "SAM3_VideoTrack",
            model=["33", 0],
            images=[batch_id, 0],
            initial_mask=[detect_id, 0],
            detection_threshold=0.5,
            max_objects=1,
            detect_interval=1,
        )
        g[mask_id] = node(
            "SAM3_TrackToMask", track_data=[track_id, 0], object_indices=""
        )
        g[save_id] = node(
            "H3CharacterSwapSaveMask", masks=[mask_id, 0], filename_prefix=prefix, frames=end - f
        )
        segments.append({"file": prefix + ".npz", "start": f, "end": end})
        if i == 0 and f:
            reverse = directory / "head-reverse.mkv"
            media.ff(
                [
                    "-i",
                    source,
                    "-vf",
                    f"trim=end_frame={f + 1},reverse,setpts=PTS-STARTPTS",
                    "-an",
                    "-c:v",
                    "ffv1",
                    reverse,
                ]
            )
            files[f"{jid}-head.mkv"] = reverse
            g["40"] = node("LoadVideo", file=f"{jid}-head.mkv")
            g["41"] = node(
                "H3CharacterSwapPrepareVideo",
                resolution="768",
                duration_seconds=0,
                video=["40", 0],
            )
            g["42"] = node(
                "SAM3_VideoTrack",
                model=["33", 0],
                images=["41", 0],
                initial_mask=[detect_id, 0],
                detection_threshold=0.5,
                max_objects=1,
                detect_interval=1,
            )
            g["44"] = node("SAM3_TrackToMask", track_data=["42", 0], object_indices="")
            g["45"] = node(
                "H3CharacterSwapSaveMask",
                masks=["44", 0],
                filename_prefix=f"{jid}-edit-head",
                frames=f + 1,
            )
            segments.append(
                {
                    "file": f"{jid}-edit-head.npz",
                    "start": 0,
                    "end": f,
                    "reverse": True,
                    "frames": f + 1,
                }
            )
    return g, files, {"segments": segments, "derived": derived}


def finish(c, s, job, directory, output, plan):
    nframes = s["end"] - s["start"]
    options = job["data"]["options"]
    base_asset = s.get("mask_base", s.get("mask"))
    mask = read(s.get("mask"), nframes, c["dimensions"])
    if not plan["segments"]:
        base = read(base_asset, nframes, c["dimensions"])
        if options["mode"] == "frame":
            mask[options["frame"]] = base[options["frame"]]
        else:
            mask = base
    for part in plan["segments"]:
        with np.load(output / "output" / part["file"], allow_pickle=False) as z:
            values = z["mask"] > 0.5
        count = part.get("frames", part["end"] - part["start"])
        if values.shape != (count, c["dimensions"][1], c["dimensions"][0]):
            raise RuntimeError("Edited mask shape does not match the shot")
        if part.get("reverse"):
            values = values[::-1][:-1]
        mask[part["start"] : part["end"]] = values
    result_file = directory / "edited-mask.npz"
    np.savez_compressed(result_file, mask=mask)
    points = s.get("mask_points", [])
    if options["mode"] == "frame":
        points = [
            p
            for p in s.get("mask_applied_points", [])
            if p["frame"] != options["frame"]
        ] + [p for p in points if p["frame"] == options["frame"]]
    proof = {
        "source": c.get("render_source") or c["source"],
        "shot_range": [s["start"], s["end"]],
        "base_mask": s.get("mask"),
        "points": s.get("mask_points", []),
        "applied_points": points,
        "options": options,
        **plan,
    }
    (directory / "mask-edit-proof.json").write_text(json.dumps(proof, indent=2))
    return {
        "asset": store.asset_add(result_file),
        "job": job["id"],
        "points": points,
        "proof": store.asset_add(directory / "mask-edit-proof.json"),
        **coverage(mask),
    }


def merge_frame(before, current, result, frame, frames, dimensions, directory):
    """Merge one immutable correction into the latest mask, preserving other frames."""
    if groups(current.get("mask_points", [])).get(frame) != groups(
        before.get("mask_points", [])
    ).get(frame):
        return None
    if current.get("mask_base") != before.get("mask_base"):
        return None
    old = read(before.get("mask"), frames, dimensions)
    latest = read(current.get("mask"), frames, dimensions)
    if not np.array_equal(old[frame], latest[frame]):
        return None
    patch = read(result["asset"], frames, dimensions)
    latest[frame] = patch[frame]
    target = directory / "merged-mask.npz"
    np.savez_compressed(target, mask=latest)
    points = [
        p for p in current.get("mask_applied_points", []) if p["frame"] != frame
    ] + [p for p in before.get("mask_points", []) if p["frame"] == frame]
    proof = directory / "frame-merge-proof.json"
    proof.write_text(
        json.dumps(
            {
                "frame": frame,
                "snapshot_mask": before.get("mask"),
                "merge_base": current.get("mask"),
                "frame_result": result["asset"],
                "frame_proof": result.get("proof"),
                "applied_points": points,
            },
            indent=2,
        )
    )
    return {
        **result,
        "asset": store.asset_add(target),
        "points": points,
        "proof": store.asset_add(proof),
        **coverage(latest),
    }
