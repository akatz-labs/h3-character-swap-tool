"""Python adapter to shared SDK paths and cache coordination; no private index."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def shared_models_root():
    explicit = os.environ.get("H3_WORKBENCH_MODELS_ROOT")
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_absolute():
            raise ValueError("The configured models root must be absolute")
        return path.resolve()
    result = subprocess.run([os.environ.get("H3_WORKBENCH_NODE", "node"),
                             '--input-type=module', '-e',
                             "import {ecosystemPaths} from '@akatz/app-config/ecosystem'; console.log(JSON.stringify(await ecosystemPaths('h3-workbench')));"],
                            cwd=ROOT, check=True, capture_output=True, text=True, timeout=15)
    return Path(json.loads(result.stdout)["modelsRoot"]).resolve()


_spec = importlib.util.spec_from_file_location("akatz_shared_hf_cache",
    ROOT / "node_modules/@akatz/app-config/src/shared_hf_cache.py")
if _spec is None or _spec.loader is None:
    raise RuntimeError("Install the pinned shared SDK with npm ci")
cache = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cache)
