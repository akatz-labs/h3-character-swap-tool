"""Workbench edit regions and Relay history for Viggle's clean-audio latent."""
import torch.nn.functional as F
import comfy.nested_tensor
import node_helpers
from .remake import RemakePrefix


class RemakeEditMask:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"latent": ("LATENT",), "mask": ("MASK",), "vae": ("VAE",), "source": ("IMAGE",)}}
    RETURN_TYPES = ("LATENT",)
    FUNCTION = "run"
    CATEGORY = "H3 Workbench"

    def run(self, latent, mask, vae, source):
        video, audio = latent["samples"].unbind()
        _, audio_mask = latent["noise_mask"].unbind()
        source_video = vae.encode(source[..., :3])
        if source_video.shape != video.shape:
            raise ValueError("Mask source must match the target video latent")
        region = F.adaptive_max_pool3d(mask[None, None].float(), video.shape[2:]).expand_as(video)
        return ({**latent, "samples": comfy.nested_tensor.NestedTensor((source_video, audio)),
                 "noise_mask": comfy.nested_tensor.NestedTensor((region, audio_mask))},)


class RemakeHistory:
    """17 clean prior frames plus one regenerated boundary; trim all 18 later."""
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"positive": ("CONDITIONING",), "latent": ("LATENT",),
            "vae": ("VAE",), "frames": ("IMAGE",), "history_frames": ("INT", {"default": 18, "min": 1, "max": 18})}}
    RETURN_TYPES = ("CONDITIONING", "LATENT")
    FUNCTION = "run"
    CATEGORY = "H3 Workbench"

    def run(self, positive, latent, vae, frames, history_frames):
        if history_frames not in (1, 18) or len(frames) != history_frames:
            raise ValueError("Continuation requires exactly its frozen 1 or 18 history frames")
        if history_frames == 18:
            latent, = RemakePrefix().run(vae, latent, frames, 17, 0)
        boundary = {"resolved_frame_index": history_frames - 1, "latent": vae.encode(frames[-1:, ..., :3])}
        positive = node_helpers.conditioning_set_values(positive, {
            "minimax_keyframes": list(positive[0][1].get("minimax_keyframes", [])) + [boundary]})
        return positive, latent
