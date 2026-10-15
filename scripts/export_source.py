"""Create the reviewed public source tree without local history, state or media."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def export(destination):
    subprocess.run([sys.executable, str(ROOT / 'scripts/verify_vendor.py')], check=True)
    files = json.loads((ROOT / 'release/source-files.json').read_text())['files']
    if destination.exists():
        raise ValueError('Source export destination must be new')
    inventory = {}
    for relative in files:
        source = ROOT / relative
        if not source.is_file() or source.is_symlink() or source.resolve() != source.absolute() or not source.resolve().is_relative_to(ROOT):
            raise ValueError('Missing or unsafe source file: ' + relative)
        inventory[relative] = hashlib.sha256(source.read_bytes()).hexdigest()
    destination.mkdir(parents=True)
    for relative in files:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
        target.chmod(0o644)
        if hashlib.sha256(target.read_bytes()).hexdigest() != inventory[relative]:
            raise ValueError('Source changed during export: ' + relative)
    receipt = {'schemaVersion': 1, 'files': inventory,
        'sourceDigest': hashlib.sha256(json.dumps(inventory, sort_keys=True).encode()).hexdigest(),
        'includesModelWeights': False, 'includesUserMedia': False}
    (destination / 'source-manifest.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps({'destination': str(destination), 'files': len(files), 'sourceDigest': receipt['sourceDigest']}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path)
    export(parser.parse_args().destination.expanduser().resolve())
