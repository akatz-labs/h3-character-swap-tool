"""Install a fresh Linux NVIDIA worker from this checkout, without Docker or models."""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'runtime'))
from build_recipe import build
from configure_installation import configure


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def shared_paths():
    return json.loads(subprocess.check_output(['node', str(ROOT / 'scripts/ecosystem_paths.mjs')], text=True))


@contextmanager
def setup_lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError('Another setup is using this installation location; wait for it to finish') from error
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def tooling(tools_root, cache):
    files = ['pyproject.toml', 'uv.lock']
    identities = {name: digest(ROOT / 'runtime/tooling' / name) for name in files}
    revision = hashlib.sha256(json.dumps(identities, sort_keys=True).encode()).hexdigest()
    target = tools_root / 'comfygit' / revision
    with setup_lock(target.parent / (revision + '.lock')):
        complete = target / 'akatz-tooling.json'
        if target.exists():
            if not complete.is_file() or json.loads(complete.read_text()) != identities:
                raise ValueError('Incomplete or changed tooling installation preserved at ' + str(target))
            if any(digest(target / name) != value for name, value in identities.items()):
                raise ValueError('Installed tooling metadata changed; existing files preserved')
        else:
            target.mkdir(parents=True)
            for name in files:
                shutil.copy2(ROOT / 'runtime/tooling' / name, target / name)
            env = {**os.environ, 'UV_CACHE_DIR': str(cache)}
            # A fresh venv at its permanent path; copying a checkout venv breaks entrypoints.
            env.pop('UV_PROJECT_ENVIRONMENT', None)
            subprocess.run(['uv', 'sync', '--locked', '--project', str(target), '--python', '3.12'], env=env, check=True)
            complete.write_text(json.dumps(identities, sort_keys=True) + '\n')
        for name in ['python', 'cg']:
            if not (target / '.venv/bin' / name).is_file():
                raise ValueError('Installed tooling is incomplete: ' + str(target))
    return target / '.venv/bin/python', target / '.venv/bin/cg'


def runtime_revision(models, port, cache, tools_root):
    files = [ROOT / name for name in ['dist/worker/h3-adapter.mjs',
        'runtime/build_recipe.py', 'runtime/relay_dependency.py', 'runtime/relay-lock.json', 'runtime/configure_installation.py', 'runtime/install_recipe.py',
        'runtime/allowed_nodes.json', 'runtime/tooling/pyproject.toml', 'runtime/tooling/uv.lock',
        'scripts/setup_source_worker.py', 'docker/resource-catalog.json', 'docker/models-v1.json']]
    for name in ['runtime/comfyui', 'runtime/tools', 'worker_nodes', 'vendor']:
        files.extend(p for p in (ROOT / name).rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc')
    identity = {'files': {str(p.relative_to(ROOT)): digest(p) for p in sorted(files)},
        'modelsRoot': str(models), 'comfyPort': port, 'cache': str(cache), 'toolsRoot': str(tools_root)}
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


def prepare(root, models, port, uv_cache, tools_root):
    if sys.platform != 'linux':
        raise ValueError('The source worker is currently qualified for Linux NVIDIA only')
    if root.exists():
        raise ValueError('Choose a new worker directory; existing installations are preserved')
    for path in [ROOT / 'dist/worker/h3-adapter.mjs']:
        if not path.is_file():
            raise ValueError('Run the source dependency/build steps first: missing ' + str(path))
    python, cg = tooling(tools_root, uv_cache)
    root.mkdir(parents=True)
    build(root / 'recipe')
    # Invoke the locked tooling interpreter, never the user's global cg install.
    subprocess.run([str(python), str(ROOT / 'runtime/install_recipe.py'), str(root / 'recipe'),
        str(root / 'workspace'), 'h3-character-swap', str(models), '--package-root', str(root / 'runtime'),
        '--cg', str(cg), '--controller', str(ROOT / 'runtime/tools/managed-comfy-session.py'),
        '--cache', str(ROOT / 'runtime/tools/managed-comfy-cache.py'),
        *(['--uv-cache', str(uv_cache)] if uv_cache else [])], check=True)
    profile = json.loads((ROOT / 'docker/resource-catalog.json').read_text())
    catalog = json.loads((root / 'recipe/model-identities.json').read_text())['models']
    contract_path = root / 'runtime/runtime-contract.json'
    contract = json.loads(contract_path.read_text())
    contract.update(models=catalog, stage_model_validation=True, startup_models=[])
    contract_path.write_text(json.dumps(contract, indent=2) + '\n')
    configuration = {'python': str(python), 'port': port, 'mediaRoot': str(root / 'media'),
        'ownerStateDirectory': str(root / 'owner'),
        'allowedNodes': json.loads((ROOT / 'runtime/allowed_nodes.json').read_text())}
    configure(root / 'runtime', root / 'binding', ROOT / 'dist/worker/h3-adapter.mjs', configuration,
        model_names=list(catalog), features=[f['id'] for f in profile['features']])
    path = root / 'binding/installation.json'
    manifest = json.loads(path.read_text())
    manifest['features'].insert(0, 'runtime-installed')
    manifest['id'] = 'h3-' + hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()[:20]
    path.write_text(json.dumps(manifest, indent=2) + '\n')
    shutil.copy2(ROOT / 'docker/resource-catalog.json', root / 'resource-catalog.json')
    settings = {'workspace': str(root / 'workspace'), 'modelsRoot': str(models), 'pythonExecutable': str(python),
        'cgExecutable': str(cg), 'environment': 'h3-character-swap', 'comfyUrl': 'http://127.0.0.1:' + str(port)}
    (root / 'settings.json').write_text(json.dumps(settings, indent=2) + '\n')
    return manifest['id']


def setup(args):
    if sys.platform != 'linux':
        raise ValueError('The source worker is currently qualified for Linux NVIDIA only')
    paths = shared_paths()
    previous = paths['installation']
    models = (args.models or Path(previous['modelsRoot'] if previous else paths['modelsRoot'])).expanduser().resolve()
    cache = (args.cache or Path(paths['cacheRoot']) / 'uv').expanduser().resolve()
    tools_root = Path(paths['toolsRoot']).resolve()
    revision = runtime_revision(models, args.comfy_port, cache, tools_root)
    root = (args.root or Path(paths['runtimesRoot']) / revision).expanduser().resolve()
    executor_port = args.executor_port or (previous['executorPort'] if previous else int(os.environ.get('AKATZ_EXECUTOR_PORT', '8841')))
    if not 1 <= executor_port <= 65535:
        raise ValueError('Invalid executor port')
    selection = {'schemaVersion': 1, 'appId': 'h3-workbench', 'revision': revision,
        'root': str(root), 'modelsRoot': str(models), 'executorPort': executor_port}
    with setup_lock(root.parent / (root.name + '.setup.lock')):
        receipt_path = root / 'ecosystem-installation.json'
        reused = root.exists()
        if reused:
            if not receipt_path.is_file():
                raise ValueError('Existing or incomplete worker preserved at ' + str(root) + '; inspect it before retrying with a new --root')
            receipt = json.loads(receipt_path.read_text())
            if receipt['installation'] != {**selection, 'executorPort': receipt['installation']['executorPort']}:
                raise ValueError('Existing worker configuration differs; choose a new --root')
            for name, expected in receipt['files'].items():
                if not (root / name).is_file() or digest(root / name) != expected:
                    raise ValueError('Installed worker file changed; existing installation preserved: ' + name)
            settings = json.loads((root / 'settings.json').read_text())
            if not Path(settings['cgExecutable']).is_file() or not Path(settings['pythonExecutable']).is_file():
                raise ValueError('Installed tooling is missing; existing worker preserved')
        else:
            installation_id = prepare(root, models, args.comfy_port, cache, tools_root)
            files = ['binding/installation.json', 'binding/runtime-contract.json', 'binding/h3-adapter.mjs',
                'binding/resources.json', 'runtime/installation-receipt.json', 'runtime/runtime-contract.json',
                'runtime/tools/managed-comfy-session.py', 'runtime/tools/managed-comfy-cache.py',
                'settings.json', 'resource-catalog.json']
            receipt = {'schemaVersion': 1, 'installation': selection, 'installationId': installation_id,
                'files': {name: digest(root / name) for name in files}}
            receipt_path.write_text(json.dumps(receipt, indent=2) + '\n')
        # Publish discovery only when every install step completed. Concurrent selection loses safely.
        subprocess.run(['node', str(ROOT / 'scripts/ecosystem_paths.mjs'), '--action', 'select'],
            input=json.dumps({'installation': selection, 'expected': previous}), text=True, check=True)
    print(json.dumps({'workerRoot': str(root), 'models': str(models), 'reused': reused,
        'installationId': receipt['installationId'], 'readyToStart': True}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, help='Override the managed, versioned runtime directory')
    parser.add_argument('--models', type=Path, help='Override the shared Akatz model store')
    parser.add_argument('--comfy-port', default=8188, type=int)
    parser.add_argument('--executor-port', type=int)
    parser.add_argument('--cache', type=Path, help='Override the shared uv package cache')
    args = parser.parse_args()
    if not 1 <= args.comfy_port <= 65535:
        parser.error('Invalid ComfyUI port')
    setup(args)
