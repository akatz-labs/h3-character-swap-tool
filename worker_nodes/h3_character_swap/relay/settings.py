"""The model, generation and continuation controls used by character swaps."""
import json
import torch

from ..identity import configuration_key
from ..planning import WINDOWS, HISTORY, plan_windows


class Models:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"model": ("MODEL",), "clip": ("CLIP",),
                             "video_vae": ("VAE",), "audio_vae": ("VAE",)},
                "hidden": {"unique_id": "UNIQUE_ID", "execution_prompt": "PROMPT"}}

    RETURN_TYPES = ("H3_RELAY_MODELS",)
    FUNCTION = "bundle"
    CATEGORY = "H3 Character Swap"

    def bundle(self, unique_id=None, execution_prompt=None, **models):
        stub = torch.zeros(1, 1, 1, 3)
        models["key"] = configuration_key(stub, stub, {}, execution_prompt, unique_id)
        return (models,)


class Settings:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "prompt": ("STRING", {"multiline": True, "default": "Replace the selected person in <Video 1> with the character in <Picture 1>. Preserve the source performance, scene and audio timing."}),
            "seed": ("INT", {"default": 194731, "min": 0, "max": 2**64 - 1}),
            "audio_guide": ("BOOLEAN", {"default": True, "tooltip": "Use the source soundtrack for reference conditioning and the frame-zero audio guide. Output keeps source audio either way."}),
            "steps": ("INT", {"default": 8, "min": 1, "max": 100}),
            "cfg": ("FLOAT", {"default": 1.0, "min": 0, "max": 20}),
            "sampler_name": ("STRING", {"default": "er_sde"}),
            "scheduler": ("STRING", {"default": "normal"}),
            "activation_memory_gib": ("FLOAT", {"default": 17.0, "min": 0, "max": 128, "step": 0.5,
                "tooltip": "Estimated sampling workspace for ComfyUI memory placement; not a VRAM limit or reservation."}),
        }}

    RETURN_TYPES = ("H3_RELAY_BODY_SETTINGS",)
    FUNCTION = "settings"
    CATEGORY = "H3 Character Swap"

    def settings(self, audio_guide, **settings):
        return ({**settings, "audio": audio_guide},)


class WindowPlan:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"shot": ("H3_RELAY_SHOT",), "settings": ("H3_RELAY_BODY_SETTINGS",),
            "window_frames": ([str(v) for v in WINDOWS], {"default": "124"}),
            "history_frames": ([str(v) for v in HISTORY], {"default": "18"})}}

    RETURN_TYPES = ("H3_RELAY_PLAN", "STRING")
    RETURN_NAMES = ("plan", "timeline")
    FUNCTION = "plan"
    CATEGORY = "H3 Character Swap"

    def plan(self, shot, settings, window_frames, history_frames):
        windows = plan_windows(shot["source_frames"], int(window_frames), int(history_frames))
        summary = json.dumps(windows, indent=2)
        return {"result": ({"windows": windows, "body": settings}, summary), "ui": {"text": [summary]}}
