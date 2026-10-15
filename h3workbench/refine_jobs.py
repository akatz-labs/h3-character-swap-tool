"""Queued refinement stages with immutable outputs and dependency-checked attachment."""

import copy
import hashlib
import json
import math

import numpy as np
from PIL import Image
from scipy.ndimage import maximum_filter

from . import anchors, masks, media, remote, store
from . import refinement as ref
from .config import ROOT


def virtual(c, s, side):
    r = ref.draft(c, s)
    f = r[side]
    vc, vs = copy.deepcopy(c), copy.deepcopy(s)
    vs.update(f)
    if side == "character":
        vc.update(
            source=r["binding"]["render_asset"],
            render_source=r["binding"]["render_asset"],
            audio=False,
            canvas_aligned=True,
        )
        vs.update(start=0, end=s["end"] - s["start"])
    return vc, vs


def alpha(c, s, job, d, side):
    count = s["end"] - s["start"]
    w, h = c["dimensions"]
    padded = max(17, math.ceil((count - 1) / 8) * 8 + 1)
    if padded > 145:
        raise ValueError(
            "Alpha extraction supports at most 145 frames per shot. Split the shot before extraction."
        )
    vc, vs = virtual(c, s, side)
    source = media.shot_media(vc, vs, d)
    pw, ph = math.ceil(w / 32) * 32, math.ceil(h / 32) * 32
    path = d / "alpha-input.mkv"
    padding_args = (
        [
            "-i",
            source,
            "-vf",
            f"pad={pw}:{ph}:0:0,tpad=stop_mode=clone:stop={padded - count}",
            "-frames:v",
            str(padded),
            "-an",
            "-c:v",
            "ffv1",
            "-pix_fmt",
            "bgr0",
            path,
        ]
    )
    media.frozen_file(path, {"source_sha256": store.sha(source), "frames": padded,
                           "dimensions": [pw, ph], "codec": "ffv1-bgr0-no-audio-v1"},
                      lambda: media.ff(padding_args))
    g = json.loads((ROOT / "templates/alpha.json").read_text())
    name = job["id"] + "-" + side + "-alpha.mkv"
    seed = ref.draft(c, s)[side]["alpha_seed"]
    for node in g.values():
        if "noise_seed" in node["inputs"]:
            node["inputs"]["noise_seed"] = seed
    g["7"]["inputs"]["file"] = name
    g["15"]["inputs"].update(width=pw, height=ph, length=padded)
    g["17"]["inputs"]["frames_number"] = padded
    out = remote.execute(
        job,
        g,
        {name: path},
        d,
        # Operator default after repeated full-residency OOMs on both workers.
        profile="alpha-offload",
    )
    hist = next(iter(json.loads((out / "history.json").read_text()).values()))
    images = hist["outputs"]["32"]["images"]
    if len(images) != padded:
        raise ValueError("Alpha output frame count mismatch")
    rows = []
    for meta in images[:count]:
        p = (out / "output" / meta.get("subfolder", "") / meta["filename"]).resolve()
        if not p.is_relative_to((out / "output").resolve()):
            raise ValueError("Invalid alpha output path")
        with Image.open(p) as im:
            a = np.asarray(im.convert("RGB"), np.float32).mean(axis=2) / 255
        if a.shape != (ph, pw):
            raise ValueError("Alpha output framing mismatch")
        rows.append(a[:h, :w])
    target = d / "alpha.npz"
    np.savez_compressed(target, alpha=np.stack(rows))
    proof = {
        "side": side,
        "source": vc.get("render_source") or vc["source"],
        "range": [vs["start"], vs["end"]],
        "dimensions": [w, h],
        "padded_dimensions": [pw, ph],
        "frames": count,
        "padded_frames": padded,
        "crop": [0, 0, w, h],
        "no_spatial_rescaling": True,
        "seed": seed,
        "runtime_profile": "alpha-offload",
        "api": store.asset_add(d / "api.json"),
    }
    (d / "alpha-proof.json").write_text(json.dumps(proof, indent=2))
    return {
        "alpha_seed": seed,
        "alpha": store.asset_add(target),
        "alpha_proof": store.asset_add(d / "alpha-proof.json"),
    }


def extract(c, s, job, d):
    side = job["data"]["options"]["side"]
    vc, vs = virtual(c, s, side)
    # Reuse either side's saved, corrected SAM mask; AlphaGen supplies opacity.
    foreground = ref.draft(c, s)[side]
    previous_id = job["data"]["options"].get("offload_after")
    previous_mask = (
        foreground["mask"]
        if not masks.pending_frames(foreground)
        else None
    )
    if previous_id and not previous_mask:
        from .config import MEDIA

        candidate = MEDIA / "jobs" / previous_id / "sam/runtime/output/person-mask.npz"
        release = candidate.parent.parent / "release.json"
        if (
            candidate.exists()
            and release.exists()
            and json.loads(release.read_text()).get("gpu_released")
        ):
            previous_mask = store.asset_add(candidate)
    if previous_mask:
        mask = previous_mask
    else:
        md = d / "sam"
        md.mkdir(exist_ok=True)
        g, files = remote.graph_mask(vc, vs, job, md)
        out = remote.execute(job, g, files, md, profile="full")
        mask = store.asset_add(out / "output/person-mask.npz")
    values = masks.read(mask, vs["end"] - vs["start"], vc["dimensions"])
    missing = masks.coverage(values)["empty_frames"]
    if missing and (not previous_mask or (previous_id and previous_mask != foreground.get("mask"))):
        rd = d / "sam-recovery"
        rd.mkdir(exist_ok=True)
        # Recovery references the exact staged source from this SAM pass. For a
        # memory-only retry that source retains the original job's staged name.
        recovery_job = copy.deepcopy(job)
        if previous_mask:
            recovery_job["id"] = previous_id
        g = remote.graph_mask_recovery(vs, recovery_job, missing)
        out = remote.execute(job, g, {}, rd, profile="full")
        for frame in missing:
            with np.load(
                out / "output" / f"recovered-{frame:04}.npz", allow_pickle=False
            ) as z:
                values[frame] = z["mask"][0] > 0.5
        recovered = d / "recovered-mask.npz"
        np.savez_compressed(recovered, mask=values)
        mask = store.asset_add(recovered)
    ad = d / "alpha"
    ad.mkdir(exist_ok=True)
    result = alpha(c, s, job, ad, side)
    if ref.draft(c, s)[side].get("auto_face", True):
        fd = d / "face"
        fd.mkdir(exist_ok=True)
        store.job_update(job["id"], phase="Tracking face protection")
        try:
            result.update(face_track(c, s, job, fd, side, mask, automatic=True))
        except remote.Cancelled:
            raise
        except Exception as error:
            receipt = fd / "runtime/release.json"
            if not receipt.exists() or not json.loads(receipt.read_text()).get("gpu_released"):
                raise
            # Keep a completed extraction available even if optional face tracking fails.
            result.update(face={"enabled":False,"keyframes":[],"feather_px":4,"guard_px":3},
                          face_track={"warning":str(error),"method":"automatic tracking failed", "uncertain_frames":list(range(s["end"]-s["start"]))})
    reused_current_mask = mask == foreground.get("mask")
    return {
        "side": side,
        "mask": mask,
        "mask_base": (foreground.get("mask_base") or mask) if reused_current_mask else mask,
        "mask_points": foreground["mask_points"] if reused_current_mask else [],
        "mask_applied_points": foreground["mask_applied_points"] if reused_current_mask else [],
        **masks.coverage(values),
        **result,
    }


def face_track(c, s, job, d, side, mask, automatic=False):
    vc, vs = virtual(c, s, side)
    source = media.shot_media(vc, vs, d)
    region = {}
    face = ref.draft(c, s)[side]["face"]
    if not automatic and face["keyframes"]:
        frame = job["data"]["options"].get("frame", 0)
        region = {"frame": frame, "polygon": ref.polygon_at(face, frame)}
    name = job["id"] + "-face-" + side
    g = {
        "1": {"class_type": "LoadVideo", "inputs": {"file": name+".mkv"}},
        "2": {"class_type": "GetVideoComponents", "inputs": {"video": ["1",0]}},
        "3": {"class_type": "H3WorkbenchTrackFace", "inputs": {"images": ["2",0], "mask_file": name+".npz", "region": json.dumps(region)}},
    }
    out = remote.execute(job, g, {name+".mkv": source, name+".npz": store.path(mask)}, d, profile="full")
    result = json.loads((out/"output/face-track.json").read_text())
    ref.validate_face(result["face"], s["end"]-s["start"])
    return {**result, "face_proof": store.asset_add(out/"output/face-track.json")}


def correct(c, s, job, d):
    side = job["data"]["options"]["side"]
    vc, vs = virtual(c, s, side)
    mj = copy.deepcopy(job)
    mj["data"]["options"].pop("side")
    g, files, plan = masks.graph(vc, vs, mj, d)
    out = remote.execute(job, g, files, d, profile="full") if g else None
    result = masks.finish(vc, vs, mj, d, out, plan)
    return {
        "side": side,
        "mask": result["asset"],
        "mask_base": vs.get("mask_base") or vs.get("mask") or result["asset"],
        "mask_applied_points": result["points"],
        "mask_proof": result["proof"],
        "empty_frames": result["empty_frames"],
        "coverage": result["coverage"],
    }


def backplate(c, s, job, d):
    r = ref.draft(c, s)
    n = s["end"] - s["start"]
    w, h = c["dimensions"]
    rgb = ref.decode(
        r["binding"]["source"], s["start"], s["end"], c["dimensions"]
    ).copy()
    mask = masks.read(s["mask"], n, c["dimensions"])
    if not mask.any(axis=(1, 2)).all():
        raise ValueError("Source mask contains empty frames")
    # The removal guide expands five pixels. Foreground finishing keeps a separate unexpanded mask.
    expanded = maximum_filter(mask, size=(1, 11, 11))
    rgb[expanded] = [0, 255, 0]
    green = d / "green.mkv"
    media.frozen_file(green, {"rgb_sha256": hashlib.sha256(rgb.data).hexdigest(),
                             "shape": list(rgb.shape), "codec": "ffv1-bgr0-24fps-v1"},
                      lambda: ref.encode(rgb, green))
    anchor = d / "clean-anchor.png"
    with Image.open(store.path(r["anchor"])) as im:
        if abs(im.width / im.height - w / h) > 0.025:
            raise ValueError("Clean anchor framing does not match the shot")
        im.convert("RGB").resize((w, h), Image.Resampling.LANCZOS).save(anchor)
    jid = job["id"]

    def node(t, **inputs):
        return {"class_type": t, "inputs": inputs}

    g = {
        "1": node(
            "UNETLoader",
            unet_name="minimax_h3_ref2va_pruned_int8_convrot.safetensors",
            weight_dtype="default",
        ),
        "2": node("MiniMaxH3SigmaShift", model=["1", 0], shift_video=12, shift_audio=3),
        "3": node(
            "ModelAttentionBackend", model=["2", 0], attention="comfy kitchen attention"
        ),
        "4": node(
            "LoraLoaderModelOnly",
            model=["3", 0],
            lora_name="h3-removal-continuation/B-step2000.safetensors",
            strength_model=r["removal_strength"],
        ),
        "6": node(
            "CLIPLoader",
            clip_name="qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
            type="minimax",
            device="default",
        ),
        "7": node("VAELoader", vae_name="minimax_h3_video_vae_fp16.safetensors"),
        "8": node("VAELoader", vae_name="minimax_h3_audio_vae_fp32.safetensors"),
        "30": node("LoadVideo", file=jid + "-green.mkv"),
        "31": node("GetVideoComponents", video=["30", 0]),
        "40": node("LoadImage", image=jid + "-clean.png"),
        "50": node(
            "H3RelayPersonRemover",
            model=["4", 0],
            clip=["6", 0],
            video_vae=["7", 0],
            audio_vae=["8", 0],
            masked_source=["31", 0],
            clean_first_frame=["40", 0],
            fps=24,
            prompt="Remove the green-masked person and reconstruct the background. Preserve the rest of the video, including its camera motion and frame timing.",
            seed=r["removal_seed"],
            steps=r["removal_steps"],
            window_frames="22",
            window_controls="{}",
        ),
        "51": node("SaveImage", images=["50", 0], filename_prefix="clean"),
    }
    prior_id = job["data"].get("options", {}).get("offload_after")
    profile = "full"
    if prior_id:
        failure = ref.capacity_failure(store.job_get(prior_id))
        profile = (
            "full-layer-stream"
            if failure.get("streaming")
            else "full-stream"
            if failure.get("offloading")
            else "full-offload"
        )
    out = remote.execute(
        job,
        g,
        {jid + "-green.mkv": green, jid + "-clean.png": anchor},
        d,
        profile=profile,
    )
    hist = next(iter(json.loads((out / "history.json").read_text()).values()))
    images = hist["outputs"]["51"]["images"]
    if len(images) != n:
        raise ValueError("Generated backplate frame count mismatch")
    frames = []
    for meta in images:
        p = (out / "output" / meta.get("subfolder", "") / meta["filename"]).resolve()
        if not p.is_relative_to((out / "output").resolve()):
            raise ValueError("Invalid output path")
        with Image.open(p) as im:
            frames.append(np.asarray(im.convert("RGB")))
    target = d / "generated.mkv"
    ref.encode(np.stack(frames), target)
    return ref.plate_record(
        c,
        s,
        store.asset_add(target),
        d,
        {
            "kind": "generated",
            "api": store.asset_add(d / "api.json"),
            "anchor": r["anchor"],
            "mask": s["mask"],
            "expand_pixels": 5,
            "window_frames": 22,
            "history_frames": 18,
        },
        0,
    )


def execute(job, d):
    c, s = job["data"]["snapshot"], job["data"]["shot"]
    kind = job["kind"]
    ref.validate_job(kind, c, s, job["data"].get("options") or {})
    if kind == "refine_plate":
        return ref.import_catalog(c, s, job["data"]["options"]["candidate"], d)
    if kind == "refine_extract":
        return extract(c, s, job, d)
    if kind == "refine_face":
        side=job["data"]["options"]["side"]
        return {"side":side, **face_track(c,s,job,d,side,ref.draft(c,s)[side]["mask"], automatic=job["data"]["options"].get("mode")=="auto")}
    if kind == "refine_mask":
        return correct(c, s, job, d)
    if kind == "refine_compose":
        def progress(phase, completed=None, total=None):
            store.job_update(job["id"], phase=phase, progress={"completed": completed, "total": total} if total else None)
        return ref.compose(c, s, job, d, progress=progress)
    if kind == "backplate_anchor":
        return anchors.generate(c, s, d, clean=True)
    if kind == "backplate":
        return backplate(c, s, job, d)
    raise ValueError("Unknown refinement stage")


def attach(c, s, job, result):
    before = ref.draft(job["data"]["snapshot"], job["data"]["shot"])
    current = ref.draft(c, s)
    kind = job["kind"]
    same_source = ref.source_binding(c, s) == ref.source_binding(
        job["data"]["snapshot"], job["data"]["shot"]
    )
    same_binding = ref.binding(c, s) == before["binding"]
    if kind in {"refine_plate", "backplate"}:
        s.setdefault("backplates", []).append(result)
        stable = (
            same_source
            and current["plate_id"] == before["plate_id"]
            and current["plate_reviewed"] == before["plate_reviewed"]
        )
        if kind == "backplate":
            stable = (
                stable
                and all(
                    current[k] == before[k]
                    for k in [
                        "anchor",
                        "removal_seed",
                        "removal_steps",
                        "removal_strength",
                    ]
                )
                and s.get("mask") == job["data"]["shot"].get("mask")
            )
        if stable:
            current.update(plate_id=result["id"], plate_reviewed=False)
    elif kind == "backplate_anchor":
        s.setdefault("backplate_anchors", []).append(result)
        if same_source and current["anchor"] == before["anchor"]:
            current.update(anchor=result["asset"], anchor_reviewed=False)
    elif kind == "refine_compose":
        s.setdefault("finishes", []).append(result)
        # Selection always remains explicit, even when no inputs changed.
    else:
        side = result["side"]
        f = current[side]
        s.setdefault("foreground_versions", []).append(
            {"job": job["id"], "binding": before["binding"], **before[side], **result}
        )
        frame_merge = (
            kind == "refine_mask"
            and job["data"].get("options", {}).get("mode") == "frame"
        )
        applicable = (
            (
                result.get("attachment") == "merged"
                and f.get("mask") == result.get("merge_from")
            )
            if frame_merge
            else f == before[side]
        )
        if same_binding and applicable:
            f.update(
                {
                    k: v
                    for k, v in result.items()
                    if k not in {"side", "merge_from", "attachment"}
                }
            )
            f["reviewed"] = False
    s["refine"] = current


def attach_frame(job, result, directory):
    """Serialize mask preparation and attachment with editor writes, without nested SQL writes."""
    from . import pipeline

    with store.LOCK:
        c = store.clip_get(job["clip_id"])
        s = next((x for x in c["shots"] if x["id"] == job["shot_id"]), None)
        before = ref.draft(job["data"]["snapshot"], job["data"]["shot"])
        side = result["side"]
        current = ref.draft(c, s) if s else None
        compatible = (
            current
            and current["binding"] == before["binding"]
            and all(
                current[side].get(k) == before[side].get(k)
                for k in ["alpha", "mask_text", "selection", "subject_index"]
            )
        )
        merged = (
            masks.merge_frame(
                before[side],
                current[side],
                {"asset": result["mask"], "proof": result["mask_proof"]},
                job["data"]["options"]["frame"],
                s["end"] - s["start"],
                c["dimensions"],
                directory,
            )
            if compatible
            else None
        )
        if merged:
            result.update(
                mask=merged["asset"],
                mask_applied_points=merged["points"],
                mask_proof=merged["proof"],
                coverage=merged["coverage"],
                empty_frames=merged["empty_frames"],
                merge_from=current[side]["mask"],
                attachment="merged",
            )
        else:
            result["attachment"] = "history_only_newer_frame_edits"
        pipeline.attach(job, lambda v, sh, ok: attach(v, sh, job, result))
