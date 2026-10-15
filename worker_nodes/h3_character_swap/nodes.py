"""Public input and mask nodes shared by the app and standalone workflow."""
import hashlib
from pathlib import Path

import numpy as np
import torch
from PIL import Image
import folder_paths
import node_helpers

from .video import LabsH3PrepareVideo, LabsH3TrimOutput
from .masks import expand_masks


class PrepareVideo(LabsH3PrepareVideo):
    CATEGORY = "H3 Character Swap"

    def prepare(self, video, resolution, duration_seconds=0.0):
        values = list(super().prepare(video, resolution, duration_seconds))
        if values[1] is None:
            values[1] = {"waveform": torch.zeros((1, 2, round(values[5] / 24 * 48000))), "sample_rate": 48000}
            values[7] += "\nNo source audio: silent conditioning reference."
        return tuple(values)


class SelectSubject:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"masks": ("MASK",),
            "selection": (["largest", "leftmost", "rightmost", "index"],),
            "index": ("INT", {"default": 0, "min": 0})}}

    RETURN_TYPES = ("MASK", "H3_SUBJECT_SELECTION")
    RETURN_NAMES = ("selected_mask", "selection")
    FUNCTION = "select"
    CATEGORY = "H3 Character Swap/masks"

    def select(self, masks, selection, index):
        areas = masks.flatten(1).sum(1)
        valid = torch.where(areas > 4)[0].tolist()
        if not valid:
            raise ValueError("SAM found no subject. Adjust the subject text and inspect the mask preview.")
        if selection == "largest":
            chosen = max(valid, key=lambda i: areas[i].item())
        elif selection in {"leftmost", "rightmost"}:
            ordered = sorted(valid, key=lambda i: float(torch.where(masks[i] > 0.5)[1].float().mean()))
            chosen = ordered[0 if selection == "leftmost" else -1]
        else:
            if not 0 <= index < len(valid):
                raise ValueError(f"Subject index {index} exceeds {len(valid)} detections.")
            chosen = valid[index]
        return masks[chosen:chosen + 1], {"selection": selection, "index": index}


class ExpandMask:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"masks": ("MASK",), "images": ("IMAGE",),
            "source_frames": ("INT", {"default": 120, "min": 1}),
            "expansion_px": ("INT", {"default": 64, "min": 0, "max": 256,
                "tooltip": "Outward Euclidean radius in prepared-video pixels; identical to the app's mask expansion."})}}

    RETURN_TYPES = ("MASK", "IMAGE")
    RETURN_NAMES = ("generation_mask", "mask_overlay")
    FUNCTION = "expand"
    CATEGORY = "H3 Character Swap/masks"

    def expand(self, masks, images, source_frames, expansion_px):
        if not 1 <= source_frames <= len(images) or len(masks) < source_frames:
            raise ValueError("Masks must cover the complete source timeline.")
        if masks.shape[1:] != images.shape[1:3]:
            raise ValueError("Masks and prepared video dimensions differ.")
        raw = masks[:source_frames].detach().cpu().numpy()
        if not np.isfinite(raw).all():
            raise ValueError("Mask contains non-finite values.")
        missing = np.flatnonzero((raw > .5).sum(axis=(1, 2)) == 0).tolist()
        if missing:
            raise ValueError(f"SAM lost the selected person at frames {missing[:20]}. Correct the selection or split the source shot before generating.")
        expanded = torch.from_numpy(expand_masks(raw, expansion_px))
        if len(images) > source_frames:
            expanded = torch.cat([expanded, expanded[-1:].expand(len(images) - source_frames, -1, -1)])
        overlay = torch.where(expanded[..., None] > 0,
            images.cpu() * .65 + torch.tensor([.1, .9, .4]) * .35, images.cpu())
        return expanded, overlay


class OptionalAnchor:
    """An empty selection is valid, so no dummy image upload is necessary."""
    NONE = "(none)"

    @classmethod
    def INPUT_TYPES(cls):
        root = Path(folder_paths.get_input_directory())
        files = sorted(str(p.relative_to(root)) for p in root.iterdir() if p.is_file())
        files = folder_paths.filter_files_content_types(files, ["image"])
        return {"required": {"image": ([cls.NONE, *files], {"image_upload": True})}}

    RETURN_TYPES = ("IMAGE", "BOOLEAN")
    RETURN_NAMES = ("anchor", "use_anchor")
    FUNCTION = "load"
    CATEGORY = "H3 Character Swap"

    @classmethod
    def VALIDATE_INPUTS(cls, image):
        if image == cls.NONE:
            return True
        from nodes import LoadImage
        return LoadImage.VALIDATE_INPUTS(image)

    @classmethod
    def IS_CHANGED(cls, image):
        if image == cls.NONE:
            return cls.NONE
        from nodes import LoadImage
        return LoadImage.IS_CHANGED(image)

    def load(self, image):
        if image == self.NONE:
            return None, False
        from nodes import LoadImage
        return LoadImage().load_image(image)[0], True


class Shot:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"source_video": ("VIDEO",), "source": ("IMAGE",),
            "source_audio": ("AUDIO",), "source_frames": ("INT",), "fps": ("FLOAT",),
            "reference": ("IMAGE",), "use_anchor": ("BOOLEAN", {"default": False}),
            "use_mask": ("BOOLEAN", {"default": True}),
            "shot_id": ("STRING", {"default": "character-swap"})},
            "optional": {"anchor": ("IMAGE",), "body_mask": ("MASK",)}}

    RETURN_TYPES = ("H3_RELAY_SHOT",)
    FUNCTION = "prepare"
    CATEGORY = "H3 Character Swap"

    def prepare(self, shot_id, use_anchor, use_mask, **kwargs):
        from .relay.inputs import Shot as RelayShot
        from .relay.records import digest
        import comfy.utils
        if use_anchor and kwargs.get("anchor") is None:
            raise ValueError("Anchor conditioning requires an anchor image.")
        if use_mask and kwargs.get("body_mask") is None:
            raise ValueError("Masked generation requires a person mask.")
        if use_anchor:
            # Match the app's ImageScale exactly, including aspect behavior.
            h, w = kwargs["source"].shape[1:3]
            kwargs["anchor"] = comfy.utils.common_upscale(kwargs["anchor"][:1].movedim(-1, 1), w, h,
                                                         "lanczos", "disabled").movedim(1, -1)
        else:
            kwargs["anchor"] = kwargs["source"][:1]
        if not use_mask:
            kwargs["body_mask"] = torch.zeros(kwargs["source"].shape[:3])
        shot = RelayShot().prepare(**kwargs)[0]
        shot["generation"] = {"use_anchor": use_anchor, "use_mask": use_mask}
        shot["key"] = digest(["character-swap-conditioning-v1", shot["key"], shot["generation"]])
        shot["shot_id"] = shot_id
        return (shot,)


class RecoverMasks:
    """Repeat the app's per-frame SAM recovery only where tracking is empty."""
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"masks": ("MASK",), "images": ("IMAGE",),
            "model": ("MODEL",), "conditioning": ("CONDITIONING",),
            "selection": ("H3_SUBJECT_SELECTION",), "source_frames": ("INT",),
            "recover_empty_frames": ("BOOLEAN", {"default": True})}}

    RETURN_TYPES = ("MASK", "STRING")
    RETURN_NAMES = ("recovered_masks", "report")
    FUNCTION = "recover"
    CATEGORY = "H3 Character Swap/masks"

    def recover(self, masks, images, model, conditioning, selection, source_frames, recover_empty_frames):
        import json
        import comfy.model_management
        from comfy_extras.nodes_sam3 import SAM3_Detect
        if len(masks) < source_frames or len(images) < source_frames:
            raise ValueError("Tracking must cover the complete source timeline")
        result = masks.clone().cpu()
        empty = torch.where((result[:source_frames] > .5).flatten(1).sum(1) == 0)[0].tolist()
        recovered = []
        if recover_empty_frames:
            for frame in empty:
                comfy.model_management.throw_exception_if_processing_interrupted()
                detection = SAM3_Detect.execute(model=model, image=images[frame:frame+1],
                    conditioning=conditioning, threshold=.35, refine_iterations=2, individual_masks=True)[0]
                result[frame] = SelectSubject().select(detection, **selection)[0][0].cpu()
                recovered.append(frame)
        report = json.dumps({"empty_tracking_frames": empty, "recovered_frames": recovered})
        return {"result": (result, report), "ui": {"text": [report]}}
