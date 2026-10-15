"""Small immutable still previews; original registered assets stay untouched."""

import threading
from PIL import Image, ImageOps
from . import store
from .config import MEDIA

LOCK = threading.Lock()
LOCKS = {}


def prepare(asset_id, size):
    if size not in {256, 640, 960, 1280}:
        raise ValueError("Choose a supported image preview size")
    asset = store.asset(asset_id)
    if not asset["mime"].startswith("image/"):
        raise ValueError("Still previews require an image")
    key = f"{asset['sha256']}-{size}-v1"
    directory = MEDIA / "previews" / "images"
    destination = directory / (key + ".webp")
    with LOCK:
        lock = LOCKS.setdefault(key, threading.Lock())
    with lock:
        if not destination.exists():
            directory.mkdir(parents=True, exist_ok=True)
            pending = destination.with_suffix(".pending.webp")
            try:
                with Image.open(asset["path"]) as original:
                    image = ImageOps.exif_transpose(original)
                    image.thumbnail((size, size), Image.Resampling.LANCZOS)
                    image = image.convert("RGBA" if "A" in image.getbands() or "transparency" in image.info else "RGB")
                    image.save(pending, "WEBP", quality=82, method=3)
                pending.replace(destination)
            finally:
                pending.unlink(missing_ok=True)
    return destination
