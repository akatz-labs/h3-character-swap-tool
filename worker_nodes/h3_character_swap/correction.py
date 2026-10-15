"""SAM point corrections used by the app's mask editor."""
import hashlib
import json
from pathlib import Path
import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image
import comfy.model_management
import comfy.utils
import folder_paths
class RefinePersonMask:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "model": ("MODEL",),
                "image": ("IMAGE",),
                "positive_coords": ("STRING", {"default": "[]"}),
                "negative_coords": ("STRING", {"default": "[]"}),
                "prior_file": ("STRING", {"default": ""}),
                "frame": ("INT", {"default": 0, "min": 0}),
            }
        }

    RETURN_TYPES = ("MASK",)
    FUNCTION = "refine"
    CATEGORY = "H3 Workbench/masks"

    @classmethod
    def IS_CHANGED(cls, prior_file, **kwargs):
        if not prior_file:
            return "no-prior"
        return hashlib.sha256(cls.prior_path(prior_file).read_bytes()).hexdigest()

    @staticmethod
    def prior_path(name):
        root = Path(folder_paths.get_input_directory()).resolve()
        p = (root / name).resolve()
        if not p.is_relative_to(root):
            raise ValueError("Prior mask must be a staged input")
        return p

    def refine(self, model, image, positive_coords, negative_coords, prior_file, frame):
        if len(image) != 1:
            raise ValueError("Refine exactly one correction frame")
        _, h, w, _ = image.shape
        pos, neg = json.loads(positive_coords), json.loads(negative_coords)
        if not pos:
            raise ValueError(
                "An include point or derived prior-mask anchor is required"
            )
        comfy.model_management.load_model_gpu(model)
        device = comfy.model_management.get_torch_device()
        dtype = model.model.get_dtype()
        sam = model.model.diffusion_model
        pixels = comfy.utils.common_upscale(
            image[..., :3].movedim(-1, 1), 1008, 1008, "bilinear", crop="disabled"
        ).to(device=device, dtype=dtype)
        points = {
            "point_coords": torch.tensor(
                [[[p["x"] / w * 1008, p["y"] / h * 1008] for p in pos + neg]],
                dtype=dtype,
                device=device,
            ),
            "point_labels": torch.tensor(
                [[1] * len(pos) + [0] * len(neg)], dtype=torch.int32, device=device
            ),
        }
        logits = None
        if prior_file:
            with np.load(self.prior_path(prior_file), allow_pickle=False) as z:
                raw = z["mask"]
                if raw.ndim != 3 or frame >= len(raw):
                    raise ValueError("Prior mask frame is outside the saved timeline")
                prior = raw[frame] > 0.5
            if prior.any():
                prior = np.asarray(
                    Image.fromarray(prior).resize((w, h), Image.Resampling.NEAREST)
                ).copy()
                logits = (
                    torch.from_numpy(prior).to(device=device, dtype=dtype)[None, None]
                    * 12
                    - 6
                )
                logits = F.interpolate(
                    logits, size=(1008, 1008), mode="bilinear", align_corners=False
                )
        # Keep the user's positive/negative constraints in both refinement passes.
        for _ in range(2):
            logits = sam.forward_segment(
                pixels, point_inputs=points, mask_inputs=logits
            )
        mask = (
            F.interpolate(logits, size=(h, w), mode="bilinear", align_corners=False)[0]
            > 0
        )
        return (mask.float().cpu(),)

