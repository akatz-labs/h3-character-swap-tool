"""Verify bundled shared packages; optionally rebuild their corresponding source."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import sys

ROOT = Path(__file__).resolve().parents[1]


def verify(rebuild=False):
    sys.path.insert(0, str(ROOT))
    from runtime.relay_dependency import source
    lock, files = source()
    if not {'LICENSE', 'NOTICE.md', 'LICENSE-Apache-2.0', 'h3_relay/workflows.py', 'h3_relay/planning.py'} <= files.keys():
        raise ValueError('Relay source or license inventory is incomplete')
    print('H3 Relay ' + lock['commit'] + ': verified')
    manifest = json.loads((ROOT / 'vendor/manifest.json').read_text())
    dependencies = json.loads((ROOT / 'package.json').read_text())['dependencies']
    for package in manifest['packages']:
        for field, checksum in [('archive', 'sha256'), ('sourceArchive', 'sourceSha256')]:
            path = ROOT / 'vendor' / package[field]
            if path.parent != ROOT / 'vendor' or hashlib.sha256(path.read_bytes()).hexdigest() != package[checksum]:
                raise ValueError('Package checksum mismatch: ' + package[field])
        if dependencies[package['name']] != 'file:vendor/' + package['archive']:
            raise ValueError('Dependency pin differs from provenance manifest')
        with tarfile.open(ROOT / 'vendor' / package['archive']) as archive:
            metadata = json.load(archive.extractfile('package/package.json'))
            if (metadata['name'], metadata['version']) != (package['name'], package['version']):
                raise ValueError('Package identity mismatch')
            if not archive.getmember('package/LICENSE').isfile():
                raise ValueError('Missing license')
            if rebuild:
                with tempfile.TemporaryDirectory(prefix='h3-vendor-') as directory:
                    with tarfile.open(ROOT / 'vendor' / package['sourceArchive']) as source:
                        source.extractall(directory, filter='data')
                    root = Path(directory) / 'package'
                    subprocess.run(['npm', 'ci', '--ignore-scripts'], cwd=root, check=True)
                    subprocess.run(['npm', 'run', 'build'], cwd=root, check=True)
                    ecosystem_test = root / 'test/ecosystem.test.mjs'
                    if ecosystem_test.is_file():
                        subprocess.run(['node', '--test', str(ecosystem_test)], cwd=root, check=True)
                    for member in archive.getmembers():
                        if member.isfile() and member.name.startswith(('package/dist/', 'package/src/', 'package/desktop/')):
                            relative = Path(member.name).relative_to('package')
                            if (root / relative).read_bytes() != archive.extractfile(member).read():
                                raise ValueError('Rebuilt output differs: ' + str(relative))
        print(package['name'] + '@' + package['version'] + ': verified' + (' and rebuilt' if rebuild else ''))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rebuild', action='store_true')
    verify(parser.parse_args().rebuild)
