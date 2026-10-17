"""Verify and consume the immutable upstream Relay source dependency.

The app imports only upstream's two pure Python recipe/planning modules. ComfyUI
loads the same archive as a node package on the worker.
"""
from functools import lru_cache
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import tarfile
import types

ROOT = Path(__file__).resolve().parents[1]


@lru_cache(maxsize=1)
def source():
    lock = json.loads((ROOT / 'runtime/relay-lock.json').read_text())
    archive = (ROOT / lock['archive']).resolve()
    if not archive.is_relative_to(ROOT / 'vendor'):
        raise ValueError('Relay archive must be a vendored dependency')
    data = archive.read_bytes()
    if hashlib.sha256(data).hexdigest() != lock['sha256']:
        raise ValueError('Pinned Relay archive integrity failure')
    files = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as package:
        for member in package.getmembers():
            name = PurePosixPath(member.name)
            if name.is_absolute() or '..' in name.parts or name.parts[0] != 'h3-relay':
                raise ValueError('Unsafe Relay archive member')
            if member.isdir():
                continue
            if not member.isfile() or len(name.parts) < 2:
                raise ValueError('Relay archive must contain regular source files')
            relative = str(PurePosixPath(*name.parts[1:]))
            if relative in files:
                raise ValueError('Duplicate Relay archive member')
            files[relative] = package.extractfile(member).read()
    return lock, files


@lru_cache(maxsize=2)
def pure_module(name):
    if name not in {'planning', 'workflows'}:
        raise ValueError('Only pure Relay modules may load in the app')
    lock, files = source()
    module = types.ModuleType('pinned_h3_relay_' + name)
    filename = f"{lock['archive']}/h3-relay/h3_relay/{name}.py"
    module.__file__ = filename
    exec(compile(files[f'h3_relay/{name}.py'], filename, 'exec'), module.__dict__)
    return module


def extract(destination):
    destination = Path(destination)
    if destination.exists():
        raise ValueError('Relay extraction requires a new destination')
    lock, files = source()
    for name, data in files.items():
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return lock
