"""Session-only CPU tensor cache. Never writes or restores tensor files."""
import copy
import hashlib
import json
import threading
import time

VERSION = 'character-swap-relay-v1'
BODY_OUTPUT_VERSION = 'raw-decoded-body-v1'
REFERENCE_HISTORY_VERSION = 'source-reference-includes-history-v2'
CACHE_POLICY = 'session-ram-one-shot-30min-v1'
IDLE_SECONDS = 30 * 60
_lock = threading.RLock()
_cache = {}
_shot = None
_last_access = time.monotonic()
_timer = None

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

def _busy():
    try:
        from server import PromptServer
        return PromptServer.instance.prompt_queue.get_tasks_remaining() > 0
    except (ImportError, AttributeError):
        return False

def expire_idle(now=None):
    global _last_access
    with _lock:
        now = time.monotonic() if now is None else now
        if now - _last_access >= IDLE_SECONDS and not _busy():
            _cache.clear()
            _last_access = now
            return True
    return False

def _tick():
    global _timer
    expire_idle()
    with _lock:
        if _cache:
            _timer = threading.Timer(60, _tick)
            _timer.daemon = True
            _timer.start()
        else:
            _timer = None

def _touch():
    global _last_access, _timer
    _last_access = time.monotonic()
    if _timer is None:
        _timer = threading.Timer(60, _tick)
        _timer.daemon = True
        _timer.start()

def begin(shot_key, keys):
    """Retain only reusable windows of the current shot and current plan."""
    global _shot
    with _lock:
        expire_idle()
        if _shot != shot_key:
            _cache.clear()
        _shot = shot_key
        for key in list(_cache):
            if key not in keys:
                del _cache[key]
        _touch()

def exists(key):
    with _lock:
        expire_idle()
        return key in _cache

def load(key):
    with _lock:
        if key not in _cache:
            raise ValueError('Temporary window expired; queue the workflow again to regenerate its prefix.')
        _touch()
        tensors, meta = _cache[key]
        return tensors, copy.deepcopy(meta)

def metadata(key):
    return load(key)[1]

def save(key, tensors, metadata):
    with _lock:
        if key in _cache:
            return load(key)[1]
        data = {k: v.detach().cpu().contiguous().clone() for k, v in tensors.items()}
        meta = dict(metadata, key=key, version=VERSION, cache_policy=CACHE_POLICY)
        _cache[key] = (data, meta)
        _touch()
        return copy.deepcopy(meta)

def status():
    with _lock:
        return {'policy': CACHE_POLICY, 'persistent_tensors': False,
                'idle_timeout_seconds': IDLE_SECONDS, 'cached_windows': len(_cache),
                'tensor_bytes': sum(t.numel()*t.element_size() for data, _ in _cache.values() for t in data.values())}

def dependency_keys(source_key, model_key, windows, settings):
    out, prior = [], None
    for entry in windows:
        body = dict(settings, seed=(int(settings['seed']) + entry['index']) % 2**64)
        key = digest([BODY_OUTPUT_VERSION, VERSION, source_key, model_key, entry, body, prior])
        if entry['history_frames'] > 1:
            key = digest([REFERENCE_HISTORY_VERSION, key])
        out.append(dict(entry, body_settings=body, body_key=key))
        prior = key
    return out
