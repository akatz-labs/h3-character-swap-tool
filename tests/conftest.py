"""Legacy domain tests use synthetic workers and isolated data, never lab state."""
import json
import os
from pathlib import Path
import tempfile

_sandbox = tempfile.TemporaryDirectory(prefix='h3-tests-')
_root = Path(_sandbox.name)
_workers = _root / 'workers.json'
_workers.write_text(json.dumps({name: {'ssh': 'test@' + name + '.invalid',
    'home': str(_root / name), 'shared': str(_root / 'media'), 'port': 1}
    for name in ['arch', 'fey']}))
os.environ.update(H3_WORKBENCH_PORTABLE='0', H3_WORKBENCH_STATE=str(_root / 'state'),
    H3_WORKBENCH_MEDIA=str(_root / 'media'), H3_WORKBENCH_LEGACY_PILOT=str(_root / 'pilot'),
    H3_WORKBENCH_WORKERS=str(_workers))

os.environ.update(H3_WORKBENCH_MODELS_ROOT=str(_root / "shared-models"),
    AKATZ_DATA_ROOT=str(_root / "ecosystem"), XDG_CONFIG_HOME=str(_root / "config"))
