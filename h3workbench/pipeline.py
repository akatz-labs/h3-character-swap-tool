import json, shutil, subprocess, threading, time, traceback, hashlib
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from . import store, media, remote, anchors, masks, refinement, render_models, remote_shared
from .config import MEDIA, WORKERS, check_storage

KINDS = {
    "cuts",
    "transcribe",
    "anchor",
    "mask",
    "render",
    "segment_assemble",
    "assemble",
    "prompts",
    "mask_edit",
}
KINDS |= refinement.KINDS
EXEC = ThreadPoolExecutor(max_workers=5, thread_name_prefix="h3-job")
STOP = threading.Event()
ACTIVE = {}
MUTEX = threading.Lock()


def shot_of(c, id):
    return next(s for s in c["shots"] if s["id"] == id)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def queue(kind, clip_id, shot_id=None, worker="fey", options=None, expected_render=None):
    # Serialize admission with clip edits and job creation. Disjoint segment jobs
    # may coexist; each dependency refers to an already admitted immutable job.
    with store.LOCK:
        return _queue(kind, clip_id, shot_id, worker, options, expected_render)


def _queue(kind, clip_id, shot_id=None, worker="fey", options=None, expected_render=None):
    if kind not in KINDS:
        raise ValueError("Unknown stage")
    if kind not in remote_shared.GPU_KINDS:
        worker = "cpu"
    if kind in remote_shared.GPU_KINDS and worker not in WORKERS and worker != "auto":
        raise ValueError("Unknown worker")
    if worker == "auto":
        from . import remote_sessions
        if not remote_sessions.enabled():
            raise ValueError("Auto placement requires unified workers")
    c = store.clip_get(clip_id)
    s = shot_of(c, shot_id) if shot_id else None
    if kind in {"anchor", "mask", "render", "mask_edit"}:
        if not s or s["mode"] != "generate":
            raise ValueError("Select a generated shot")
    if kind in refinement.KINDS:
        refinement.validate_job(kind, c, s, options or {})
        # Freeze default settings in this job's snapshot, without modifying the clip.
        s["refine"] = refinement.draft(c, s)
        if (options or {}).get("offload_after"):
            failure = refinement.validate_offload(kind, c, s, options)
            if failure["worker"] != worker:
                raise ValueError(
                    "Retry on the worker with the measured capacity failure"
                )
    if kind == "anchor" and not c.get("reference"):
        raise ValueError("Add a reference image first")
    if kind == 'anchor' and options:
        from . import segments
        if set(options) != {'segment_id'} or not any(x['id']==options['segment_id'] for x in segments.current(s)):
            raise ValueError('Choose a current segment for the anchor')
    if kind == "mask_edit":
        options = options or {}
        if set(options) != {"mode", "frame"} or options["mode"] not in {
            "frame",
            "shot",
        }:
            raise ValueError("Choose Update frame or Track shot")
        if (
            type(options["frame"]) is not int
            or not 0 <= options["frame"] < s["end"] - s["start"]
        ):
            raise ValueError("Correction frame is outside the shot")
        masks.validate_points(s.get("mask_points", []), s["end"] - s["start"])
        if not s.get("mask") and not s.get("mask_points"):
            raise ValueError("Add an include point or track the person first")
    if kind == "render":
        if options is not None and (not isinstance(options, dict) or set(options) - {"model_offloading", "segments"}):
            raise ValueError("Unknown render execution option")
        options = {**(options or {}), "model_offloading": (options or {}).get("model_offloading", True)}
        if type(options["model_offloading"]) is not bool:
            raise ValueError("Model offloading must be true or false")
        config = render_models.settings(s)
        if expected_render is not None:
            base_fields = {"seed", "prompt", "render_mp", "lora_id", "use_anchor", "use_mask"}
            if not isinstance(expected_render, dict) or set(expected_render) not in (
                base_fields, base_fields | {"window_frames", "history_frames"}
            ):
                raise ValueError("Invalid expected generation settings")
            # An already-open older client expects the original window defaults.
            expected_render = {"window_frames": 124, "history_frames": 18, **expected_render}
            actual = {"seed": s["seed"], "prompt": s["prompt"], "render_mp": s.get("render_mp", 1.0), **config}
            if actual != expected_render:
                raise store.Conflict("Generation settings changed before submission; review your saved settings")
        if not c.get("reference"):
            raise ValueError("Add a character reference before rendering")
        if "segments" not in options and config["use_anchor"] and not s.get("anchor"):
            raise ValueError("Select an anchor or disable anchor conditioning")
        if "segments" not in options and config["use_mask"]:
            if masks.pending_frames(s):
                raise ValueError("Apply your point corrections before rendering")
            if not s.get("mask"):
                raise ValueError("Create a mask or disable masked diffusion")
            if s.get("mask_empty_frames"):
                raise ValueError("Correct missing mask frames before rendering")
    segment_plan = None
    if kind == 'render' and 'segments' in options:
        from . import segment_jobs
        segment_plan = segment_jobs.freeze(c,s,options['segments'])
    if kind == 'segment_assemble':
        from . import segments
        if not s or not all(v['current'] for v in segments.states(c,s)):
            raise ValueError('Choose current takes for every segment before assembling')
    if kind == "assemble":
        for x in c["shots"]:
            if x["mode"] == "generate" and not x.get("selected_render"):
                raise ValueError("Every generated shot needs a selected result")

            if x["mode"] == "generate":
                refinement.selected(c, x)
    frame_request = None
    if kind in {"refine_mask", "mask_edit"} and options.get("mode") == "frame":
        foreground = s["refine"][options["side"]] if kind == "refine_mask" else s
        frame_request = fingerprint(
            {
                "mask": foreground.get("mask"),
                "frame": options["frame"],
                "points": masks.groups(foreground.get("mask_points", [])).get(
                    options["frame"]
                ),
                "source": c.get("render_source") or c["source"],
                "start": s["start"],
                "end": s["end"],
            }
        )
    return store.job_create(
        kind,
        clip_id,
        shot_id,
        {
            "options": options,
            **({"segment_plan": segment_plan, "segment_scope": segment_jobs.scope(segment_plan),
                "segment_dependencies": segment_plan['dependencies']} if segment_plan else {}),
            "snapshot": c,
            "shot": s,
            "worker": worker,
            "requested_worker": worker,
            "execution_backend": "shared" if store.setting("shared_executor", False) and kind in {"mask", "render", "mask_edit", "refine_extract", "refine_mask", "refine_face", "backplate"} else "direct",
            "fingerprint": fingerprint({"clip": c, "shot": s}),
            "frame_request": frame_request,
            "phase": "Queued",
            **({"render_spec": media.render_spec(c, s), "generation": config,
                "lora": render_models.LORAS[config["lora_id"]]} if kind == "render" else {}),
        },
    )


def check_finalization_cancel(job):
    if job["data"].get("execution_backend") != "shared":
        return remote.check_cancel(job)
    latest = store.job_get(job["id"])["data"]
    if latest.get("cancel_requested") and latest.get("execution_backend") == "shared" and latest.get("shared_phase") == "completed":
        store.job_update(job["id"], completion_won_cancellation=True)
        return
    remote.check_cancel(job)


def attach(job, fn):
    with MUTEX:
        check_finalization_cancel(job)
        store.job_update(job["id"], finalizing=True)
    # A changed shot keeps its historical job artifacts but cannot receive stale selections.
    def change(c):
        if job["shot_id"]:
            try:
                s = shot_of(c, job["shot_id"])
            except StopIteration:
                return
            original = job["data"]["shot"]
            stable = (
                all(
                    s.get(k) == original.get(k)
                    for k in [
                        "start",
                        "end",
                        "selection",
                        "subject_index",
                        "source_frame",
                    ]
                )
                and c["reference"] == job["data"]["snapshot"]["reference"]
            )
            fn(c, s, stable)
        else:
            fn(c, None, True)

    store.clip_mutate(job["clip_id"], change)


def transcribe(job, d):
    c = job["data"]["snapshot"]
    if not c["audio"]:
        return {"text": "", "words": [], "model": "no audio"}
    audio = d / "audio.wav"
    media.ff(
        [
            "-i",
            store.path(c.get("render_source") or c["source"]),
            "-vn",
            "-ar",
            "16000",
            "-ac",
            "1",
            audio,
        ]
    )
    from . import transcription

    result = transcription.transcribe(
        audio,
        progress=lambda phase: store.job_update(job["id"], phase=phase),
        check_cancel=lambda: remote.check_cancel(job),
    )
    (d / "transcript.json").write_text(json.dumps(result, indent=2))
    return result


def assemble(job, d, progress=None):
    report_progress = progress or (lambda **kwargs: None)
    c = job["data"]["snapshot"]
    w, h = c["dimensions"]
    masters = {}
    finishes = {
        s["id"]: refinement.selected(c, s)
        for s in c["shots"]
        if s["mode"] == "generate"
    }
    refined = any(finishes.values())
    for side_name in ["source", "character"]:
        parts = []
        for i, s in enumerate(c["shots"]):
            report_progress(phase=f"Building {side_name} shot {i + 1} of {len(c['shots'])}", progress={"completed": i + (len(c["shots"]) if side_name == "character" else 0), "total": len(c["shots"]) * 2})
            target = d / f"{side_name}-part-{i:03}.mkv"
            count = s["end"] - s["start"]
            finish = finishes.get(s["id"])
            if finish:
                asset, start = finish[side_name], 0
            elif side_name == "source" or s["mode"] == "preserve":
                asset, start = c.get("render_source") or c["source"], s["start"]
            else:
                asset = next(
                    x["asset"] for x in s["renders"] if x["id"] == s["selected_render"]
                )
                start = 0
            if (
                not start
                and asset != (c.get("render_source") or c["source"])
                and media.probe(store.path(asset))["frames"] != count
            ):
                raise ValueError(
                    "Selected shot length differs from its source interval"
                )
            media.ff(
                [
                    "-i",
                    store.path(asset),
                    "-vf",
                    f"trim=start_frame={start}:end_frame={start + count},setpts=PTS-STARTPTS,scale={w}:{h}:flags=lanczos,setsar=1",
                    "-an",
                    "-c:v",
                    "ffv1",
                    "-r",
                    "24",
                    "-pix_fmt",
                    "bgr0",
                    target,
                ]
            )
            if media.probe(target)["frames"] != count:
                raise ValueError("Selected source range is incomplete")
            parts.append(target)
        listing = d / f"{side_name}-concat.txt"
        listing.write_text(
            "".join("file '" + str(p).replace("'", "'\\''") + "'\n" for p in parts)
        )
        master_path = d / f"{side_name}-master.mkv"
        media.ff(
            ["-f", "concat", "-safe", "0", "-i", listing, "-c", "copy", master_path]
        )
        masters[side_name] = master_path
    master = masters["character"]
    report_progress(phase="Encoding full clip and source audio", progress=None)
    target = d / "result.mp4"
    media.ff(
        [
            "-i",
            master,
            "-i",
            store.path(c["source"]),
            "-map",
            "0:v",
            "-map",
            "1:a?",
            "-c:v",
            "libx264",
            "-r",
            "24",
            "-crf",
            "16",
            "-preset",
            "fast",
            "-c:a",
            "copy",
            "-movflags",
            "+faststart",
            target,
        ]
    )
    if media.probe(target)["frames"] != c["frames"]:
        raise ValueError("Assembled frame count mismatch")
    source_view = d / "source-pair.mp4"
    media.ff(
        [
            "-i",
            masters["source"],
            "-i",
            store.path(c["source"]),
            "-map",
            "0:v",
            "-map",
            "1:a?",
            "-c:v",
            "libx264",
            "-r",
            "24",
            "-crf",
            "16",
            "-preset",
            "fast",
            "-c:a",
            "copy",
            "-movflags",
            "+faststart",
            source_view,
        ]
    )
    # Both report layouts fit all three complete images and retain source audio packets.
    import math

    ref = store.path(c["reference"]) if c.get("reference") else store.path(c["poster"])
    comparisons = {}
    for layout in ["16x9", "fit"]:
        report_progress(phase=f"Building {layout} comparison export")
        natural = (
            1920
            if layout == "16x9"
            else max(
                1920,
                math.ceil(2 * 1024 * max(c["width"] / c["height"], w / h) / 0.8 + 32),
            )
        )
        factor = min(1, 2560 / natural)
        W = int(natural * factor) // 2 * 2
        H = int(1080 * factor) // 2 * 2
        header = max(30, int(56 * factor) // 2 * 2)
        gap = 16
        center = int(W * 0.2) // 2 * 2
        side = (W - center - 2 * gap) // 4 * 2
        center = W - 2 * side - 2 * gap
        content = H - header

        def panel(i, width):
            return f"[{i}:v]setpts=PTS-STARTPTS,scale={width}:{content}:force_original_aspect_ratio=decrease:force_divisible_by=2,pad={width}:{content}:(ow-iw)/2:(oh-ih)/2:color=0x151515,setsar=1,format=yuv420p[p{i}]"

        filters = [
            panel(0, side),
            panel(1, center),
            panel(2, side),
            f"color=c=0x151515:s={gap}x{content}:r=24[g1]",
            f"color=c=0x151515:s={gap}x{content}:r=24[g2]",
            f"[p0][g1][p1][g2][p2]hstack=inputs=5:shortest=1,pad={W}:{H}:0:{header}:color=0x202024[stack]",
        ]
        labels = [
            ("Source pair" if refined else "Original", side / 2),
            ("Reference", side + gap + center / 2),
            ("Result", side + 2 * gap + center + side / 2),
        ]
        filters.append(
            "[stack]"
            + ",".join(
                f"drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf:text={label}:fontsize={max(18, round(26 * factor))}:fontcolor=white:x={x}-text_w/2:y=({header}-text_h)/2"
                for label, x in labels
            )
            + "[v]"
        )
        report = d / f"comparison-{layout}.mp4"
        media.ff(
            [
                "-i",
                source_view,
                "-loop",
                "1",
                "-framerate",
                "24",
                "-i",
                ref,
                "-i",
                target,
                "-filter_complex",
                ";".join(filters),
                "-map",
                "[v]",
                "-map",
                "0:a?",
                "-c:v",
                "libx264",
                "-r",
                "24",
                "-crf",
                "18",
                "-preset",
                "fast",
                "-c:a",
                "copy",
                "-movflags",
                "+faststart",
                report,
            ]
        )
        if media.probe(report)["frames"] != c["frames"]:
            raise ValueError("Comparison frame count mismatch")
        comparisons[layout] = store.asset_add(report)
    receipt = {
        "source_sha256": store.asset(c["source"])["sha256"],
        "frames": c["frames"],
        "shots": c["shots"],
        "result": store.asset_add(target),
        "comparison": comparisons["16x9"],
        "comparison_fit": comparisons["fit"],
        "master": store.asset_add(master),
        "raw_decoded": not refined,
        "source_pair": store.asset_add(source_view),
        "source_master": store.asset_add(masters["source"]),
        "selected_finishes": {
            sid: f["id"] if f else None for sid, f in finishes.items()
        },
    }
    (d / "provenance.json").write_text(json.dumps(receipt, indent=2))
    receipt["provenance"] = store.asset_add(d / "provenance.json")
    return receipt


def execute(id):
    job = store.job_get(id)
    kind = job["kind"]
    from . import transcription_resources
    if kind == transcription_resources.INSTALL_KIND:
        transcription_resources.run_install(job)
        return
    c = job["data"]["snapshot"]
    s = job["data"]["shot"]
    d = MEDIA / "jobs" / id
    d.mkdir(exist_ok=True)
    (d / "request.json").write_text(json.dumps(job, indent=2))
    try:
        expected_gib = 3
        if kind in refinement.KINDS:
            import math

            frames = s["end"] - s["start"]
            bytes_per_pixel = 80 if kind == "backplate" else 40
            expected_gib = max(
                3,
                math.ceil(
                    frames
                    * c["dimensions"][0]
                    * c["dimensions"][1]
                    * bytes_per_pixel
                    / 2**30
                ),
            )
        check_storage(expected_gib)
        store.job_update(
            id,
            phase={
                "anchor": "Generating anchor",
                "transcribe": "Transcribing audio",
                "cuts": "Finding edit points",
                "assemble": "Assembling selected shots",
            }.get(kind, "Preparing inputs"),
        )
        if kind in refinement.KINDS:
            from . import refine_jobs

            result = refine_jobs.execute(job, d)
            check_finalization_cancel(job)
            if kind == 'refine_mask' and job['data']['options'].get('mode') == 'frame':
                refine_jobs.attach_frame(job, result, d)
            else:
                attach(job, lambda v, sh, ok: refine_jobs.attach(v, sh, job, result))
        elif kind == 'render' and job['data'].get('segment_plan'):
            from . import segment_jobs
            result = segment_jobs.execute(job,d)
        elif kind == 'segment_assemble':
            from . import segment_jobs
            result = segment_jobs.assemble(c,s,d)
            segment_jobs.attach_assembly(job,c,s,result)
        elif kind == "cuts":
            cuts = media.detect_cuts(c)
            attach(job, lambda v, _, ok: v.update(suggested_cuts=cuts))
            result = {"cuts": cuts}
        elif kind == "prompts":

            def update(v, _, ok):
                for sh in v["shots"]:
                    sh["prompt"] = media.prompt(v, sh)

            attach(job, update)
            result = {"updated": len(c["shots"])}
        elif kind == "transcribe":
            result = transcribe(job, d)

            def update(v, _, ok):
                v["transcript"] = result
                for sh in v["shots"]:
                    if not sh.get("dialogue_edited"):
                        sh["dialogue"] = media.dialogue(v, sh["start"], sh["end"])
                        if not sh.get("prompt_edited"):
                            sh["prompt"] = media.prompt(v, sh)

            attach(job, update)
        elif kind == 'anchor' and (job['data'].get('options') or {}).get('segment_id'):
            from . import segment_jobs
            result=segment_jobs.generate_anchor(job,d)
        elif kind == "anchor":
            result = anchors.generate(c, s, d)

            def update(v, sh, ok):
                sh["anchors"].append(result)
                if (
                    ok
                    and all(
                        (sh.get(k) or "") == (s.get(k) or "")
                        for k in ("anchor_instructions", "anchor_prompt")
                    )
                    and v.get("character") == c.get("character")
                ):
                    sh.update(
                        anchor=result["asset"],
                        anchor_approved=False,
                    )

            attach(job, update)
        elif kind == "mask_edit":
            graph, files, plan = masks.graph(c, s, job, d)
            out = remote.execute(job, graph, files, d) if graph else None
            result = masks.finish(c, s, job, d, out, plan)

            def update(v, sh, ok):
                current = ok and (
                    (
                        result.get("attachment") == "merged"
                        and sh.get("mask") == result.get("merge_from")
                    )
                    if job["data"]["options"]["mode"] == "frame"
                    else sh.get("mask") == s.get("mask")
                    and sh.get("mask_points", []) == s.get("mask_points", [])
                )
                label = (
                    "Tracked shot"
                    if job["data"]["options"]["mode"] == "shot"
                    else f"Edited frame {s['start'] + job['data']['options']['frame']}"
                )
                masks.install(sh, result, label, result["points"], current=current)

            if job["data"]["options"]["mode"] == "frame":
                with store.LOCK:
                    live = store.clip_get(job["clip_id"])
                    shot = next((x for x in live["shots"] if x["id"] == s["id"]), None)
                    compatible = (
                        shot
                        and all(
                            shot.get(k) == s.get(k)
                            for k in [
                                "start",
                                "end",
                                "selection",
                                "subject_index",
                                "mask_text",
                            ]
                        )
                        and live["source"] == c["source"]
                    )
                    merged = (
                        masks.merge_frame(
                            s,
                            shot,
                            result,
                            job["data"]["options"]["frame"],
                            s["end"] - s["start"],
                            c["dimensions"],
                            d,
                        )
                        if compatible
                        else None
                    )
                    if merged:
                        result.update(
                            merged, attachment="merged", merge_from=shot.get("mask")
                        )
                    else:
                        result["attachment"] = "history_only_newer_frame_edits"
                    attach(job, update)
            else:
                attach(job, update)
        elif kind in {"mask", "render"}:
            graph, files = (
                remote.graph_mask if kind == "mask" else remote.graph_render
            )(c, s, job, d)
            out = remote.execute(job, graph, files, d, profile=(
                "full-offload" if render_models.offloading(job)
                else "full") if kind == "render" else "legacy")
            if kind == "mask":
                file = out / "output/person-mask.npz"
                import numpy as np

                with np.load(file, allow_pickle=False) as z:
                    mask = z["mask"]
                    coverage = (mask > 0.5).sum(axis=(1, 2)).tolist()
                missing = [i for i, n in enumerate(coverage) if n == 0]
                if missing:
                    store.job_update(
                        id, phase=f"Recovering {len(missing)} missing mask frames"
                    )
                    recovery = d / "recovery"
                    recovery.mkdir(exist_ok=True)
                    recovered = remote.execute(
                        job, remote.graph_mask_recovery(s, job, missing), {}, recovery
                    )
                    for f in missing:
                        with np.load(
                            recovered / f"output/recovered-{f:04}.npz",
                            allow_pickle=False,
                        ) as z:
                            mask[f] = z["mask"][0]
                    file = d / "person-mask-recovered.npz"
                    np.savez_compressed(file, mask=mask)
                    coverage = (mask > 0.5).sum(axis=(1, 2)).tolist()
                result = {
                    "asset": store.asset_add(file),
                    "coverage": coverage,
                    "empty_frames": [i for i, n in enumerate(coverage) if n == 0],
                    "recovered_frames": missing,
                }
                if len(coverage) != s["end"] - s["start"]:
                    raise RuntimeError("Mask frame count mismatch")

                def update(v, sh, ok):
                    current = (
                        ok
                        and sh.get("mask_points", []) == s.get("mask_points", [])
                        and sh.get("mask") == s.get("mask")
                    )
                    masks.install(sh, result, "Automatic tracking", [], current=current)
                    if current:
                        sh["mask_base"] = result["asset"]

                attach(job, update)
            else:
                history = json.loads((out / "history.json").read_text())
                hist = next(iter(history.values()))
                output = hist["outputs"]["807"]
                (d / "node-output.json").write_text(json.dumps(output, indent=2))
                candidates = [p for p in (out / "output").rglob(id + "*.mp4")]
                if len(candidates) != 1:
                    raise RuntimeError("Could not uniquely identify assembled render")
                info = media.probe(candidates[0])
                spec = job["data"].get("render_spec") or media.render_spec(c, s)
                if info["frames"] != s["end"] - s["start"]:
                    raise RuntimeError("Rendered timeline mismatch")
                if [info["width"], info["height"]] != spec["dimensions"] or abs(
                    info["fps"] - 24
                ) > 0.001:
                    raise RuntimeError(
                        "Rendered size differs from the queued H3 canvas"
                    )
                result = {
                    "id": id,
                    "asset": store.asset_add(candidates[0]),
                    "api": store.asset_add(d / "api.json"),
                    "seed": s["seed"],
                    "prompt": s["prompt"],
                    **spec,
                    "settings": {
                        "render_mp": spec["render_mp"],
                        "dimensions": spec["dimensions"],
                        "generation_expansion": spec["expansion"],
                        **render_models.settings(s),
                        **{
                            k: s.get(k)
                            for k in [
                                "seed",
                                "prompt",
                                "anchor",
                                "mask",
                                "expansion",
                                "dialogue",
                                "speaker",
                            ]
                        },
                    },
                    "reference": c["reference"],
                    "created": store.now(),
                }
                result.update(
                    lora=job["data"].get("lora") or render_models.identify(graph),
                    worker=job["data"]["worker"],
                    **render_models.settings(s),
                    residency="offload" if render_models.offloading(job) else "full",
                )

                def update(v, sh, ok):
                    sh["renders"].append(result)
                    if (
                        ok
                        and (not render_models.settings(s)["use_mask"] or
                             (not masks.pending_frames(sh) and not sh.get("mask_empty_frames")))
                        and render_models.settings(sh) == render_models.settings(s)
                        and sh.get("render_mp", 1.0) == s.get("render_mp", 1.0)
                        and all(
                            sh.get(k) == s.get(k)
                            for k in ["prompt", "seed", "anchor", "mask", "expansion"]
                        )
                    ):
                        sh["selected_render"] = id
                        v["result"] = None

                attach(job, update)
        else:
            result = assemble(job, d, progress=lambda **fields: store.job_update(id, **fields))
            attach(
                job,
                lambda v, _, ok: (
                    v.update(
                        result=result["result"],
                        source_pair=result["source_pair"],
                        source_master=result["source_master"],
                        raw_decoded=result["raw_decoded"],
                        comparison=result["comparison"],
                        comparison_fit=result["comparison_fit"],
                        provenance=result["provenance"],
                        review="pending",
                    )
                    if v["shots"] == c["shots"]
                    else None
                ),
            )
        store.job_update(id, "success", phase="Complete", result=result)
    except remote.Cancelled as e:
        store.job_update(id, "cancelled", phase="Cancelled", cancellation=str(e))
    except subprocess.TimeoutExpired as e:
        store.job_update(
            id,
            "interrupted",
            phase="Needs inspection",
            error="The client timed out. Inspect the saved receipt before running this stage again.",
        )
    except remote.Busy as e:
        automatic = job["data"].get("requested_worker") == "auto" and not any(d.rglob("attempt.json")) and not any(d.rglob("shared-binding.json"))
        store.job_update(id, "waiting", phase=str(e), retry_after=store.now() + (2 if automatic else 5 if "owned session" in str(e) else 30),
                         **({"worker": "auto"} if automatic else {}))
    except remote_shared.Uncertain as e:
        store.job_update(id, "interrupted", phase="Shared attempt needs inspection", error=str(e)[-4000:])
    except Exception as e:
        (d / "failure.txt").write_text(traceback.format_exc())
        store.job_update(id, "failed", phase="Failed", error=str(e)[-4000:])

        if kind in refinement.KINDS or kind == "render":
            measured = refinement.capacity_failure(store.job_get(id))
            if measured:
                store.job_update(
                    id, capacity_failure=measured, error=measured["message"]
                )
    finally:
        from . import remote_sessions
        if remote_sessions.enabled() and job["data"].get("execution_backend") != "shared":
            try:
                remote_sessions.finish_job(store.job_get(id))
            except Exception as cleanup_error:
                store.job_update(id, cleanup_warning=str(cleanup_error)[-500:])


def resource(job):
    if job.get("state") in {"running", "cancelling"} and job["data"].get("execution_resource"):
        return job["data"]["execution_resource"]
    return (
        job["data"]["worker"]
        if job["kind"]
        in {"mask", "render", "mask_edit", "refine_extract", "refine_mask", "refine_face", "backplate"}
        else "image"
        if job["kind"] in {"anchor", "backplate_anchor"}
        else "cpu"
    )


def scheduler():
    automatic_cache = {"shared": {"time": 0, "workers": {}}, "direct": {"time": 0, "workers": {}}}
    while not STOP.wait(.5):
        pending = store.pending_jobs()
        for backend in automatic_cache:
            current = automatic_cache[backend]
            if any(resource(j) == "auto" and (j["data"].get("execution_backend") == "shared") == (backend == "shared") for j in pending) and store.now() - current["time"] >= 5:
                current.update(time=store.now(), workers=remote_shared.all_status() if backend == "shared" else remote.all_status())
        with MUTEX:
            for id, f in list(ACTIVE.items()):
                if f.done():
                    ACTIVE.pop(id)
            if store.setting("queue_paused", False):
                continue
            busy = {resource(store.job_get(id)) for id in ACTIVE}
            for j in pending:
                from . import segment_jobs
                if not segment_jobs.resolve_dependencies(j):
                    continue
                r = resource(j)
                if r == "auto":
                    from . import session_policy
                    shared = j["data"].get("execution_backend") == "shared"
                    observations = automatic_cache["shared" if shared else "direct"]["workers"]
                    if shared:
                        # This reserves CPU preparation only; exact warm placement
                        # is resolved through the SDK after the graph is frozen.
                        r = next((w for w, s in observations.items() if s.get("auto_available") and
                                  s["state"] in {"available", "ready", "warm_idle", "unloaded_idle", "busy", "running", "starting"}), None)
                    else:
                        r = session_policy.choose_worker(j, observations, busy)
                    if r is None:
                        continue
                from . import remote_sessions
                gpu = r in WORKERS
                preparing = gpu and remote_sessions.enabled()
                if preparing:
                    # One preparation stage at a time, plus at most one stage
                    # waiting for each worker. GPU completion can release the
                    # worker while this job performs CPU attachment/mixing.
                    waiting = any(store.job_get(id)["data"].get("gpu_waiting") and store.job_get(id)["data"].get("worker") == r for id in ACTIVE)
                    if "prep" in busy or waiting:
                        continue
                scheduling_resource = "prep" if preparing else r
                if (
                    j["state"] in {"queued", "waiting"}
                    and (j["data"].get("retry_after") or 0) <= store.now()
                    and scheduling_resource not in busy
                ):
                    if not store.claim_job(j["id"], r if resource(j) == "auto" else None,
                                           "prep" if preparing else None):
                        continue
                    busy.add(scheduling_resource)
                    ACTIVE[j["id"]] = EXEC.submit(execute, j["id"])


def start():
    # Backfill lightweight public coverage for existing segment jobs, including
    # historical failures and in-flight batches accepted by an older app.
    from . import segment_jobs
    existing = {j['id']: j for j in store.jobs()}
    existing.update({j['id']: j for j in store.pending_jobs(segment_jobs.ACTIVE_STATES, full=True)})
    for j in existing.values():
        if j['data'].get('segment_plan') and not j['data'].get('segment_scope'):
            store.job_update(j['id'], segment_scope=segment_jobs.scope(j['data']['segment_plan']))
    # A restart cannot safely infer completion of a remote GPU/image action.
    for j in store.pending_jobs(("running", "cancelling")):
        from . import remote_shared
        if remote_shared.recoverable(j):
            store.job_update(j["id"], "queued", phase="Reconciling accepted shared attempt")
            continue
        from . import remote_sessions
        if remote_sessions.enabled() and remote_sessions.recoverable(j):
            store.job_update(j["id"], "queued", phase="Reconciling existing worker attempt")
            continue
        if j["state"] in {"running", "cancelling"}:
            store.job_update(
                j["id"],
                "interrupted",
                error="Service restarted during this job. Inspect its receipt before retrying.",
            )
    for j in store.jobs():
        if j["kind"] == "render" and j["state"] == "failed" and not j["data"].get("capacity_failure"):
            measured = refinement.capacity_failure(j)
            if measured:
                store.job_update(j["id"], capacity_failure=measured)
    threading.Thread(target=scheduler, daemon=True).start()
