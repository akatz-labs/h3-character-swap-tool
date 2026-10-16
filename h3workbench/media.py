import json, subprocess, math, re, copy
from fractions import Fraction
from pathlib import Path
from . import store
from .config import MEDIA


def run(args, **kwargs):
    p = subprocess.run([str(x) for x in args], capture_output=True, text=True, **kwargs)
    if p.returncode:
        raise RuntimeError(p.stderr[-5000:] or p.stdout[-5000:])
    return p.stdout


def ff(args):
    return run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", *args]
    )


def probe(p):
    x = json.loads(
        run(
            [
                "ffprobe",
                "-v",
                "error",
                "-count_frames",
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                p,
            ]
        )
    )
    v = next(s for s in x["streams"] if s["codec_type"] == "video")
    for key in ["avg_frame_rate", "r_frame_rate"]:
        try:
            fps = float(Fraction(v.get(key) or "0/0"))
        except (ValueError, ZeroDivisionError):
            continue
        if math.isfinite(fps) and fps > 0:
            break
    else:
        raise ValueError("Video has no valid reported frame rate")
    return {
        "width": v["width"],
        "height": v["height"],
        "fps": fps,
        "frames": int(v.get("nb_read_frames") or v.get("nb_frames")),
        "duration": float(x["format"]["duration"]),
        "audio": any(s["codec_type"] == "audio" for s in x["streams"]),
    }


RENDER_BUDGETS = {0.5: "480", 1.0: "768"}


def validate_render_mp(value):
    if (
        type(value) not in {int, float}
        or not math.isfinite(value)
        or not 0.1 <= value <= 1.0
    ):
        raise ValueError("Choose a render size between 0.1 and 1.0 MP")
    return value


def canvas(w, h, budget=1000000):
    if not all(math.isfinite(v) and v > 0 for v in (w, h)):
        raise ValueError("Invalid source dimensions")
    iw, ih = math.sqrt(budget * w / h), math.sqrt(budget * h / w)
    options = []
    for x in range(32, min(16384, budget // 32) + 1, 32):
        top = min(16384, budget // x // 32 * 32)
        for y in {
            max(32, min(top, int(ih // 32) * 32)),
            max(32, min(top, math.ceil(ih / 32) * 32)),
        }:
            options.append(
                (math.log(x / iw) ** 2 + math.log(y / ih) ** 2, -x * y, x, y)
            )
    _, _, x, y = min(options)
    return [x, y]


def render_spec(clip, shot):
    mp = validate_render_mp(shot.get("render_mp", 1.0))
    # Legacy shots keep their exact canvas. Lower-budget variants use the same
    # normalized framing as their existing mask and anchor, on H3's 32px grid.
    dimensions = (
        clip["dimensions"]
        if mp == 1.0
        else canvas(*clip["dimensions"], budget=int(mp * 1000000))
    )
    scale = math.sqrt(math.prod(dimensions) / math.prod(clip["dimensions"]))
    return {
        "render_mp": mp,
        "resolution": RENDER_BUDGETS.get(mp, "custom"),
        "dimensions": dimensions,
        "actual_mp": math.prod(dimensions) / 1000000,
        "expansion": round(shot.get("expansion", 64) * scale),
    }


def frame(src, n, dest):
    ff(["-i", src, "-vf", f"select=eq(n\\,{n})", "-frames:v", "1", dest])
    return store.asset_add(dest)


def normalize(src, dest):
    info = probe(src)
    if info["duration"] > 3592 / 24:
        raise ValueError("Trim this clip to 149.66 seconds or less before import.")
    master = dest.with_name("source-master.mkv")
    ff(
        [
            "-i",
            src,
            "-map",
            "0:v:0",
            "-map",
            "0:a:0?",
            "-vf",
            "fps=24",
            "-c:v",
            "ffv1",
            "-c:a",
            "pcm_s16le",
            master,
        ]
    )
    ff(
        [
            "-i",
            master,
            "-map",
            "0:v:0",
            "-map",
            "0:a:0?",
            "-c:v",
            "libx264",
            "-crf",
            "16",
            "-preset",
            "fast",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            dest,
        ]
    )
    return {**probe(dest), "render_source": store.asset_add(master)}


def import_clip(batch, src, name=None):
    src = Path(src).resolve(strict=True)
    if not src.is_relative_to(MEDIA.resolve()):
        raise ValueError("Copy imports to the configured workbench media directory first")
    id = store.uid("clip-")
    d = MEDIA / "batches" / batch / id
    d.mkdir(parents=True)
    out = d / "source.mp4"
    info = normalize(src, out)
    poster = frame(out, 0, d / "poster.jpg")
    c = store.clip_create(
        batch,
        {
            "id": id,
            "name": name or src.stem,
            "source": store.asset_add(out),
            "original": store.asset_add(src),
            "poster": poster,
            **info,
            "dimensions": canvas(info["width"], info["height"]),
            "reference": None,
            "character": "",
            "shots": [],
            "cuts_approved": False,
            "transcript": None,
            "result": None,
        },
    )
    return set_cuts(c, [], reviewed=True)


def dialogue(clip, start, end):
    words = (clip.get("transcript") or {}).get("words", [])
    return "".join(
        w["word"] for w in words if start / 24 <= (w["start"] + w["end"]) / 2 < end / 24
    ).strip()


def prompt(clip, shot):
    speech = {
        "target": "The selected character is speaking.",
        "offscreen": "Speech is offscreen; do not make the selected character speak.",
        "other": "Another person is speaking; preserve the selected character’s actual mouth performance.",
        "none": "No dialogue; preserve the visible source performance.",
    }[shot.get("speaker", "target")]
    return f'Replace only the selected person in <Video 1> with the single character in <Picture 1>: {clip.get("character") or "the reference character"}. <Picture 2> is the exact first-frame scene and pose anchor. Keep the reference identity and costume throughout. Match source posture, gaze, blinks, facial emotion, mouth movement, hand contacts and camera movement. {speech} Current shot dialogue: "{shot.get("dialogue", "")}". <Audio 1> and source video determine exact timing. Do not repeat earlier dialogue or invent words, gestures, people or camera cuts. Retain the scene outside the person region. Output the raw decoded generation.'


def set_cuts(clip, cuts, modes=None, reviewed=False):
    if not isinstance(cuts, list) or any(type(n) is not int for n in cuts):
        raise ValueError("Cut positions must be whole frame numbers")
    cuts = sorted(set(cuts))
    if any(n <= 0 or n >= clip["frames"] for n in cuts):
        raise ValueError("Cuts must fall inside the clip")
    bounds = [0, *cuts, clip["frames"]]
    if modes is not None and (not isinstance(modes, list) or len(modes) != len(bounds)-1
                              or any(not isinstance(m, str) or m not in {"generate", "preserve"} for m in modes)):
        raise ValueError("Choose Replace character or Keep original for each shot")
    if not reviewed:
        return store.clip_mutate(clip["id"], lambda c: c.update(
            shot_layout_draft={"cuts": cuts, "modes": modes}), clip["revision"])
    layout = [(a, b, modes[i] if modes else next((s["mode"] for s in clip["shots"]
               if (s["start"], s["end"]) == (a, b)), "generate"))
              for i, (a, b) in enumerate(zip(bounds, bounds[1:]))]
    changed = layout != [(s["start"], s["end"], s["mode"]) for s in clip["shots"]]
    if changed and any(j["clip_id"] == clip["id"] for j in
                       store.pending_jobs(("queued", "waiting", "running", "cancelling"))):
        raise store.Conflict("Wait for this clip's active jobs before confirming a new layout")
    old = {(s["start"], s["end"]): s for s in clip["shots"]}
    shots = []
    d = MEDIA / "batches" / clip["batch_id"] / clip["id"] / store.uid("cuts-")
    d.mkdir(parents=True)
    for a, b in zip(bounds, bounds[1:]):
        if (a, b) in old:
            shots.append(copy.deepcopy(old[a, b]))
            continue
        sid = store.uid("shot-")
        s = {
            "id": sid,
            "start": a,
            "end": b,
            "mode": "generate",
            "source_frame": frame(
                store.path(clip.get("render_source") or clip["source"]),
                a,
                d / f"{a}.png",
            ),
            "speaker": "target" if clip["audio"] else "none",
            "dialogue": dialogue(clip, a, b),
            "seed": 194729,
            "render_mp": 1.0,
            "lora_id": "v2-1000",
            "use_anchor": True,
            "use_mask": True,
            "expansion": 64,
            "selection": "largest",
            "subject_index": 0,
            "mask": None,
            "mask_approved": False,
            "anchor": None,
            "anchor_approved": False,
            "anchors": [],
            "renders": [],
            "selected_render": None,
        }
        s["prompt"] = prompt(clip, s)
        shots.append(s)
    if modes is not None:
        for s, mode in zip(shots, modes):
            s["mode"] = mode
    changed = [(s["start"], s["end"], s["mode"]) for s in shots] != [
        (s["start"], s["end"], s["mode"]) for s in clip["shots"]]
    def apply(c):
        if changed and c["shots"]:
            c.setdefault("shot_layout_history", []).append({
                "id": store.uid("layout-"), "created": store.now(),
                "revision": clip["revision"], "shots": copy.deepcopy(c["shots"]),
                "result": c.get("result"),
            })
        c.update(shots=shots, cuts_approved=True)
        c.pop("shot_layout_draft", None)
        if changed:
            c["result"] = None
    return store.clip_mutate(clip["id"], apply, clip["revision"])


def detect_cuts(clip):
    p = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-i",
            str(store.path(clip["source"])),
            "-vf",
            "select='gt(scene,0.32)',showinfo",
            "-an",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
    )
    if p.returncode:
        raise RuntimeError(p.stderr[-1500:])
    return sorted(
        set(
            round(float(t) * 24)
            for t in re.findall(r"pts_time:([\d.]+)", p.stderr)
            if 0 < round(float(t) * 24) < clip["frames"]
        )
    )


def frozen_file(target, recipe, build):
    """Retain exact prepared bytes when reconciling an immutable job."""
    target = Path(target)
    manifest = target.with_suffix(target.suffix + ".input.json")
    if manifest.exists():
        previous = json.loads(manifest.read_text())
        if previous["recipe"] != recipe or not target.is_file() or store.sha(target) != previous["sha256"]:
            raise ValueError("Prepared input changed; inspect this job before retrying")
        return target
    if target.exists():
        raise ValueError("Prepared input has no freeze receipt; inspect before replacing it")
    build()
    manifest.write_text(json.dumps({"recipe": recipe, "sha256": store.sha(target)}, indent=2))
    return target


def shot_media(clip, shot, d, dimensions=None):
    target = d / "source.mkv"
    start = shot["start"]
    end = shot["end"]
    w, h = clip["dimensions"]
    spatial = (
        f"scale={w}:{h}:flags=lanczos"
        if clip.get("canvas_aligned")
        else f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2"
    )
    if dimensions and dimensions != clip["dimensions"]:
        spatial += f",scale={dimensions[0]}:{dimensions[1]}:flags=lanczos"
    source_id = clip.get("render_source") or clip["source"]
    source = store.path(source_id)
    source_stat = source.stat()
    recipe = {"source": source_id, "source_sha256": store.asset(source_id)["sha256"],
              "source_size": source_stat.st_size, "source_mtime_ns": source_stat.st_mtime_ns,
              "range": [start, end], "dimensions": dimensions or clip["dimensions"],
              "canvas": clip["dimensions"], "canvas_aligned": bool(clip.get("canvas_aligned")),
              "audio": clip["audio"], "codec": "ffv1-pcm-24fps-v2"}
    args = ["-i", source]
    if not clip["audio"]:
        args += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
    args += [
        "-vf",
        f"trim=start_frame={start}:end_frame={end},setpts=PTS-STARTPTS,{spatial},setsar=1",
        "-c:v",
        "ffv1",
        "-r",
        "24",
    ]
    if clip["audio"]:
        args += [
            "-af",
            f"atrim=start={start / 24}:end={end / 24},asetpts=PTS-STARTPTS",
            "-c:a",
            "pcm_s16le",
        ]
    else:
        args += [
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:a",
            "pcm_s16le",
            "-t",
            str((end - start) / 24),
        ]
    return frozen_file(target, recipe, lambda: ff([*args, target]))
