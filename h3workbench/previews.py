"""Small immutable browser proxies; generation inputs and downloads stay original."""
import hashlib
import json
import threading
import zipfile
from functools import lru_cache
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from PIL import Image
from scipy.ndimage import distance_transform_edt

from . import media, store
from .config import MEDIA, check_storage

LOCK = threading.Lock()
LOCKS = {}
CLIP_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="h3-clip-preview")
CLIP_PENDING = {}


def lock_for(key):
    with LOCK:
        return LOCKS.setdefault(key, threading.Lock())


def size(dimensions, max_side=640):
    w, h = dimensions
    scale = min(1, max_side / max(w, h))
    return max(2, round(w * scale / 2) * 2), max(2, round(h * scale / 2) * 2)


def digest(spec):
    return hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()


def prepare_clip(c, retry=False):
    source = c.get("render_source") or c["source"]
    spec = dict(version=1, asset=source, start=0, end=c["frames"], dimensions=c["dimensions"], audio=c["audio"])
    key = digest(spec)
    directory = MEDIA / "previews" / key
    with LOCK:
        if (directory / "preview.json").exists() and (directory / "preview.mp4").exists():
            return {"state": "ready", "asset": store.asset_add(directory / "preview.mp4"),
                    "frames": c["frames"], "fps": 24, "width": size(c["dimensions"])[0],
                    "height": size(c["dimensions"])[1]}
        if retry and CLIP_PENDING.get(key, {}).get("state") == "failed":
            CLIP_PENDING.pop(key)
        if key not in CLIP_PENDING:
            CLIP_PENDING[key] = {"state": "preparing"}
            def build():
                try:
                    proxy(source, 0, c["frames"], c["dimensions"], c["audio"])
                    with LOCK:
                        CLIP_PENDING.pop(key, None)
                except Exception as error:
                    with LOCK:
                        CLIP_PENDING[key] = {"state": "failed", "error": str(error)}
            CLIP_POOL.submit(build)
        return dict(CLIP_PENDING[key])


def proxy(asset, start, end, dimensions, audio=False, max_side=640, canvas_aligned=False):
    spec = dict(version=1, asset=asset, start=start, end=end, dimensions=dimensions, audio=audio)
    if max_side != 640:
        spec["max_side"] = max_side
    if canvas_aligned:
        spec["canvas_aligned"] = True
    directory = MEDIA / "previews" / digest(spec)
    path = directory / "preview.mp4"
    with lock_for(str(path)):
        if (directory / "preview.json").exists() and path.exists():
            return path
        check_storage(1)
        directory.mkdir(parents=True, exist_ok=True)
        source = store.path(asset)
        # Metadata only: don't decode/count an entire master before encoding a cut.
        info = json.loads(media.run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", source]))
        video = next(s for s in info["streams"] if s["codec_type"] == "video")
        a, b = map(int, video["avg_frame_rate"].split("/"))
        if not b or abs(a / b - 24) > .001:
            raise ValueError("Preview source must have the shot's 24 fps timing")
        w, h = dimensions
        pw, ph = size(dimensions, max_side)
        pending = directory / "preview.pending.mp4"
        spatial = (
            f"scale={w}:{h}:flags=lanczos"
            if canvas_aligned
            else f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2"
        )
        args = ["-i", source, "-map", "0:v:0", "-vf",
                f"trim=start_frame={start}:end_frame={end},setpts=PTS-STARTPTS,"
                f"{spatial},"
                f"scale={pw}:{ph},setsar=1"]
        if audio:
            args += ["-map", "0:a:0", "-af", f"atrim=start={start/24}:end={end/24},asetpts=PTS-STARTPTS",
                     "-c:a", "aac", "-b:a", "96k"]
        else:
            args += ["-an"]
        args += ["-r", "24", "-frames:v", str(end-start), "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
                 "-g", "12", "-keyint_min", "12", "-sc_threshold", "0", "-pix_fmt", "yuv420p",
                 "-movflags", "+faststart", pending]
        media.ff(args)
        result = media.probe(pending)
        if result["frames"] != end-start or abs(result["fps"]-24) > .001:
            raise ValueError("Preview video does not match this shot's frame range")
        pending.replace(path)
        receipt = directory / "preview.pending.json"
        receipt.write_text(json.dumps({**spec, **result, "bytes": path.stat().st_size}, indent=2))
        receipt.replace(directory / "preview.json")
    return path


def source_frame(c, s, frame):
    video = proxy(c.get("render_source") or c["source"], s["start"], s["end"], c["dimensions"], c["audio"])
    path = video.parent / f"frame-{frame}.jpg"
    with lock_for(str(path)):
        if not path.exists():
            pending = path.with_suffix(".pending.jpg")
            # Accurate input seeking decodes at most one short GOP from the proxy.
            media.ff(["-ss", str(max(0, (frame-.01)/24)), "-i", video,
                      "-frames:v", "1", "-q:v", "4", pending])
            pending.replace(path)
    return path


@lru_cache(maxsize=8)
def mask_frame(asset, frame, dimensions):
    w, h = dimensions
    if not asset:
        return np.zeros((h, w), dtype=bool)
    # Read just the requested NPY frame, not a full multi-gigabyte shot array.
    with zipfile.ZipFile(store.path(asset)) as z, z.open("mask.npy") as f:
        version = np.lib.format.read_magic(f)
        if version == (1, 0):
            shape, fortran, dtype = np.lib.format.read_array_header_1_0(f)
        elif version == (2, 0):
            shape, fortran, dtype = np.lib.format.read_array_header_2_0(f)
        else:
            raise ValueError("Unsupported mask array format")
        if len(shape) != 3 or fortran or dtype.hasobject or not 0 <= frame < shape[0]:
            raise ValueError("Mask does not match the preview frame")
        count = shape[1]*shape[2]
        f.seek(f.tell() + frame*count*dtype.itemsize)
        values = np.frombuffer(f.read(count*dtype.itemsize), dtype=dtype).reshape(shape[1:]) > .5
    return np.asarray(Image.fromarray(values).resize((w, h), Image.Resampling.NEAREST))


def mask_layer(s, dimensions, frame, padding):
    spec = dict(version=1, mask=s.get("mask"), dimensions=dimensions, frame=frame, padding=padding)
    path = MEDIA / "previews" / "mask-layers" / (digest(spec)+".png")
    with lock_for(str(path)):
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            m = mask_frame(s.get("mask"), frame, tuple(dimensions))
            if padding and m.any():
                m = distance_transform_edt(~m) <= padding
            # Expand at generation resolution, then resize: padding meaning is unchanged.
            m = np.asarray(Image.fromarray(m).resize(size(dimensions), Image.Resampling.NEAREST))
            arr = np.zeros((*m.shape, 4), dtype=np.uint8)
            arr[m] = [62, 149, 243, 140]
            pending = path.with_suffix(".pending.png")
            Image.fromarray(arr).save(pending, "PNG", optimize=True)
            pending.replace(path)
    return path


# One source decoder plus lossless, paged distance fields. Padding is a client
# threshold in native pixels, so changing it never rebuilds media or runs SAM.
MASK_PENDING = {}
MASK_PAGE_FRAMES = 8


def mask_playback_spec(c, s):
    return dict(
        version=2,
        source=c.get("render_source") or c["source"],
        start=s["start"],
        end=s["end"],
        dimensions=c["dimensions"],
        audio=c["audio"],
        mask=s.get("mask"),
    )


def distance_frames(asset, count, dimensions):
    from contextlib import ExitStack

    with ExitStack() as stack:
        stream = None
        if asset:
            archive = stack.enter_context(zipfile.ZipFile(store.path(asset)))
            stream = stack.enter_context(archive.open("mask.npy"))
            version = np.lib.format.read_magic(stream)
            reader = {
                (1, 0): np.lib.format.read_array_header_1_0,
                (2, 0): np.lib.format.read_array_header_2_0,
            }.get(version)
            if reader is None:
                raise ValueError("Unsupported mask array format")
            shape, fortran, dtype = reader(stream)
            if len(shape) != 3 or shape[0] != count or fortran or dtype.hasobject:
                raise ValueError("Mask frame count does not match shot")
            nbytes = shape[1] * shape[2] * dtype.itemsize
        for _ in range(count):
            if stream:
                raw = stream.read(nbytes)
                mask = np.frombuffer(raw, dtype=dtype).reshape(shape[1:]) > 0.5
                mask = np.asarray(
                    Image.fromarray(mask).resize(
                        tuple(dimensions), Image.Resampling.NEAREST
                    )
                )
                distance = (
                    np.ceil(np.minimum(distance_transform_edt(~mask), 257)).astype(
                        np.uint16
                    )
                    if mask.any()
                    else np.full(mask.shape, 257, np.uint16)
                )
            else:
                distance = np.full(tuple(reversed(dimensions)), 257, np.uint16)
            # Thresholding this nearest sample is exactly equivalent to native
            # expansion followed by nearest-neighbor preview downsampling.
            yield np.asarray(
                Image.fromarray(distance).resize(
                    size(dimensions), Image.Resampling.NEAREST
                ),
                dtype="<u2",
            )


def build_mask_playback(c, s, directory):
    import gzip

    spec = mask_playback_spec(c, s)
    count = s["end"] - s["start"]
    pw, ph = size(c["dimensions"])
    expected = max(1, int(np.ceil(count * pw * ph * 2 / 1024**3)) + 1)
    check_storage(expected)
    directory.mkdir(parents=True, exist_ok=True)
    video = proxy(spec["source"], s["start"], s["end"], c["dimensions"], c["audio"])
    pages, chunk = [], []
    for index, field in enumerate(
        distance_frames(s.get("mask"), count, c["dimensions"])
    ):
        delta = field.copy()
        delta[:, 1:] = field[:, 1:] - field[:, :-1]
        chunk.append(delta.tobytes())
        if len(chunk) == MASK_PAGE_FRAMES or index == count - 1:
            path = directory / f"distance-{len(pages):04}.bin.gz"
            temp = path.with_suffix(".pending")
            with gzip.open(temp, "wb", compresslevel=6) as f:
                for data in chunk:
                    f.write(data)
            temp.replace(path)
            pages.append(
                {
                    "asset": store.asset_add(path),
                    "frames": len(chunk),
                    "bytes": path.stat().st_size,
                }
            )
            chunk = []
    w, h = size(c["dimensions"])
    result = {
        "state": "ready",
        "asset": store.asset_add(video),
        "frames": count,
        "fps": 24,
        "width": w,
        "height": h,
        "pages": pages,
        "page_frames": MASK_PAGE_FRAMES,
        "encoding": "gzip-uint16-le-delta-x",
        "spec": spec,
    }
    temp = directory / "manifest.pending.json"
    temp.write_text(json.dumps(result, indent=2))
    temp.replace(directory / "manifest.json")
    return result


def prepare_mask(c, s, retry=False):
    key = digest(mask_playback_spec(c, s))
    directory = MEDIA / "mask-playback" / key
    manifest = directory / "manifest.json"
    with LOCK:
        if manifest.exists():
            return json.loads(manifest.read_text())
        if retry and MASK_PENDING.get(key, {}).get("state") == "failed":
            MASK_PENDING.pop(key)
        if key not in MASK_PENDING:
            MASK_PENDING[key] = {"state": "preparing"}

            def build():
                try:
                    build_mask_playback(c, s, directory)
                    with LOCK:
                        MASK_PENDING.pop(key, None)
                except Exception as error:
                    with LOCK:
                        MASK_PENDING[key] = {"state": "failed", "error": str(error)}

            CLIP_POOL.submit(build)
        return dict(MASK_PENDING[key])
