"""Workbench session integration. Generation belongs to upstream H3 Relay."""
import hashlib
import importlib.util
import os
from pathlib import Path

if os.environ.get('AKATZ_MANAGED_SESSION') == '1':
    path = Path(os.environ['AKATZ_SESSION_CACHE_MODULE'])
    if hashlib.sha256(path.read_bytes()).hexdigest() != os.environ['AKATZ_SESSION_CACHE_SHA256']:
        raise RuntimeError('Shared cache module revision changed')
    spec = importlib.util.spec_from_file_location('akatz_managed_cache', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}
