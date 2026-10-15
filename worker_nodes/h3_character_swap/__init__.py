"""H3 Character Swap: the app's focused, standalone ComfyUI node package.

Derived from H3 Relay / H3 Performance under GPL-3.0; see SOURCE_PROVENANCE.json.
Only body generation, SAM support and source-aligned continuation are registered.
"""
from .nodes import PrepareVideo, SelectSubject, ExpandMask, OptionalAnchor, Shot, RecoverMasks
from .video import LabsH3TrimOutput
from .masked_latent import MaskedRegeneration
from .sampling import StagedReference, SamplingWorkspace
from .masks import SavedPersonMask, SaveMaskNPZ
from .correction import RefinePersonMask
from .relay.inputs import WindowInputs
from .relay.settings import Models, Settings, WindowPlan
from .relay.passes import History, BodyWindow
from .relay.runner import Checkpoint, Restore, Append, Run, Controller, Assemble

NODE_CLASS_MAPPINGS = {
    "H3CharacterSwapPrepareVideo": PrepareVideo,
    "H3CharacterSwapSelectSubject": SelectSubject,
    "H3CharacterSwapExpandMask": ExpandMask,
    "H3CharacterSwapRecoverMasks": RecoverMasks,
    "H3CharacterSwapOptionalAnchor": OptionalAnchor,
    "H3CharacterSwapShot": Shot,
    "H3CharacterSwapTrim": LabsH3TrimOutput,
    "H3CharacterSwapMaskedRegeneration": MaskedRegeneration,
    "H3CharacterSwapReference": StagedReference,
    "H3CharacterSwapWorkspace": SamplingWorkspace,
    "H3CharacterSwapLoadMask": SavedPersonMask,
    "H3CharacterSwapSaveMask": SaveMaskNPZ,
    "H3CharacterSwapRefineMask": RefinePersonMask,
    **{"H3CharacterSwap" + cls.__name__: cls for cls in (
        WindowInputs, Models, Settings, WindowPlan, History, BodyWindow,
        Checkpoint, Restore, Append, Run, Controller, Assemble)},
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "H3CharacterSwapPrepareVideo": "H3 · Prepare Source Video",
    "H3CharacterSwapSelectSubject": "H3 · Select Person",
    "H3CharacterSwapExpandMask": "H3 · Expand Person Mask",
    "H3CharacterSwapOptionalAnchor": "H3 · Optional Anchor Image",
    "H3CharacterSwapShot": "H3 Relay · Prepare Character Swap",
    "H3CharacterSwapModels": "H3 Relay · Character Swap Models",
    "H3CharacterSwapSettings": "H3 · Generation Settings",
    "H3CharacterSwapWindowPlan": "H3 Relay · Window Plan",
    "H3CharacterSwapController": "H3 Relay · Generate Character Swap",
    "H3CharacterSwapAssemble": "H3 Relay · Save Video with Source Audio",
    "H3CharacterSwapLoadMask": "H3 · Load Saved Mask + Expansion",
    "H3CharacterSwapSaveMask": "H3 · Save Mask",
    "H3CharacterSwapRefineMask": "H3 · Correct Mask with Points",
}

WEB_DIRECTORY = "./web"

# The qualified INT8 kernels need 64-bit pointer offsets for large H3 matrices.
# Apply only to the exact affected version, retaining its optimized Triton path.
import importlib.metadata
import torch
if torch.cuda.is_available() and importlib.metadata.version("comfy-kitchen") == "0.2.35":
    import comfy_kitchen.backends.triton.quantization as _quantization
    from . import triton_int8_large as _int8
    _quantization._int8_matmul_dequant_kernel = _int8._int8_matmul_dequant_kernel
    _quantization._int8_matmul_dequant_per_row_kernel = _int8._int8_matmul_dequant_per_row_kernel

# Hooks are inert in ordinary ComfyUI. The app enables them for owned sessions.
from . import session as _session

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
