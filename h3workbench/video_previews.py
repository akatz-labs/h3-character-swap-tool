"""Cached playback copies; registered originals remain download/generation inputs."""
import json
import threading
from concurrent.futures import ThreadPoolExecutor

from . import media, store
from .config import check_storage

POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix='h3-video-preview')
LOCK = threading.Lock()
PENDING = {}
VERSION = '960-crf24-v1'


def prepare(asset_id, retry=False):
    original = store.asset(asset_id)
    if not original['mime'].startswith('video/'):
        raise ValueError('Choose a video for playback')
    key = original['sha256'] + '-' + VERSION
    directory = store.MEDIA / 'previews' / 'videos' / key
    output, receipt = directory / 'preview.mp4', directory / 'preview.json'
    with LOCK:
        if output.exists() and receipt.exists():
            return {'state': 'ready', 'asset': store.asset_add(output), **json.loads(receipt.read_text())}
        if retry and PENDING.get(key, {}).get('state') == 'failed':
            PENDING.pop(key)
        if key not in PENDING:
            PENDING[key] = {'state': 'preparing'}

            def build():
                pending = directory / 'preview.pending.mp4'
                try:
                    check_storage(1)
                    directory.mkdir(parents=True, exist_ok=True)
                    media.ff(['-i', original['path'], '-map', '0:v:0', '-map', '0:a:0?',
                        '-vf', "scale=w='min(960,iw)':h='min(960,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2:flags=lanczos,setsar=1",
                        '-c:v', 'libx264', '-preset', 'fast', '-crf', '24', '-maxrate', '1600k', '-bufsize', '3200k',
                        '-g', '24', '-keyint_min', '24', '-sc_threshold', '0', '-pix_fmt', 'yuv420p',
                        '-c:a', 'aac', '-b:a', '96k', '-movflags', '+faststart', pending])
                    info = media.probe(pending)
                    pending.replace(output)
                    temporary = receipt.with_suffix('.pending.json')
                    temporary.write_text(json.dumps({**info, 'bytes': output.stat().st_size,
                        'original': asset_id, 'source_sha256': original['sha256'], 'version': VERSION}))
                    temporary.replace(receipt)
                    with LOCK:
                        PENDING.pop(key, None)
                except Exception as error:
                    with LOCK:
                        PENDING[key] = {'state': 'failed', 'error': str(error)}
                finally:
                    pending.unlink(missing_ok=True)

            POOL.submit(build)
        return dict(PENDING[key])
