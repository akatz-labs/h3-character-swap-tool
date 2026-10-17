import json, subprocess, shlex, shutil, time, hashlib
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from . import store, media
from .config import WORKERS, MEDIA, ROOT, PILOT, SHARED_ROOT


class Cancelled(RuntimeError):
    pass


def check_cancel(job):
    if store.job_get(job["id"])["data"].get("cancel_requested"):
        raise Cancelled("Cancelled by user")


class Busy(RuntimeError):
    pass


def remote_path(path, worker):
    relative = Path(path).resolve().relative_to(SHARED_ROOT)
    return str(Path(WORKERS[worker]["shared"]) / relative)


def ssh(worker, code, timeout=60):
    return media.run(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=8",
            WORKERS[worker]["ssh"],
            "python3 -",
        ],
        input=code,
        timeout=timeout,
    )


def status(worker, owned=True):
    try:
        x = json.loads(
            ssh(
                worker,
                """import subprocess,json
from pathlib import Path
def r(a):return subprocess.check_output(a,text=True).strip()
runtimes=[]
for p in Path('/proc').iterdir():
    if not p.name.isdigit():continue
    try:
        if 'main.py' in (p/'cmdline').read_bytes().decode().split('\\0'):runtimes.append(int(p.name))
    except (FileNotFoundError,PermissionError,ProcessLookupError,UnicodeDecodeError):pass
print(json.dumps({'runtimes':runtimes,'compute':r(['nvidia-smi','--query-compute-apps=pid,process_name,used_memory','--format=csv,noheader,nounits']),'gpu':r(['nvidia-smi','--query-gpu=name,memory.total,memory.used,utilization.gpu','--format=csv,noheader,nounits'])}))""",
                20,
            )
        )
        result = {
            "worker": worker,
            "state": "busy" if x["compute"] or x["runtimes"] else "available",
            **x,
        }
        if owned:
            from . import remote_sessions
            if remote_sessions.enabled():
                session = remote_sessions.status(worker)
                if session:
                    own_pid = session.get("runtime_pid")
                    foreign_compute = any(row.split(",", 1)[0].strip() != str(own_pid) for row in x["compute"].splitlines())
                    foreign_runtime = any(pid != own_pid for pid in x["runtimes"])
                    if own_pid and (foreign_compute or foreign_runtime):
                        return {**result, **session, "state": "external_busy"}
                    return {**result, **session}
        return result
    except Exception as e:
        return {"worker": worker, "state": "offline", "error": str(e)[-300:]}


def all_status():
    with ThreadPoolExecutor(2) as p:
        return dict(zip(WORKERS, p.map(status, WORKERS)))


def workbench_node_bundle():
    sources = sorted((ROOT / "worker_nodes/h3_workbench_masks").glob("*.py"))
    digest = hashlib.sha256(b"".join(f.name.encode() + b"\0" + f.read_bytes() for f in sources)).hexdigest()
    destination = MEDIA / "runtime" / "mask-refine" / digest
    destination.mkdir(parents=True, exist_ok=True)
    for source in sources:
        target = destination / source.name
        if not target.exists():
            shutil.copy2(source, target)
        if target.read_bytes() != source.read_bytes():
            raise RuntimeError("Immutable workbench node bundle changed")
    return digest, destination


def ensure_workbench_nodes(worker, directory):
    """Install a frozen node bundle only after the dispatcher's idle check."""
    digest, bundle = workbench_node_bundle()
    home = WORKERS[worker]["home"]
    target = remote_path(bundle, worker)
    receipt = json.loads(ssh(worker, f"""import json,subprocess,hashlib
from pathlib import Path
root=Path({home!r})/'comfygit/environments/h3-character-swap/ComfyUI/custom_nodes/h3_workbench_masks'
target=Path({target!r})
actual=hashlib.sha256(b''.join(p.name.encode()+b'\\0'+p.read_bytes() for p in sorted(target.glob('*.py')))).hexdigest()
assert actual=={digest!r}, 'Workbench node bundle drift'
previous=str(root.resolve())
if previous != str(target.resolve()):
 for process in Path('/proc').iterdir():
  if not process.name.isdigit(): continue
  try: args=(process/'cmdline').read_bytes().decode().split('\\0')
  except (OSError,UnicodeDecodeError): continue
  assert 'main.py' not in args, 'Worker became busy before node installation'
 completed=subprocess.run([{str(Path(home)/'.local/bin/cg')!r},'-e','h3-character-swap','node','dev-link','h3_workbench_masks','--path',str(target),'--force'],capture_output=True,text=True)
 assert completed.returncode==0, completed.stderr or completed.stdout
assert root.resolve()==target.resolve(), 'Workbench node link mismatch'
print(json.dumps({{'previous':previous,'target':str(target.resolve()),'sha256':actual}}))
"""))
    (directory / "workbench-node-install.json").write_text(json.dumps(receipt, indent=2))
    return digest


def execute(job, graph, files, d, profile="legacy"):
    if job["data"].get("execution_backend") == "shared":
        from . import remote_shared
        return remote_shared.execute(job, graph, files, d, profile)
    if any(n["class_type"].startswith(("H3CharacterSwap", "H3Relay")) for n in graph.values()):
        raise RuntimeError("This character-swap graph requires the matching Relay worker recipe through the shared executor. Upgrade the app and worker together.")
    from . import remote_sessions
    if remote_sessions.enabled():
        return remote_sessions.execute(job, graph, files, d, profile)
    check_cancel(job)
    from . import remote_warm
    if remote_warm.compatible(job):
        return remote_warm.execute(job, graph, files, d)
    remote_warm.close(job["data"]["worker"])
    if profile not in {
        "legacy",
        "full",
        "alpha",
        "alpha-offload",
        "full-offload",
        "full-stream",
        "full-layer-stream",
    }:
        raise ValueError("Unknown runtime profile")
    # Resume only a completed, released stage with its identical immutable graph.
    prior = d / "runtime"
    if (prior / "attempt.json").exists():
        identical = (d / "api.json").exists() and json.loads(
            (d / "api.json").read_text()
        ) == graph
        if (
            identical
            and (prior / "complete.json").exists()
            and (prior / "release.json").exists()
        ):
            receipt = json.loads((prior / "release.json").read_text())
            if receipt.get("stopped") and receipt.get("gpu_released"):
                return prior
        raise RuntimeError(
            "Existing worker attempt needs inspection; create a new job after reviewing its receipt"
        )
    worker = job["data"]["worker"]
    w = WORKERS[worker]
    live = status(worker)
    if live["state"] != "available":
        raise Busy(f"{worker}: {live['state']}. Waiting for available GPU.")
    node_sha = None
    if any(n["class_type"].startswith("H3Workbench") for n in graph.values()):
        node_sha = ensure_workbench_nodes(worker, d)
    if job["kind"] == "render":
        from . import render_models
        model = render_models.identify(graph)
        if model is None:
            raise ValueError("Unknown character-swap checkpoint in generation graph")
        verified = render_models.verify_worker(worker, model)
        (d / "generation-model.json").write_text(json.dumps({"lora": model, "verified": verified}, indent=2))
    for name, path in files.items():
        if "/" in name:
            raise ValueError("Invalid staged input name")
    # All names are generated by this service. The remote interpreter receives data literals.
    env_relative = (
        ".local/share/akatz/comfygit/environments/h3-alpha"
        if profile.startswith("alpha")
        else "comfygit/environments/h3-character-swap"
    )
    mapping = {name: remote_path(path, worker) for name, path in files.items()}
    code = f"import shutil,json,hashlib\nfrom pathlib import Path\nfiles=json.loads({json.dumps(json.dumps(mapping))})\nroot=Path({w['home']!r})/{(env_relative + '/ComfyUI/input')!r}\nfor name,src in files.items():\n p=root/name\n if p.exists():\n  assert hashlib.sha256(p.read_bytes()).digest()==hashlib.sha256(Path(src).read_bytes()).digest(), 'Input name collision'\n else: shutil.copy2(src,p)\n assert hashlib.sha256(p.read_bytes()).digest()==hashlib.sha256(Path(src).read_bytes()).digest()\nprint('staged')"
    ssh(worker, code)
    graphfile = d / "api.json"
    graphfile.write_text(json.dumps(graph, indent=2))
    script = d / "remote_worker.py"
    shutil.copy2(ROOT / "scripts/remote_worker.py", script)
    script.write_text(script.read_text().replace(str(SHARED_ROOT) + "/", WORKERS[worker]["shared"].rstrip("/") + "/"))
    out = d / "runtime"
    out.mkdir(exist_ok=True)
    args = [
        "python3",
        remote_path(script, worker),
        worker,
        job["id"],
        job["kind"],
        remote_path(graphfile, worker),
        remote_path(out, worker),
        str(w["port"]),
    ]
    args += ["--profile", profile]
    if profile.startswith("alpha"):
        contract = d / "runtime-contract.json"
        shutil.copy2(ROOT / "templates" / f"alpha-runtime-{worker}.json", contract)
        args += ["--contract", remote_path(contract, worker)]
    if node_sha:
        args += [
            "--mask-node-sha256",
            node_sha,
        ]
    store.job_update(
        job["id"],
        phase="Starting ComfyUI",
        runtime=str(out),
        api=store.asset_add(graphfile),
    )
    with (d / "dispatch.log").open("w") as log:
        p = subprocess.Popen(
            ["ssh", "-o", "BatchMode=yes", w["ssh"], shlex.join(args)],
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        cancellation_sent = False
        while p.poll() is None:
            if store.job_get(job["id"])["data"].get("cancel_requested"):
                if not cancellation_sent:
                    # Write on the worker, avoiding NFS negative-entry cache delays.
                    marker = remote_path(out / "cancel.json", worker)
                    try:
                        ssh(worker, f"from pathlib import Path\np=Path({marker!r})\nt=p.with_suffix('.pending')\nt.write_text({json.dumps({'job': job['id']})!r})\nt.replace(p)\nprint('requested')", 20)
                        cancellation_sent = True
                    except Exception as error:
                        store.job_update(job["id"], stop_warning=str(error)[-300:])
                store.job_update(job["id"], phase="Stopping owned runtime")
            st = out / "status.json"
            if st.exists() and not store.job_get(job["id"])["data"].get("cancel_requested"):
                try:
                    store.job_update(
                        job["id"],
                        phase="Rendering"
                        if job["kind"] == "render"
                        else "Extracting alpha"
                        if profile.startswith("alpha")
                        else "Generating clean backplate"
                        if job["kind"] == "backplate"
                        else "Tracking face"
                        if job["kind"] == "refine_face"
                        else "Tracking mask",
                        runtime_status=json.loads(st.read_text()),
                    )
                except json.JSONDecodeError:
                    pass
            time.sleep(3)
    if (out / "cancelled.json").exists():
        release = json.loads((out / "release.json").read_text())
        if not release.get("stopped") or not release.get("gpu_released"):
            raise RuntimeError("Stop requested but worker release needs inspection")
        raise Cancelled("Owned runtime stopped and GPU released")
    if p.returncode:
        err = out / "failure.json"
        raise RuntimeError(
            json.loads(err.read_text())["error"]
            if err.exists()
            else (d / "dispatch.log").read_text()[-2000:]
        )
    release = json.loads((out / "release.json").read_text())
    if not release.get("stopped") or not release.get("gpu_released"):
        raise RuntimeError("Output completed but worker release needs inspection")
    return out


def graph_render(clip, shot, job, d):
    from . import render_models, relay_contract
    config = render_models.settings(job["data"].get("generation") or shot)
    g = relay_contract.recipe(config["use_anchor"], config["use_mask"])
    sid = job["id"]
    spec = job["data"].get("render_spec") or media.render_spec(clip, shot)
    source = media.shot_media(clip, shot, d, dimensions=spec["dimensions"])
    count = shot["end"] - shot["start"]
    g["805"]["inputs"].update(source_frames=count, seed=shot["seed"],
        window_frames=str(config["window_frames"]), history_frames=str(config["history_frames"]))
    model = render_models.LORAS[config["lora_id"]]
    if model["filename"]:
        g["850"]["inputs"]["lora_name"] = model["filename"]
    else:
        del g["850"]
        for key in ("guider", "schedule"):
            g[key]["inputs"]["model"] = ["4", 0]
    files = {f"{sid}-source.mkv": source, f"{sid}-reference.png": store.path(clip["reference"])}
    g["9"]["inputs"]["image"] = f"{sid}-reference.png"
    if config["use_anchor"]:
        files[f"{sid}-anchor.png"] = store.path(shot["anchor"])
        g["309"]["inputs"]["image"] = f"{sid}-anchor.png"
    if config["use_mask"]:
        mask_path = store.path(shot["mask"])
        if spec["dimensions"] != clip["dimensions"]:
            import numpy as np
            from . import masks
            mask_path = d / "generation-mask.npz"
            np.savez_compressed(mask_path, mask=masks.read(shot["mask"], count, spec["dimensions"]))
        files[f"{sid}-mask.npz"] = mask_path
        g["43"]["inputs"].update(mask_file=f"{sid}-mask.npz", expansion_px=spec["expansion"], source_frames=count)
    g["30"]["inputs"]["file"] = f"{sid}-source.mkv"
    # Staging already supplies exact 24fps, 32px-grid pixels and source audio.
    g["31"] = {"class_type": "GetVideoComponents", "inputs": {"video": ["30", 0]}}
    g["803"]["inputs"]["fps"] = ["31", 2]
    g["801"]["inputs"]["prompt"] = shot["prompt"]
    g["809"]["inputs"]["filename_prefix"] = sid
    return g, files


def graph_mask(clip, shot, job, d):
    sid = job["id"]
    source = media.shot_media(clip, shot, d)

    def n(t, **i):
        return {"class_type": t, "inputs": i}

    g = {
        "30": n("LoadVideo", file=f"{sid}-source.mkv"),
        "31": n(
            "H3RelayPrepareSource",
            resolution="768",
            duration_seconds=0,
            video=["30", 0],
        ),
        "32": n("ImageFromBatch", image=["31", 0], batch_index=0, length=1),
        "33": n(
            "CheckpointLoaderSimple", ckpt_name="sam3.1_multiplex_fp16.safetensors"
        ),
        "34": n("CLIPTextEncode", text=shot.get("mask_text", "person"), clip=["33", 1]),
        "35": n(
            "SAM3_Detect",
            model=["33", 0],
            image=["32", 0],
            conditioning=["34", 0],
            threshold=0.35,
            refine_iterations=2,
            individual_masks=True,
        ),
        "36": n(
            "H3RelaySelectMask",
            masks=["35", 0],
            selection=shot["selection"],
            index=shot.get("subject_index", 0),
        ),
        "37": n(
            "SAM3_VideoTrack",
            model=["33", 0],
            images=["31", 0],
            initial_mask=["36", 0],
            conditioning=["34", 0],
            detection_threshold=0.5,
            max_objects=1,
            detect_interval=1,
        ),
        "38": n("SAM3_TrackToMask", track_data=["37", 0], object_indices=""),
        "39": n(
            "H3RelaySaveMask",
            masks=["38", 0],
            filename_prefix="person-mask",
            frames=shot["end"] - shot["start"],
        ),
    }
    return g, {f"{sid}-source.mkv": source}


def graph_mask_recovery(shot, job, missing):
    sid = job["id"]

    def n(t, **i):
        return {"class_type": t, "inputs": i}

    g = {
        "30": n("LoadVideo", file=f"{sid}-source.mkv"),
        "31": n(
            "H3RelayPrepareSource",
            resolution="768",
            duration_seconds=0,
            video=["30", 0],
        ),
        "33": n(
            "CheckpointLoaderSimple", ckpt_name="sam3.1_multiplex_fp16.safetensors"
        ),
        "34": n("CLIPTextEncode", text=shot.get("mask_text", "person"), clip=["33", 1]),
    }
    for f in missing:
        key = str(1000 + f * 10)
        detect = str(1001 + f * 10)
        selected = str(1002 + f * 10)
        save = str(1003 + f * 10)
        g[key] = n("ImageFromBatch", image=["31", 0], batch_index=f, length=1)
        g[detect] = n(
            "SAM3_Detect",
            model=["33", 0],
            image=[key, 0],
            conditioning=["34", 0],
            threshold=0.35,
            refine_iterations=2,
            individual_masks=True,
        )
        g[selected] = n(
            "H3RelaySelectMask",
            masks=[detect, 0],
            selection=shot["selection"],
            index=shot.get("subject_index", 0),
        )
        g[save] = n(
            "H3RelaySaveMask",
            masks=[selected, 0],
            filename_prefix=f"recovered-{f:04}",
            frames=1,
        )
    return g
