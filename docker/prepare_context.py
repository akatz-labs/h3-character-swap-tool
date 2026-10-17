"""Explicit private Docker context. Never sends a repository, data root or home.

Inputs are the verified CPU payload, captured recipe and exact tooling wheels.
All outputs are new; no model bytes, job records or host configuration are copied.
"""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import subprocess

ROOT=Path(__file__).resolve().parents[1]


def sha(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def tree(source,target,exact=False):
    source=source.resolve()
    for path in source.rglob('*'):
        if path.is_symlink():raise ValueError('Context source has a link; resolve and review it first: '+str(path))
    shutil.copytree(source,target,ignore=None if exact else shutil.ignore_patterns('__pycache__','*.pyc'))


PAYLOAD_METADATA = {'payload-manifest.json', 'desktop/launch.json'}


def verify_file_set(payload, files, metadata):
    """Reject unlisted files, links, aliases and changed content."""
    payload = payload.resolve(strict=True)
    declared = set()
    for item in files:
        relative = item['path']
        path = PurePosixPath(relative)
        if (path.is_absolute() or '..' in path.parts or str(path) != relative
                or relative in declared or relative in metadata):
            raise ValueError('Invalid or duplicate payload path: ' + relative)
        declared.add(relative)
    actual = set()
    for path in payload.rglob('*'):
        if path.is_symlink():
            raise ValueError('Payload links require review: ' + str(path.relative_to(payload)))
        if path.is_file():
            actual.add(path.relative_to(payload).as_posix())
        elif not path.is_dir():
            raise ValueError('Payload contains a special file: ' + str(path.relative_to(payload)))
    expected = declared | metadata
    if actual != expected:
        raise ValueError('Payload file set differs from the manifest; unexpected=' +
                         repr(sorted(actual - expected)) + '; missing=' + repr(sorted(expected - actual)))
    for item in files:
        path = payload / item['path']
        if path.stat().st_size != item['bytes'] or sha(path) != item['sha256']:
            raise ValueError('Payload content differs: ' + item['path'])


def verify_payload(payload):
    """Reject unlisted files before any payload bytes enter a build context."""
    payload = payload.resolve(strict=True)
    manifest = json.loads((payload / 'payload-manifest.json').read_text())
    if manifest.get('appId') != 'h3-workbench' or manifest.get('gpuDependencies') is not False:
        raise ValueError('Expected the CPU-only H3 payload')
    verify_file_set(payload, manifest['files'], PAYLOAD_METADATA)
    revision = hashlib.sha256(json.dumps(manifest['files'], sort_keys=True,
                                        separators=(',', ':')).encode()).hexdigest()
    if revision != manifest['revision']:
        raise ValueError('Payload revision differs from its file manifest')
    return manifest


def main():
    parser=argparse.ArgumentParser()
    for name in ['payload','recipe','wheels','tooling-constraints','resource-catalog','configuration','release-notices','out']:parser.add_argument('--'+name,required=True,type=Path)
    args=parser.parse_args();manifest=verify_payload(args.payload)
    output=args.out.resolve();output.mkdir(parents=True,exist_ok=False)
    # Copy the verified allowlist, never the enclosing directory. Recheck the
    # copy so a changed source cannot silently enter the finished context.
    for relative in sorted({item['path'] for item in manifest['files']} | PAYLOAD_METADATA):
        target=output/'payload'/relative;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(args.payload/relative,target)
    copied=verify_payload(output/'payload')
    if copied['revision']!=manifest['revision']:
        raise ValueError('Payload changed while preparing the build context')
    notices=json.loads((args.release_notices/'notice-manifest.json').read_text())
    notice_metadata={'notice-manifest.json'}
    verify_file_set(args.release_notices,notices['files'],notice_metadata)
    for relative in sorted({item['path'] for item in notices['files']} | notice_metadata):
        target=output/'release-notices'/relative;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(args.release_notices/relative,target)
    verify_file_set(output/'release-notices',notices['files'],notice_metadata)
    subprocess.run([str(output/'payload/runtime/node/node'),'--input-type=module','-e',
        "import{readDesktopLaunch}from'@akatz/app-config/desktop-runtime';if(!(await readDesktopLaunch(process.cwd())))throw Error('Missing launch manifest');"],
        cwd=output/'payload',check=True)
    for name in ['Dockerfile.app','Dockerfile.worker','compose.yaml','compose.cdi.yaml','compose.models.yaml']:
        shutil.copy2(ROOT/'docker'/name,output/name)
    # Add the same verified pure planner/recipe archive used by the source app.
    import sys
    sys.path.insert(0, str(ROOT))
    from runtime.relay_dependency import source
    relay, _ = source()
    for name in ['runtime/relay_dependency.py', 'runtime/relay-lock.json', relay['archive']]:
        target = output / 'app-relay-dependency' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, target)
    worker=output/'worker';worker.mkdir();(worker/'scripts').mkdir()
    for name in ['install_recipe.py','configure_installation.py']:shutil.copy2(ROOT/'runtime'/name,worker/'scripts'/name)
    for name in ['build_worker.py','native_preflight.py']:
        shutil.copy2(ROOT/'docker'/name,worker/'scripts'/name)
    tree(ROOT/'runtime/tools',worker/'tools')
    tree(args.payload/'runtime/node',worker/'runtime-node')
    tree(ROOT/'dist/runtime',worker/'dist/runtime');tree(ROOT/'dist/worker',worker/'dist/worker')
    for package in ['@akatz/app-config','zod']:tree(args.payload/'node_modules'/package,worker/'node_modules'/package)
    (worker/'package.json').write_text(json.dumps({'name':'@akatz/h3-container-worker','version':'0.1.0-docker.1','private':True,'type':'module'}))
    receipt=json.loads((args.recipe/'capture-receipt.json').read_text());recipe=worker/'recipe';recipe.mkdir()
    assert receipt.get('profile') == 'h3-relay-toolkit-v2', 'Build a focused release recipe with runtime/build_recipe.py'
    assert {Path(p).parts[1] for p in receipt['files']} == {'h3_relay', 'h3_workbench_runtime'}, 'Unexpected custom node package'
    for name in ['capture-receipt.json','model-identities.json']:shutil.copy2(args.recipe/name,recipe/name)
    for name,identity in receipt['files'].items():
        source=(args.recipe/name).resolve();assert source.is_relative_to(args.recipe.resolve()) and source.is_file()
        assert source.stat().st_size==identity['bytes'] and sha(source)==identity['sha256']
        target=recipe/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
    for name,expected in receipt['metadata'].items():
        source=(args.recipe/'metadata'/name).resolve();assert source.is_relative_to((args.recipe/'metadata').resolve()) and sha(source)==expected
        target=recipe/'metadata'/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
    wheels=json.loads((args.wheels/'wheel-manifest.json').read_text());(output/'tooling-wheels').mkdir()
    for name,identity in wheels.items():
        assert Path(name).name==name and name.endswith('.whl')
        path=args.wheels/name;assert path.stat().st_size==identity['bytes'] and sha(path)==identity['sha256']
        shutil.copy2(path,output/'tooling-wheels'/name)
    shutil.copy2(args.tooling_constraints,output/'tooling-wheels/constraints.txt')
    configuration=json.loads(args.configuration.read_text());assert configuration['appId']=='h3-workbench'
    allowed=json.loads((ROOT/'runtime/allowed_nodes.json').read_text())
    (worker/'configuration.json').write_text(json.dumps({'python':'/usr/local/bin/python','port':8188,
        'mediaRoot':'/var/lib/akatz/media','ownerStateDirectory':'/var/lib/akatz/owner','allowedNodes':allowed},indent=2))
    catalog=json.loads(args.resource_catalog.read_text());assert catalog['appId']=='h3-workbench'
    # Existing acquisition modes/terms stay exact; no download URLs are invented.
    (worker/'resource-catalog.json').write_text(json.dumps(catalog,indent=2))
    entries=[{'path':str(p.relative_to(output)),'bytes':p.stat().st_size,'sha256':sha(p)} for p in sorted(output.rglob('*')) if p.is_file()]
    record={'schemaVersion':1,'payloadRevision':manifest['revision'],'files':entries,'modelBytesIncluded':False,
            'sourceCheckoutIncluded':False,'privateCandidate':True,'imagePublication':'not-published'}
    (output/'context-manifest.json').write_text(json.dumps(record,indent=2)+'\n')
    print(json.dumps({'output':str(output),'files':len(entries),'bytes':sum(i['bytes'] for i in entries),'payloadRevision':manifest['revision'],'modelBytesIncluded':False}))


if __name__=='__main__':main()
