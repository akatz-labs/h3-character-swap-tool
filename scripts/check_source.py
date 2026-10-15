"""Check the public allowlist, local documentation links and obvious private inputs.

This bounded source review is a release check, not a comprehensive secret audit.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess
import tarfile
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
SECRET = re.compile(rb"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|AKIA[A-Z0-9]{16}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)")
PRIVATE = re.compile(rb"/(?:home|Users)/[A-Za-z0-9._-]+/(?:dev|Documents|Desktop)/|[a-zA-Z0-9.-]+\.ts\.net(?=[:/\s])")


def check(tracked=False):
    files = json.loads((ROOT / 'release/source-files.json').read_text())['files']
    if len(set(files)) != len(files):
        raise ValueError('Duplicate source entries')
    errors = []

    def inspect(name, data):
        if SECRET.search(data) or PRIVATE.search(data):
            errors.append(name + ': possible private input; inspect locally')

    for name in files:
        path = ROOT / name
        if not path.is_file() or path.is_symlink() or path.resolve() != path.absolute() or not path.resolve().is_relative_to(ROOT):
            errors.append(name + ': missing or unsafe file')
            continue
        inspect(name, path.read_bytes())
        if name.endswith(('.tgz', '.tar.gz')):
            with tarfile.open(path) as archive:
                for member in archive.getmembers():
                    if Path(member.name).is_absolute() or '..' in Path(member.name).parts or member.issym() or member.islnk():
                        errors.append(name + ': unsafe archive member')
                    elif member.isfile():
                        inspect(name + ':' + member.name, archive.extractfile(member).read())
        if name.endswith('.md'):
            for link in re.findall(r'\[[^\]]*\]\(([^\s)]+)\)', path.read_text()):
                if re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*:', link) or link.startswith('#'):
                    continue
                target = (path.parent / unquote(link.split('#')[0])).resolve()
                if not target.is_relative_to(ROOT) or not any(
                    entry == str(target.relative_to(ROOT)) or entry.startswith(str(target.relative_to(ROOT)) + '/')
                    for entry in files
                ):
                    errors.append(name + ': link outside public source: ' + link)
    if tracked:
        actual = set(subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode().split('\0')) - {''}
        for name in sorted(actual.symmetric_difference(files)):
            errors.append(name + ': tracked files and public allowlist differ')
    if errors:
        raise SystemExit('\n'.join(errors))
    print(f'Public source checks passed: {len(files)} files, including archive contents')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tracked', action='store_true')
    check(parser.parse_args().tracked)
