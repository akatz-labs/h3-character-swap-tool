import io, json, shutil, subprocess, threading, time, hashlib, os
from pathlib import Path
from urllib.parse import urlparse
from contextlib import asynccontextmanager
import numpy as np
from PIL import Image
from scipy.ndimage import distance_transform_edt
from fastapi import FastAPI, Request, HTTPException, UploadFile, File, Form, Query
from fastapi.responses import FileResponse, JSONResponse, Response, HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware
from . import (
    store,
    media,
    pipeline,
    remote,
    remote_shared,
    masks,
    comparisons,
    previews,
    refinement,
    anchors,
)
from .config import ROOT, MEDIA, BASE_PATH, PORTABLE, MEDIA_MOUNT, SHARED_ROOT, check_storage


@asynccontextmanager
async def life(app):
    store.init()
    from . import remote_shared
    remote_shared.instance()
    pipeline.start()
    yield
    pipeline.STOP.set()


app = FastAPI(lifespan=life)
from .segment_api import router as segment_router
app.include_router(segment_router)
app.add_middleware(GZipMiddleware, minimum_size=1000, compresslevel=4)


@app.middleware("http")
async def trusted_origin(request, call_next):
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        if origin and urlparse(origin).netloc != request.headers.get("host"):
            return JSONResponse({"detail": "Cross-origin writes are disabled"}, 403)
    return await call_next(request)


@app.exception_handler(ValueError)
async def invalid(req, e):
    return JSONResponse(
        {"detail": str(e)}, 409 if isinstance(e, store.Conflict) else 400
    )


@app.exception_handler(KeyError)
async def missing(req, e):
    return JSONResponse({"detail": str(e)}, 404)


@app.get("/api/state")
def state(request: Request, batch: str | None = None):
    response = JSONResponse({
        "batches": store.batches(),
        "clips": store.clips(batch),
        "jobs": store.public_jobs(),
        "queue_paused": store.setting("queue_paused", False),
        "unified_workers": store.setting("unified_workers", False),
        "auto_placement_supported": (any(w.get("auto_available") for w in WORKER_CACHE["value"].values())
                                     if store.setting("shared_executor", False) else store.setting("unified_workers", False)),
        "shared_executor": store.setting("shared_executor", False),
        "workers": list(remote.WORKERS),
        "anchor_providers": anchors.providers(),
    })
    etag = '"' + hashlib.sha256(response.body).hexdigest() + '"'
    headers = {"ETag": etag, "Cache-Control": "private, no-cache"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    response.headers.update(headers)
    return response


WORKER_CACHE = {"time": 0, "value": {}}


@app.post("/api/queue")
def queue_control(body: dict):
    if type(body.get("paused")) is not bool:
        raise ValueError("paused must be true or false")
    store.set_setting("queue_paused", body["paused"])
    return {"paused": body["paused"]}


@app.post("/api/anchor-provider")
def select_anchor_provider(body: dict):
    if set(body) != {"provider"} or body["provider"] not in {"manual", "codex-local"}:
        raise ValueError("Choose an available anchor provider")
    selected = next(item for item in anchors.providers()["items"] if item["id"] == body["provider"])
    if not selected["available"]:
        raise ValueError("This image provider is not installed")
    store.set_setting("anchor_provider", body["provider"])
    return anchors.providers()


@app.get("/api/transcription-resource")
def transcription_resource():
    from . import transcription_resources as resources
    return {"model": resources.inventory(), "jobs": resources.resource_jobs()}


@app.post("/api/transcription-resource/{action}")
def transcription_resource_action(action: str, body: dict):
    from . import transcription_resources as resources
    if action == "install" and not body:
        return resources.request_install()
    if action == "uninstall-plan" and not body:
        return resources.removal_plan()
    if action == "uninstall":
        return resources.uninstall(body)
    raise ValueError("Unknown transcription resource action")


@app.get("/api/workers")
def workers():
    if time.time() - WORKER_CACHE["time"] > 10:
        from . import remote_shared
        WORKER_CACHE.update(time=time.time(), value=remote_shared.all_status() if remote_shared.enabled() else remote.all_status())
    return WORKER_CACHE["value"]


@app.post("/api/workers/{worker}/release")
def release_worker(worker: str):
    from . import remote_sessions
    if worker not in remote.WORKERS:
        raise ValueError("Unknown worker")
    from . import remote_shared
    if not remote_sessions.enabled() and not remote_shared.enabled():
        raise ValueError("Unified worker lifecycle is not enabled")
    if any(pipeline.resource(j) == worker for j in store.pending_jobs(("running", "cancelling"))):
        raise store.Conflict("Worker has an active job; use the job stop control")
    try:
        result = remote_shared.release_idle(worker) if remote_shared.enabled() else remote_sessions.release_idle(worker)
    except RuntimeError as error:
        raise store.Conflict(str(error)) from error
    WORKER_CACHE["time"] = 0
    return result


@app.post("/api/batches")
def create_batch(body: dict):
    return {"id": store.batch_create(body.get("name", "Untitled batch"))}


@app.patch("/api/batches/{id}")
def rename_batch(id: str, body: dict):
    if set(body) != {"name", "expected_name"} or not all(isinstance(v, str) for v in body.values()):
        raise ValueError("Supply the new name and the previously displayed name")
    return store.batch_rename(id, body["name"], body["expected_name"])


@app.get("/api/clips/{id}")
def get_clip(id: str):
    return store.clip_get(id)


@app.post("/api/clips/{id}/review-preview")
def prepare_review_preview(id: str, body: dict):
    if (
        not {"layout"} <= set(body) <= {"layout", "retry"}
        or not isinstance(body.get("layout"), str)
        or type(body.get("retry", False)) is not bool
    ):
        raise ValueError("Choose a final comparison preview format")
    return comparisons.prepare_review(
        store.clip_get(id), body["layout"], body.get("retry", False)
    )


@app.post("/api/clips/{id}/shots/{sid}/comparison")
def prepare_comparison(id: str, sid: str, body: dict):
    c = store.clip_get(id)
    s = pipeline.shot_of(c, sid)
    if (
        not {"left", "right"} <= set(body) <= {"left", "right", "retry"}
        or not all(isinstance(body[k], str) for k in ["left", "right"])
        or type(body.get("retry", False)) is not bool
    ):
        raise ValueError("Choose left and right comparison clips")
    return comparisons.prepare(
        c, s, body["left"], body["right"], body.get("retry", False)
    )


@app.patch("/api/clips/{id}")
def patch_clip(id: str, body: dict):
    revision = body["revision"]
    changes = body.get("changes", {})
    if not set(changes) <= {
        "name",
        "character",
        "cuts_approved",
        "review",
        "reference",
        "review_notes",
    }:
        raise ValueError("Unknown clip field")
    if changes.get("review", "pending") not in {"pending", "keep", "maybe", "skip"}:
        raise ValueError("Unknown review decision")
    if changes.get("reference"):
        store.asset(changes["reference"])

    def mutate(c):
        if "reference" in changes and changes["reference"] != c["reference"]:
            for s in c["shots"]:
                s["anchor_approved"] = False
        c.update(changes)

    return store.clip_mutate(id, mutate, revision)


@app.post("/api/clips/{id}/cuts")
def cuts(id: str, body: dict):
    c = store.clip_get(id)
    if c["revision"] != body["revision"]:
        raise store.Conflict("Clip changed; reload before editing cuts")
    if not set(body) <= {"revision", "cuts", "modes", "reviewed"}:
        raise ValueError("Unknown shot editing field")
    if type(body.get("reviewed", False)) is not bool:
        raise ValueError("Reviewed must be true or false")
    return media.set_cuts(
        c, body["cuts"], body.get("modes"), body.get("reviewed", False)
    )


@app.post("/api/clips/{id}/preview")
def clip_preview(id: str, body: dict):
    if not set(body) <= {"retry"} or type(body.get("retry", False)) is not bool:
        raise ValueError("Invalid preview request")
    return previews.prepare_clip(store.clip_get(id), body.get("retry", False))


@app.get("/api/clips/{id}/shots/{sid}/render-plan")
def render_plan(id: str, sid: str, window_frames: int | None = None, history_frames: int | None = None):
    from . import render_models
    shot = dict(pipeline.shot_of(store.clip_get(id), sid))
    if window_frames is not None:
        shot["window_frames"] = window_frames
    if history_frames is not None:
        shot["history_frames"] = history_frames
    return render_models.window_plan(shot)


@app.patch("/api/clips/{id}/shots/{sid}")
def patch_shot(id: str, sid: str, body: dict):
    fields = {
        "dialogue",
        "speaker",
        "prompt",
        "anchor_prompt",
        "anchor_instructions",
        "seed",
        "render_mp",
        "window_frames",
        "history_frames",
        "lora_id",
        "use_anchor",
        "use_mask",
        "expansion",
        "selection",
        "subject_index",
        "mask_text",
        "mask_points",
        "mask_version",
        "mode",
        "anchor",
        "anchor_approved",
        "mask_approved",
        "selected_render",
    }
    x = body["changes"]
    if not set(x) <= fields:
        raise ValueError("Unknown shot field")
    for k in ("anchor_prompt", "anchor_instructions"):
        if k in x and (not isinstance(x[k], str) or len(x[k]) > 20000):
            raise ValueError("Anchor instructions must be text of at most 20,000 characters")
    from . import render_models
    render_models.settings({**pipeline.shot_of(store.clip_get(id), sid), **x})
    if "render_mp" in x:
        media.validate_render_mp(x["render_mp"])
    for k, maximum in [("seed", 2**63 - 1), ("expansion", 256), ("subject_index", 100)]:
        if k in x and (not isinstance(x[k], int) or not 0 <= x[k] <= maximum):
            raise ValueError(f"Invalid {k}")
    for k, choices in [
        ("speaker", {"target", "other", "offscreen", "none"}),
        ("selection", {"largest", "leftmost", "rightmost", "index"}),
        ("mode", {"generate", "preserve"}),
    ]:
        if k in x and x[k] not in choices:
            raise ValueError(f"Invalid {k}")

    def mutate(c):
        s = pipeline.shot_of(c, sid)
        masks.preserve(s)
        if "mask_points" in x:
            masks.validate_points(x["mask_points"], s["end"] - s["start"])
            if x["mask_points"] != s.get("mask_points", []):
                s["mask_approved"] = False
        if "mask_version" in x:
            if set(x) != {"mask_version"}:
                raise ValueError("Select a mask version separately from other edits")
            masks.select(s, x["mask_version"], c["dimensions"])
            return
        if "anchor" in x and x["anchor"] not in [a["asset"] for a in s["anchors"]]:
            raise ValueError("Unknown anchor variant")
        if "selected_render" in x and x["selected_render"] not in [
            a["id"] for a in s["renders"]
        ]:
            raise ValueError("Unknown render variant")
        if x.get("anchor_approved") and not s.get("anchor"):
            raise ValueError("Generate or upload an anchor first")
        if x.get("mask_approved") and not s.get("mask"):
            raise ValueError("Track a mask first")
        if x.get("mask_approved") and masks.pending_frames({**s, **x}):
            raise ValueError("Apply the pending point corrections first")
        if x.get("mask_approved") and s.get("mask_empty_frames"):
            raise ValueError(
                "Mask has empty frames. Retrack with a clearer subject description or split around target absence."
            )
        if any(
            k in x and x[k] != s.get(k)
            for k in ["selection", "subject_index", "mask_text"]
        ):
            s.update(mask=None, mask_approved=False)
        if "anchor" in x and x["anchor"] != s["anchor"]:
            s["anchor_approved"] = False
        if "dialogue" in x and x["dialogue"] != s.get("dialogue"):
            s["dialogue_edited"] = True
        if "prompt" in x and x["prompt"] != s.get("prompt"):
            s["prompt_edited"] = True
        selection_changed = any(
            k in x and x[k] != s.get(k) for k in ["selected_render", "mode"]
        )
        s.update(x)
        if selection_changed:
            c["result"] = None

    return store.clip_mutate(id, mutate, body["revision"])


@app.get("/api/refine-defaults")
def refine_defaults():
    return refinement.defaults()


@app.patch("/api/refine-defaults")
def patch_refine_defaults(body: dict):
    return refinement.set_defaults(body)


@app.get("/api/clips/{id}/shots/{sid}/refine")
def refine_state(id: str, sid: str):
    c = store.clip_get(id)
    s = pipeline.shot_of(c, sid)
    candidates = [
        {k: v for k, v in p.items() if k != "path"} for p in refinement.catalog(c)
    ]
    return {
        "revision": c["revision"],
        "draft": refinement.draft(c, s),
        "defaults": refinement.defaults(),
        "candidates": candidates,
        "backplates": s.get("backplates", []),
        "finishes": s.get("finishes", []),
        "selected_finish": s.get("selected_finish"),
    }


@app.patch("/api/clips/{id}/shots/{sid}/refine")
def refine_patch(id: str, sid: str, body: dict):
    def update(c):
        refinement.patch(c, pipeline.shot_of(c, sid), body["changes"])

    with store.LOCK:
        current = store.clip_get(id)
        if current["revision"] != body["revision"]:
            base = body.get("base")
            saved = refinement.draft(current, pipeline.shot_of(current, sid))
            if not isinstance(base, dict) or base.get("binding") != saved["binding"]:
                raise store.Conflict(
                    "Foreground changed elsewhere. Your edits are kept; reload before saving."
                )
            for key, value in body["changes"].items():
                if key in {"source", "character"}:
                    stable = all(
                        k in base.get(key, {}) and saved[key].get(k) == base[key][k]
                        for k in value
                    )
                else:
                    stable = key in base and saved.get(key) == base[key]
                if not stable:
                    raise store.Conflict(
                        "These foreground settings changed elsewhere. Your local edits are kept."
                    )
        return store.clip_mutate(id, update, current["revision"])


@app.post("/api/clips/{id}/shots/{sid}/refine/foreground-version")
def refine_version(id: str, sid: str, body: dict):
    def update(c):
        s = pipeline.shot_of(c, sid)
        side = body.get("side")
        if side not in refinement.SIDES:
            raise ValueError("Unknown foreground side")
        version = next(
            (
                v
                for v in s.get("foreground_versions", [])
                if v["job"] == body.get("job") and v["side"] == side
            ),
            None,
        )
        if not version:
            raise ValueError("Unknown foreground version")
        binding = version["binding"]
        current = refinement.binding(c, s)
        if side == "source":
            binding = {k: v for k, v in binding.items() if not k.startswith("render_")}
            current = refinement.source_binding(c, s)
        if binding != current:
            raise ValueError("Foreground version belongs to different input frames")
        r = refinement.draft(c, s)
        f = r[side]
        for k in [
            "mask",
            "mask_base",
            "alpha",
            "alpha_proof",
            "alpha_seed",
            "face",
            "face_track",
            "face_proof",
            "gate_px",
            "core_px",
            "mask_text",
            "selection",
            "subject_index",
            "auto_face",
            "mask_proof",
            "mask_applied_points",
        ]:
            if k in version:
                f[k] = version[k]
        f["mask_points"] = f.get("mask_applied_points", [])
        f["reviewed"] = False
        f.update(
            masks.coverage(
                masks.read(f["mask"], s["end"] - s["start"], c["dimensions"])
            )
        )
        s["refine"] = r

    return store.clip_mutate(id, update, body["revision"])


@app.post("/api/clips/{id}/shots/{sid}/refine/source-mask")
def refine_source_mask(id: str, sid: str, body: dict):
    def update(c):
        s = pipeline.shot_of(c, sid)
        if not s.get("mask"):
            raise ValueError("Track the source person in Render first")
        r = refinement.draft(c, s)
        values = masks.read(s["mask"], s["end"] - s["start"], c["dimensions"])
        r["source"].update(
            mask=s["mask"],
            mask_base=s["mask"],
            mask_points=[],
            mask_applied_points=[],
            reviewed=False,
            **masks.coverage(values),
        )
        s["refine"] = r

    return store.clip_mutate(id, update, body["revision"])


@app.post("/api/clips/{id}/shots/{sid}/refine/select")
def refine_select(id: str, sid: str, body: dict):
    def update(c):
        s = pipeline.shot_of(c, sid)
        chosen = body.get("finish")
        if chosen is not None:
            f = next((f for f in s.get("finishes", []) if f["id"] == chosen), None)
            if not f or f["binding"] != refinement.binding(c, s):
                raise ValueError("This refinement belongs to an older source or render")
            f["reviewed"] = True
        s["selected_finish"] = chosen
        c["result"] = None

    return store.clip_mutate(id, update, body["revision"])


@app.get("/api/clips/{id}/shots/{sid}/refine/runs/{job_id}/thumbnail")
def refine_run_thumbnail(id: str, sid: str, job_id: str):
    import copy
    c = store.clip_get(id)
    s = pipeline.shot_of(c, sid)
    version = next((v for v in s.get("foreground_versions", []) if v["job"] == job_id), None)
    if not version:
        raise ValueError("Unknown foreground result")
    job = store.job_get(job_id)
    c, s = copy.deepcopy(job["data"]["snapshot"]), copy.deepcopy(job["data"]["shot"])
    r = refinement.draft(c, s)
    r[version["side"]].update({k:v for k,v in version.items() if k not in {"job","binding","side"}})
    s["refine"] = r
    path=MEDIA / "previews" / "refine-thumbnails" / (refinement.digest([id,sid,job_id])+".jpg")
    with MASK_FRAME_LOCK:
        if not path.exists():
            im = refinement.frame_image(c,s,version["side"],0,"checker" if r[version["side"]].get("alpha") else "sam")
            im.thumbnail((240,240))
            path.parent.mkdir(parents=True,exist_ok=True)
            im.save(path,format="JPEG",quality=80)
    return FileResponse(path,media_type="image/jpeg",headers={"Cache-Control":"private, max-age=31536000, immutable"})


@app.post("/api/clips/{id}/shots/{sid}/refine/runs/{job_id}/playback")
def refine_run_playback(id: str, sid: str, job_id: str, body: dict):
    import copy
    from . import refine_preview
    c=store.clip_get(id);s=pipeline.shot_of(c,sid)
    version=next((v for v in s.get("foreground_versions",[]) if v["job"]==job_id),None)
    if not version:raise ValueError("Unknown foreground result")
    job=store.job_get(job_id)
    c,s=copy.deepcopy(job["data"]["snapshot"]),copy.deepcopy(job["data"]["shot"])
    r=refinement.draft(c,s)
    r[version["side"]].update({k:v for k,v in version.items() if k not in {"job","binding","side"}})
    s["refine"]=r
    return refine_preview.prepare(c,s,version["side"],"comparison" if r[version["side"]].get("alpha") else "sam",body.get("retry",False))


@app.post("/api/clips/{id}/shots/{sid}/refine/playback")
def refine_playback(id: str, sid: str, body: dict):
    from . import refine_preview
    c = store.clip_get(id)
    return refine_preview.prepare(c, pipeline.shot_of(c, sid), body.get("side", "source"), body.get("view", "checker"), body.get("retry", False))


@app.get("/api/clips/{id}/shots/{sid}/refine/frame")
def refine_frame(
    id: str, sid: str, side: str = "source", frame: int = 0, view: str = "checker"
):
    c = store.clip_get(id)
    s = pipeline.shot_of(c, sid)
    with MASK_FRAME_LOCK:
        im = refinement.frame_image(c, s, side, frame, view)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return Response(
        buf.getvalue(), media_type="image/png", headers={"Cache-Control": "no-store"}
    )


@app.post("/api/clips/{id}/shots/{sid}/refine/backplate")
def upload_backplate(
    id: str,
    sid: str,
    revision: int = Form(...),
    timeline: str = Form("shot"),
    file: UploadFile = File(...),
):
    c = store.clip_get(id)
    s = pipeline.shot_of(c, sid)
    if c["revision"] != revision:
        raise store.Conflict("Clip changed; reload before importing a backplate")
    if timeline not in {"shot", "clip"}:
        raise ValueError("Choose a shot or full-clip timeline")
    check_storage(3)
    d = MEDIA / "batches" / c["batch_id"] / id / "backplates" / store.uid()
    d.mkdir(parents=True)
    dest = d / ("upload" + Path(file.filename or "plate.mkv").suffix)
    with dest.open("wb") as out:
        shutil.copyfileobj(file.file, out)
    result = refinement.plate_record(
        c,
        s,
        store.asset_add(dest),
        d,
        {"kind": "uploaded", "name": Path(file.filename or "").name},
        s["start"] if timeline == "clip" else 0,
    )

    def update(v):
        sh = pipeline.shot_of(v, sid)
        sh.setdefault("backplates", []).append(result)
        r = refinement.draft(v, sh)
        r.update(plate_id=result["id"], plate_reviewed=False)
        sh["refine"] = r

    return store.clip_mutate(id, update, revision)


@app.get("/api/clips/{id}/shots/{sid}/renders/{rid}")
def render_details(id: str, sid: str, rid: str):
    s = pipeline.shot_of(store.clip_get(id), sid)
    r = next((r for r in s["renders"] if r["id"] == rid), None)
    if r is None:
        raise KeyError("Render not found")
    result = dict(r)
    if "prompt" not in result and result.get("api"):
        graph = json.loads(store.path(result["api"]).read_text())
        result["prompt"] = graph["801"]["inputs"]["prompt"]
    if not result.get("lora") and result.get("api"):
        from . import render_models

        result["lora"] = render_models.identify(json.loads(store.path(result["api"]).read_text()))
    if result.get("api") and any(k not in result for k in ("window_frames", "history_frames")):
        graph = json.loads(store.path(result["api"]).read_text())
        plan = graph.get("805", {}).get("inputs", {})
        for key, default in (("window_frames", 124), ("history_frames", 18)):
            result.setdefault(key, int(plan.get(key, default)))
    return result


@app.post("/api/clips/{id}/prompts")
def prompts(id: str, body: dict):
    def mutate(c):
        for s in c["shots"]:
            s["prompt"] = media.prompt(c, s)

    return store.clip_mutate(id, mutate, body["revision"])


@app.post("/api/jobs")
def queue(body: dict):
    id = pipeline.queue(
            body["kind"],
            body["clip_id"],
            body.get("shot_id"),
            body.get("worker", "fey"),
            body.get("options"),
            body.get("expected_render"),
        )
    return {"id": id, "job": store.public_job(store.job_get(id))}


@app.post("/api/jobs/{id}/cancel")
def cancel(id: str, body: dict = None):
    with pipeline.MUTEX:
        j = store.job_get(id)
        if j["state"] == "cancelling":
            return store.public_job(j)
        if j["state"] in {"queued", "waiting"}:
            from . import remote_shared
            if remote_shared.recoverable(j):
                # Restart may expose an accepted worker attempt as a queued
                # observation. Dispatch its saved identity to cancel/collect;
                # a local queued label is not backend terminal evidence.
                store.job_update(id, phase="Reconciling cancellation of the accepted attempt", cancel_requested=True)
            else:
                store.job_update(id, "cancelled", phase="Cancelled")
        elif (j["state"] == "running" and not j["data"].get("finalizing")
              and j["kind"] in {"mask", "mask_edit", "render", "refine_extract", "refine_mask", "refine_face", "backplate"}
              and pipeline.resource(j) in {j["data"].get("worker"), "prep", "cpu"}):
            if not (body or {}).get("confirm_running"):
                raise ValueError("Confirm stopping this running job")
            store.job_update(id, "cancelling", phase="Cancellation requested" if pipeline.resource(j) in {"prep", "cpu"} or j["data"].get("gpu_waiting") else "Stopping", cancel_requested=True)
        else:
            raise ValueError("This job cannot be stopped at its current stage")
    return store.public_job(store.job_get(id))


@app.post("/api/jobs/{id}/retry-offload")
def retry_render_offload(id: str):
    from . import render_models

    new_id = render_models.retry_offload(id)
    return {"id": new_id, "job": store.public_job(store.job_get(new_id))}


@app.post("/api/batches/{id}/queue")
def batch_queue(id: str, body: dict):
    ids = []
    errors = []
    kind = body["kind"]
    workers = list(remote.WORKERS) if body.get("worker") == "both" else [body.get("worker", next(iter(remote.WORKERS), "cpu"))]
    if not workers:
        if kind in remote_shared.GPU_KINDS:
            raise ValueError("Connect a generation worker before queueing these shots")
        workers = ["cpu"]
    for c in store.clips(id):
        shots = (
            [s for s in c["shots"] if s["mode"] == "generate"]
            if kind in {"anchor", "mask", "render"}
            else [None]
        )
        for s in shots:
            if (
                s
                and body.get("missing_only", True)
                and (
                    (kind == "anchor" and s["anchor"])
                    or (kind == "mask" and s["mask"])
                    or (kind == "render" and s["selected_render"])
                )
            ):
                continue
            try:
                options = None
                if kind == 'render':
                    from . import segments
                    items = segments.current(s)
                    options = {'model_offloading': body.get('model_offloading', True), 'segments': {
                        'mode':'all', 'segment_id':items[0]['id'], 'plan_revision':segments.plan_revision(s)}}
                ids.append(
                    pipeline.queue(
                        kind,
                        c["id"],
                        s["id"] if s else None,
                        workers[len(ids) % len(workers)],
                        options=options,
                    )
                )
            except ValueError as e:
                errors.append({"clip": c["name"], "reason": str(e)})
    return {"jobs": ids, "errors": errors,
            "job_records": [store.public_job(store.job_get(id)) for id in ids]}


@app.post("/api/import")
def imports(batch_id: str = Form(...), files: list[UploadFile] = File(...)):
    # Browser folder uploads deliberately ignore relative directory names.
    check_storage(max(5, len(files) * 3))
    d = MEDIA / "batches" / batch_id / "imports" / store.uid()
    d.mkdir(parents=True)
    results = []
    errors = []
    for file in files:
        name = Path(file.filename or "clip.mp4").name
        if Path(name).suffix.lower() not in {".mp4", ".mov", ".mkv", ".webm", ".avi"}:
            continue
        dest = d / (store.uid() + "-" + name)
        with dest.open("wb") as o:
            shutil.copyfileobj(file.file, o)
        try:
            results.append(media.import_clip(batch_id, dest, Path(name).stem))
        except Exception as e:
            errors.append({"file": name, "error": str(e)})
    return {"clips": results, "errors": errors}


@app.post("/api/import-folder")
def import_folder(body: dict):
    p = Path(body["path"]).resolve(strict=True)
    permitted = MEDIA if PORTABLE else SHARED_ROOT
    if not p.is_relative_to(permitted) or not p.is_dir():
        raise ValueError("Choose an existing folder within the configured media drive")
    check_storage(5)
    results = []
    errors = []
    for file in sorted(p.iterdir()):
        if not file.is_file() or file.suffix.lower() not in {
            ".mp4",
            ".mov",
            ".mkv",
            ".webm",
            ".avi",
        }:
            continue
        d = MEDIA / "batches" / body["batch_id"] / "imports" / store.uid()
        d.mkdir(parents=True)
        dst = d / file.name
        shutil.copy2(file, dst)
        try:
            results.append(media.import_clip(body["batch_id"], dst))
        except Exception as e:
            errors.append({"file": file.name, "error": str(e)})
    return {"clips": results, "errors": errors}


@app.post("/api/clips/{id}/image")
def upload_image(
    id: str,
    role: str = Form(...),
    revision: int = Form(...),
    shot_id: str = Form(""),
    file: UploadFile = File(...),
):
    if role not in {"reference", "anchor", "backplate_anchor"}:
        raise ValueError("Unknown image role")
    c = store.clip_get(id)
    if c["revision"] != revision:
        raise store.Conflict("Clip changed; reload first")
    d = MEDIA / "batches" / c["batch_id"] / id / "images"
    d.mkdir(parents=True, exist_ok=True)
    dest = d / (store.uid() + ".png")
    with Image.open(file.file) as im:
        im.convert("RGB").save(dest)
    asset = store.asset_add(dest)

    def mutate(v):
        if role == "reference":
            v["reference"] = asset
            for s in v["shots"]:
                s.update(anchor_approved=False)
        elif role == "backplate_anchor":
            s = pipeline.shot_of(v, shot_id)
            s.setdefault("backplate_anchors", []).append(
                {"id": store.uid(), "asset": asset, "generator": "Manual import",
                 "provider": {"id": "manual", "revision": 1},
                 "source_sha256": store.asset(s["source_frame"])["sha256"]}
            )
            r = refinement.draft(v, s)
            r.update(anchor=asset, anchor_reviewed=False)
            s["refine"] = r
        else:
            s = pipeline.shot_of(v, shot_id)
            s["anchors"].append(
                {"id": store.uid(), "asset": asset, "generator": "Manual import",
                 "provider": {"id": "manual", "revision": 1},
                 "source_sha256": store.asset(s["source_frame"])["sha256"],
                 "reference_sha256": store.asset(v["reference"])["sha256"] if v.get("reference") else None}
            )
            s.update(anchor=asset, anchor_approved=False)

    return store.clip_mutate(id, mutate, revision)


THUMBNAIL_LOCK = threading.Lock()
THUMBNAIL_KEYS = {}


@app.get("/api/assets/{id}/preview")
def still_preview(id: str, size: int = 960):
    from . import image_previews

    return FileResponse(image_previews.prepare(id, size), media_type="image/webp",
                        headers={"Cache-Control": "private, max-age=31536000, immutable"})


@app.get("/api/assets/{id}/thumbnail")
def asset_thumbnail(id: str):
    a = store.asset(id)
    if not a["mime"].startswith("video/"):
        raise ValueError("Thumbnails require a video asset")
    directory = MEDIA / "thumbnails"
    directory.mkdir(exist_ok=True)
    dest = directory / (a["sha256"] + ".jpg")
    with THUMBNAIL_LOCK:
        key_lock = THUMBNAIL_KEYS.setdefault(a["sha256"], threading.Lock())
    with key_lock:
        if not dest.exists():
            pending = dest.with_suffix(".pending.jpg")
            try:
                media.ff(
                    [
                        "-i",
                        a["path"],
                        "-vf",
                        "scale=512:-2",
                        "-frames:v",
                        "1",
                        "-q:v",
                        "3",
                        pending,
                    ]
                )
                pending.replace(dest)
            finally:
                pending.unlink(missing_ok=True)
    return FileResponse(
        dest,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=31536000, immutable"},
    )


@app.get("/api/assets/{id}")
def asset(id: str, download: bool = False):
    a = store.asset(id)
    return FileResponse(
        a["path"], media_type=a["mime"], filename=a["name"] if download else None
    )


MASK_FRAME_LOCK = threading.Lock()


@app.post("/api/clips/{id}/shots/{sid}/mask-playback")
def mask_playback(id: str, sid: str, body: dict):
    if set(body) - {"retry"} or not isinstance(body.get("retry", False), bool):
        raise ValueError("Invalid preview request")
    c = store.clip_get(id)
    return previews.prepare_mask(c, pipeline.shot_of(c, sid), body.get("retry", False))


@app.get("/api/clips/{id}/shots/{sid}/mask")
def mask(
    id: str,
    sid: str,
    frame: int = 0,
    padding: int | None = None,
    view: str = "overlay",
    preview: bool = False,
    source_asset: str | None = Query(None, alias="source"),
    mask_asset: str | None = Query(None, alias="mask"),
    dimensions: str | None = None,
    range_key: str | None = Query(None, alias="range"),
):
    c = store.clip_get(id)
    s = pipeline.shot_of(c, sid)
    if view not in {"overlay", "source", "layer"}:
        raise ValueError("Unknown mask view")
    padding = s["expansion"] if padding is None else padding
    if not 0 <= padding <= 256:
        raise ValueError("Padding must be between 0 and 256 pixels")
    index = max(0, min(frame, s["end"] - s["start"] - 1))
    if preview:
        # Never cache a newer selection under an older immutable browser URL.
        if dimensions and dimensions != "x".join(map(str, c["dimensions"])):
            raise ValueError("Preview dimensions changed; reload the viewer")
        if range_key and range_key != f"{s['start']}-{s['end']}":
            raise ValueError("Shot range changed; reload the viewer")
        if view == "source":
            if source_asset and source_asset != (c.get("render_source") or c["source"]):
                raise ValueError("Source changed; reload the viewer")
            return FileResponse(
                previews.source_frame(c, s, index),
                media_type="image/jpeg",
                headers={
                    "Cache-Control": "private, max-age=31536000, immutable"
                    if source_asset and range_key and dimensions
                    else "private, no-cache"
                },
            )
        if view == "layer":
            if mask_asset and mask_asset != (s.get("mask") or "none"):
                raise ValueError("Mask changed; reload the viewer")
            return FileResponse(
                previews.mask_layer(s, c["dimensions"], index, padding),
                media_type="image/png",
                headers={
                    "Cache-Control": "private, max-age=31536000, immutable"
                    if mask_asset and dimensions
                    else "private, no-cache"
                },
            )
        raise ValueError("Compact previews use separate source and layer views")
    w, h = c["dimensions"]
    cache = MEDIA / "batches" / c["batch_id"] / id / "mask-previews"
    cache.mkdir(parents=True, exist_ok=True)
    source = c.get("render_source") or c["source"]
    base = cache / f"{source}-{s['start'] + index}-{w}x{h}.png"
    if view != "layer":
        with MASK_FRAME_LOCK:
            if not base.exists():
                pending = base.with_suffix(".pending.png")
                media.ff(
                    [
                        "-i",
                        store.path(source),
                        "-vf",
                        f"select=eq(n\\,{s['start'] + index}),scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1",
                        "-frames:v",
                        "1",
                        pending,
                    ]
                )
                pending.replace(base)
        if view == "source":
            return FileResponse(
                base,
                media_type="image/png",
                headers={"Cache-Control": "private, max-age=3600"},
            )
    m = masks.read(s.get("mask"), s["end"] - s["start"], c["dimensions"])[index]
    if padding and m.any():
        m = distance_transform_edt(~m) <= padding
    buf = io.BytesIO()
    if view == "layer":
        arr = np.zeros((h, w, 4), dtype=np.uint8)
        arr[m] = [62, 149, 243, 140]
        Image.fromarray(arr).save(buf, "PNG")
        return Response(
            buf.getvalue(),
            media_type="image/png",
            headers={"Cache-Control": "no-store"},
        )
    arr = np.asarray(Image.open(base).convert("RGB")).copy()
    arr[m] = (arr[m] * 0.5 + np.array([62, 149, 243]) * 0.5).astype("uint8")
    Image.fromarray(arr).save(buf, "JPEG", quality=90)
    return Response(
        buf.getvalue(), media_type="image/jpeg", headers={"Cache-Control": "no-store"}
    )


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "appId": "h3-workbench",
        "instanceId": store.setting("app_instance_id"),
        "revision": os.environ.get("H3_WORKBENCH_PACKAGE_REVISION"),
        "shared_mounted": bool(MEDIA_MOUNT and MEDIA_MOUNT.is_mount()),
        "jobs_running": len(pipeline.ACTIVE),
    }


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.get("/")
def index():
    return HTMLResponse(
        (ROOT / "static/index.html")
        .read_text()
        .replace('href="/h3-workbench/"', f'href="{BASE_PATH}/"')
    )
