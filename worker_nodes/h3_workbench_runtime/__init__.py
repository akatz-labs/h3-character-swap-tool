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

from .segment_planning import H3WorkbenchSegmentPlan

NODE_CLASS_MAPPINGS = {'H3WorkbenchSegmentPlan': H3WorkbenchSegmentPlan}
NODE_DISPLAY_NAME_MAPPINGS = {'H3WorkbenchSegmentPlan': 'H3 Workbench Segment Plan'}

# The API imports the pure segment planner without a ComfyUI/PyTorch runtime.
# ComfyUI loads this package with its own modules on the import path.
if importlib.util.find_spec('comfy') is not None:
    from .remake import NODE_CLASS_MAPPINGS as REMAKE_NODES
    NODE_CLASS_MAPPINGS.update(REMAKE_NODES)
    from .remake_region import RemakeEditMask, RemakeHistory
    NODE_CLASS_MAPPINGS.update(H3RemakeEditMask=RemakeEditMask, H3RemakeHistory=RemakeHistory)
