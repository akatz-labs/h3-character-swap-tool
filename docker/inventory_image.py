"""Read-only inventory of the actual worker image; run without GPUs or networking."""
import hashlib
from importlib import metadata
import json
from pathlib import Path
import platform
import subprocess


def packages(paths):
    result = []
    for distribution in sorted(metadata.distributions(path=paths), key=lambda d: d.metadata['Name'].lower()):
        notices = []
        for item in distribution.files or []:
            if not any(part.lower().startswith(('license', 'copying', 'notice', 'authors')) for part in Path(item).parts):
                continue
            path = Path(distribution.locate_file(item))
            if not path.is_file() or path.stat().st_size > 1024**2:
                continue
            body = path.read_bytes()
            notices.append({'path': str(path), 'sha256': hashlib.sha256(body).hexdigest(),
                            'text': body.decode('utf-8', errors='replace')})
        result.append({'name': distribution.metadata['Name'], 'version': distribution.version,
                       'licenseExpression': distribution.metadata.get('License-Expression'),
                       'licenseMetadata': distribution.metadata.get('License'), 'notices': notices})
    return result


environment = Path('/opt/h3-worker/workspace/environments/h3-docker/.venv/lib/python3.12/site-packages')
assert environment.is_dir()
root = Path('/opt/h3-worker')
dpkg = subprocess.check_output(['dpkg-query', '-W', '-f=${binary:Package}\t${Version}\n'], text=True)
result = {'schemaVersion': 1, 'platform': platform.platform(), 'python': platform.python_version(),
          'globalPython': packages(['/usr/local/lib/python3.12/site-packages']),
          'environmentPython': packages([str(environment)]),
          'debianPackages': [dict(zip(['name', 'version'], row.split('\t'))) for row in dpkg.splitlines()],
          'runtime': json.loads((root/'image-runtime.json').read_text()),
          'node': subprocess.check_output([str(root/'runtime-node/node'), '--version'], text=True).strip(),
          'modelsIncluded': False, 'licenseReviewComplete': False, 'publicationStatus': 'not-published'}
print(json.dumps(result, indent=2))
