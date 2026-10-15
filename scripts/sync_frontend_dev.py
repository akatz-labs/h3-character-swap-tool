"""Mirror only frontend inputs to an owned development directory over SSH."""
import argparse
import json
import re
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INPUTS = ['web/src', 'web/index.html', 'web/vite.config.ts',
          'static', 'scripts/build_legacy_bridge.mjs']


def fingerprint():
    rows = []
    for name in INPUTS:
        path = ROOT / name
        paths = [path, *path.rglob('*')] if path.is_dir() else [path]
        for item in paths:
            if item.is_dir() or not item.exists():
                continue
            stat = item.lstat()
            rows.append((str(item.relative_to(ROOT)), stat.st_size, stat.st_mtime_ns))
    return sorted(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', required=True)
    parser.add_argument('--destination', required=True)
    parser.add_argument('--watch', action='store_true')
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.@-]*', args.host):
        parser.error('Use a plain SSH host or user@host')
    if not re.fullmatch(r'/[A-Za-z0-9_./-]+', args.destination) or '..' in Path(args.destination).parts or args.destination == '/':
        parser.error('Use a dedicated absolute destination without spaces or parent traversal')
    previous = None
    while True:
        current = fingerprint()
        if current != previous:
            command = ['rsync', '-azc', '--recursive', '--relative', '--delete',
                       '--exclude=.env*', '--exclude=__pycache__',
                       '-e', 'ssh -o BatchMode=yes', '--files-from=-',
                       str(ROOT) + '/', args.host + ':' + args.destination.rstrip('/') + '/']
            result = subprocess.run(command, input='\n'.join(INPUTS) + '\n', text=True)
            if result.returncode == 0:
                previous = current
                print(json.dumps({'event': 'frontend-synced', 'files': len(current)}), flush=True)
            elif not args.watch:
                raise SystemExit(result.returncode)
        if not args.watch:
            return
        time.sleep(1)


if __name__ == '__main__':
    main()
