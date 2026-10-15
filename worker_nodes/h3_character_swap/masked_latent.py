"""H3 masked diffusion; extracted from the qualified body-swap runtime."""
import json
import torch
import comfy.conds
from comfy.nested_tensor import NestedTensor
import node_helpers
from .mask_geometry import pool_person_mask, prune_guide_layout
CATEGORY = "H3 Character Swap/internal"
def video_latent(latent):
    x = latent["samples"]
    if not x.is_nested or len(x.tensors) != 2 or x.tensors[0].shape[1] != 24:
        raise ValueError("Expected native H3 video/audio latent")
    return x.tensors


class MaskedRegeneration:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"latent": ("LATENT",), "vae": ("VAE",), "image": ("IMAGE",), "mask": ("MASK",)}}
    RETURN_TYPES = ("LATENT",)
    FUNCTION = "prepare"
    CATEGORY = CATEGORY

    def prepare(self, latent, vae, image, mask):
        video, audio = video_latent(latent)
        mask_v, holes = pool_person_mask(mask, video.shape)
        source = vae.encode(image)
        if source.shape != video.shape:
            raise ValueError("Source VAE shape does not match target")
        result = dict(latent)
        result["samples"] = NestedTensor([source, audio])
        result["noise_mask"] = NestedTensor([mask_v, torch.ones_like(audio)])
        proof = {"method": "masked-regeneration", "target_video_shape": list(source.shape), "regenerated_video_tokens": int(holes.sum()), "total_video_tokens": holes.numel(), "outside_mask": "source VAE latent preserved by native H3 inpaint sampler", "inside_mask": "full denoise strength 1"}
        print("H3_PERSON_MASK_PREPARE " + json.dumps(proof), flush=True)
        return {"ui": {"text": [json.dumps(proof)]}, "result": (result,)}

