"""Body-only relay execution and source-audio export."""
import json
import re
import subprocess
import tempfile
from pathlib import Path

import torch
from comfy_execution.graph_utils import GraphBuilder
from . import records
from .inputs import audio_slice


def preview(frames, audio, filename):
    import folder_paths
    from scipy.io import wavfile
    destination = Path(folder_paths.get_output_directory()) / "H3-Character-Swap" / filename
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="h3-swap-audio-") as tmp:
        args = ["ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
                "-s", f"{frames.shape[2]}x{frames.shape[1]}", "-r", "24", "-i", "pipe:0"]
        if audio is not None:
            wav = Path(tmp) / "audio.wav"
            wavfile.write(wav, audio["sample_rate"], audio["waveform"][0].cpu().T.numpy())
            args += ["-i", str(wav), "-map", "0:v:0", "-map", "1:a:0", "-c:a", "aac", "-b:a", "192k"]
        args += ["-c:v", "libx264", "-preset", "fast", "-crf", "16", "-pix_fmt", "yuv420p",
                 "-t", str(len(frames) / 24), "-movflags", "+faststart", str(destination)]
        proc = subprocess.Popen(args, stdin=subprocess.PIPE)
        try:
            for frame in frames:
                proc.stdin.write(frame.clamp(0, 1).mul(255).round().to(torch.uint8).cpu().numpy().tobytes())
        finally:
            proc.stdin.close()
            if proc.wait() != 0:
                raise RuntimeError("Character-swap output encoding failed")
    return {"filename": filename, "subfolder": "H3-Character-Swap", "type": "output"}


class Checkpoint:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"frames": ("IMAGE",), "entry": ("H3_RELAY_ENTRY",)}}
    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "save"
    CATEGORY = "H3 Character Swap/internal"

    def save(self, frames, entry):
        records.save(entry["body_key"], {"frames": frames}, {"entry": entry})
        return (frames,)


class Restore:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"key": ("STRING",)}}
    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "load"
    CATEGORY = "H3 Character Swap/internal"

    def load(self, key):
        return (records.load(key)[0]["frames"],)


class Append:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"state": ("H3_RELAY_STATE",), "entry": ("H3_RELAY_ENTRY",), "frames": ("IMAGE",)}}
    RETURN_TYPES = ("H3_RELAY_STATE",)
    FUNCTION = "append"
    CATEGORY = "H3 Character Swap/internal"

    def append(self, state, entry, frames):
        count = entry["source_end_exclusive"] - entry["source_start"]
        overlap = entry["discarded_overlap"]
        if len(frames) < count:
            raise ValueError("Decoded window is shorter than its source range")
        if len(state["body"]) != entry["source_start"] + overlap:
            raise ValueError("Window assembly would create a gap or duplicate")
        return ({**state, "body": torch.cat([state["body"], frames[overlap:count].cpu()]),
                 "records": [*state["records"], entry]},)


class Run:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"models": ("H3_RELAY_MODELS",), "shot": ("H3_RELAY_SHOT",),
            "entries": ("H3_RELAY_ENTRIES",), "state": ("H3_RELAY_STATE",), "index": ("INT",)}}
    RETURN_TYPES = ("H3_RELAY_STATE",)
    FUNCTION = "run"
    CATEGORY = "H3 Character Swap/internal"

    def run(self, models, shot, entries, state, index):
        if index >= len(entries):
            return (state,)
        graph = GraphBuilder()
        entry = entries[index]
        if records.exists(entry["body_key"]):
            frames = graph.node("H3CharacterSwapRestore", key=entry["body_key"]).out(0)
        else:
            render = graph.node("H3CharacterSwapBodyWindow", models=models, shot=shot, entry=entry, state=state)
            frames = graph.node("H3CharacterSwapCheckpoint", frames=render.out(0), entry=entry).out(0)
        appended = graph.node("H3CharacterSwapAppend", state=state, entry=entry, frames=frames)
        following = graph.node("H3CharacterSwapRun", models=models, shot=shot, entries=entries,
                               state=appended.out(0), index=index + 1)
        return {"result": (following.out(0),), "expand": graph.finalize()}


class Controller:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"models": ("H3_RELAY_MODELS",), "shot": ("H3_RELAY_SHOT",), "plan": ("H3_RELAY_PLAN",)}}
    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")
    RETURN_TYPES = ("H3_RELAY_STATE", "H3_RELAY_SHOT", "STRING")
    RETURN_NAMES = ("completed_windows", "shot", "status")
    FUNCTION = "run"
    CATEGORY = "H3 Character Swap"

    def run(self, models, shot, plan):
        entries = records.dependency_keys(shot["key"], models["key"], plan["windows"], plan["body"])
        records.begin(shot["key"], {entry["body_key"] for entry in entries})
        if len(entries) > 1 and entries[1]["history_frames"] >= 18:
            from ..vendor.context_loop.sliding_context import require_sliding_history_support
            require_sliding_history_support()
        status = {"source_frames": shot["source_frames"], "planned_windows": len(entries),
            "reference_history_version": records.REFERENCE_HISTORY_VERSION,
            "windows": [dict(entry, cached=records.exists(entry["body_key"])) for entry in entries]}
        state = {"body": shot["source"][:0], "records": [], "status": status}
        graph = GraphBuilder()
        run = graph.node("H3CharacterSwapRun", models=models, shot=shot, entries=entries, state=state, index=0)
        return {"result": (run.out(0), shot, json.dumps(status, indent=2)),
                "expand": graph.finalize(), "ui": {"text": [json.dumps(status)]}}


class Assemble:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"state": ("H3_RELAY_STATE",), "shot": ("H3_RELAY_SHOT",),
                             "filename_prefix": ("STRING", {"default": "character-swap"})}}
    RETURN_TYPES = ("IMAGE", "AUDIO", "STRING")
    RETURN_NAMES = ("generated_frames", "source_audio", "report")
    FUNCTION = "assemble"
    CATEGORY = "H3 Character Swap"
    OUTPUT_NODE = True

    def assemble(self, state, shot, filename_prefix):
        import folder_paths
        if not re.fullmatch(r"[A-Za-z0-9_-]+", filename_prefix):
            raise ValueError("Use letters, numbers, dashes and underscores in the output prefix")
        frames = state["body"]
        if len(frames) != shot["source_frames"]:
            raise ValueError("Completed video does not cover the exact source timeline")
        audio = audio_slice(shot["source_audio"], 0, len(frames))
        identity = records.digest([entry["body_key"] for entry in state["records"]])[:16]
        file = preview(frames, audio, filename_prefix + "-" + identity + ".mp4")
        report = {"version": records.VERSION, "body_output_version": records.BODY_OUTPUT_VERSION,
            "body_composited": False, "source_frames": shot["source_frames"], "delivered_frames": len(frames),
            "fps": 24, "complete": True, "output": file, "generation": shot["generation"],
            "windows": state["records"], "status": state["status"]}
        path = Path(folder_paths.get_output_directory()) / file["subfolder"] / (Path(file["filename"]).stem + ".json")
        path.write_text(json.dumps(report, indent=2))
        return {"result": (frames, audio, json.dumps(report, indent=2)),
                "ui": {"text": [json.dumps(report)], "h3_character_swap": [report]}}
