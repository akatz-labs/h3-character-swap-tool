"""Build the focused, reproducible character-swap recipe from tracked sources."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tomllib

ROOT = Path(__file__).resolve().parents[1]
PROFILE = 'h3-relay-toolkit-v2'
import sys
sys.path.insert(0, str(ROOT))
from runtime.relay_dependency import extract


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(destination):
    destination = Path(destination)
    if destination.exists():
        raise ValueError('Destination must be new; frozen recipes are immutable')
    destination.mkdir(parents=True)
    metadata = destination / 'metadata'
    shutil.copytree(ROOT / 'runtime/comfyui', metadata)
    relay = extract(destination / 'nodes/h3_relay')
    shutil.copytree(ROOT / 'worker_nodes/h3_workbench_runtime', destination / 'nodes/h3_workbench_runtime',
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    files = {str(p.relative_to(destination)): {'sha256': sha(p), 'bytes': p.stat().st_size}
             for p in sorted((destination / 'nodes').rglob('*')) if p.is_file()}
    manifest = tomllib.loads((metadata / 'pyproject.toml').read_text())
    receipt = {'version': 1, 'profile': PROFILE,
        'relay': relay, 'comfy_revision': manifest['tool']['comfygit']['comfyui_commit_sha'],
        'metadata': {str(p.relative_to(metadata)): sha(p) for p in sorted(metadata.rglob('*')) if p.is_file()},
        'files': files}
    reviewed = json.loads((ROOT / 'docker/resource-catalog.json').read_text())['models']
    catalog = {'version': 1, 'platform': 'linux-nvidia-cu128',
        'comfy_revision': receipt['comfy_revision'],
        'models': {m['relativePath']: {'sha256': m['sha256'], 'bytes': m['sizeBytes']} for m in reviewed}}
    (destination / 'model-identities.json').write_text(json.dumps(catalog, indent=2) + '\n')
    (destination / 'capture-receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps({'profile': PROFILE, 'files': len(files), 'bytes': sum(v['bytes'] for v in files.values())}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    build(args.destination)
