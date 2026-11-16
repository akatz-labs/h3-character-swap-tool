# Pika + Character Swap V1.5 — standalone ComfyUI

Use these directly in ComfyUI; the character-swap editor and its backend are not required.

| Download | What it does |
| --- | --- |
| [Pika Character Swap.json](Pika%20Character%20Swap.json) | Source video + character reference + prompt. |
| [Pika Guided Character Swap.json](Pika%20Guided%20Character%20Swap.json) | Adds an edited scene frame at an explicit frame index using native **MiniMaxH3AddGuide**. |

Download the raw JSON, then drag it onto the ComfyUI canvas. The `.api.json`
companions are for API clients, not the recommended visual editing entry point.

## Install once

Use ComfyUI with native MiniMax H3 support. These exports were opened and
round-tripped with ComfyUI `c844cb672186f187bf9455e2808821417a10d296`
and frontend **1.59.1**. The full pinned environment is in
[runtime/comfyui](../../runtime/comfyui).

Install [H3 Relay](https://github.com/akatz-ai/h3-relay) revision
`8c313cc6bd23298be3b475d95a3af4cde6e7f10b` for source preparation and memory helpers.
The exact source is bundled in this repository, so installation does not depend
on an unpinned custom-node update. From the cloned repository root:

```sh
# Replace this with your existing ComfyUI installation.
comfy_dir=/path/to/ComfyUI
mkdir -p "$comfy_dir/custom_nodes/h3-relay"
tar -xzf vendor/h3-relay-8c313cc6bd23.tar.gz \
  -C "$comfy_dir/custom_nodes/h3-relay" --strip-components=1
```

Use a fresh custom-node directory; do not overlay another version. Follow that
package's dependency instructions using ComfyUI's Python environment, ensure
FFmpeg/ffprobe are on PATH, then restart ComfyUI. Existing ComfyGit users can
instead dev-link the extracted package into their chosen environment. The app's
worker/session integration nodes are not needed. Neither workflow needs SAM.

## Model downloads

Download these six files. Paths below are relative to **ComfyUI/models/**, or to
an existing model store configured through ComfyUI's extra model paths. The
[catalog](../../docker/resource-catalog.json) includes pinned URLs, sizes,
checksums and license references. No weights or user media are bundled.

| Model | Download | Save as |
| --- | --- | --- |
| H3 text encoder | [Pinned file](https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/e5eb578a89295337b8ff433a035929ce0279e0b6/text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors) | `text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors` |
| H3 audio VAE | [Pinned file](https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/e5eb578a89295337b8ff433a035929ce0279e0b6/vae/minimax_h3_audio_vae_fp32.safetensors) | `vae/minimax_h3_audio_vae_fp32.safetensors` |
| H3 video VAE | [Pinned file](https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/e5eb578a89295337b8ff433a035929ce0279e0b6/vae/minimax_h3_video_vae_fp16.safetensors) | `vae/minimax_h3_video_vae_fp16.safetensors` |
| MiniMax H3 FL2VA | [Pinned file](https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/54472221d27c695fca9621b0c3ed52f06c4af707/diffusion_models/minimax_h3_fl2va_int8_convrot.safetensors) | `diffusion_models/minimax_h3_fl2va_int8_convrot.safetensors` |
| Character Swap LoRA V1.5 | [Pinned file](https://huggingface.co/akatz-ai/MiniMax-H3-Character-Swap-LoRA/resolve/0f409f0c2a18f123f3985041ec52982c813f5a9c/h3_character_swap_v1.5.safetensors) | `loras/h3-v2-challenge-eval-20261013/v2-1000.safetensors` |
| Pika H3 Flex FL2VA | [Pinned file](https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/54472221d27c695fca9621b0c3ed52f06c4af707/loras/pika_h3_flex_fl2va_v1_bf16.safetensors) | `loras/pika_h3_flex_fl2va_v1_bf16.safetensors` |

The V1.5 download is named `h3_character_swap_v1.5.safetensors`; the supplied
loader uses the catalog alias `h3-v2-challenge-eval-20261013/v2-1000.safetensors`.
Either save it at that exact subfolder/name or select your downloaded filename
in **Character Swap V1.5**. It is the same weight file.

## Run a swap

1. Upload a short source video and character reference in **User inputs**.
2. Edit **Swap instructions**. `<Video 1>` is the source performance;
   `<Picture 1>` is the character reference. A short, specific instruction is a
   useful starting point; describe which person should change in a multi-person shot.
3. Keep the default first **5 seconds**, **480** resolution mode (up to **0.5 MP**)
   for a first test. Aspect ratio is fitted to the model's 32-pixel grid.
4. In the guided variant, upload an edited scene frame and set **frame_idx** in
   **Add guide**. Index 0 is the first output frame, at the prepared **24 fps**.
5. Run. **Save result** previews and saves an MP4 under `output/h3-pika/`.

The model stack is **H3 FL2VA INT8 + Pika Flex 1.0 + Character Swap V1.5 0.8**.
Sampling uses **6 steps, LCM, simple, CFG 1**. Both LoRA strengths remain editable.
The memory helpers are enabled; use dynamic VRAM/model offloading on 24 GB GPUs.
Actual capacity depends on the clip length, dimensions and reference sizes.

Guide images go only to **MiniMaxH3AddGuide**, not to the prompt's Picture list.
The guide is resized/cropped to the prepared scene dimensions, so provide matching
composition/aspect. To add another timed guide, duplicate its Load Image, resize
and Add Guide nodes, then chain conditioning through the new Add Guide. Keep the
same latent connected to every guide node.

Source audio conditions the model and is muxed back into the output. Silent video
receives silence for conditioning. Export removes model padding to restore the
prepared source frame count. Output is raw generated video, not a masked composite.

## Scope and validation

These are **single-window** examples: no SAM tracking, masks, segment database or
automatic multi-window continuation. Keep prepared model length at or below
**362 frames (about 15 seconds at 24 fps)**; start shorter on limited hardware.
Set duration to 0 only when the entire source is short enough. The editor handles
longer videos as segments with generated history.

See [VALIDATION.md](VALIDATION.md) for checks and limitations. Older Turbo/Ref2VA
Relay canvases remain available in the [workflow inventory](../README.md).
Models retain their upstream terms; H3 Relay is GPL-3.0. See the repository's
[third-party notices](../../THIRD_PARTY_NOTICES.md).

[Akatz Labs](https://akatz.ai/) · [Hugging Face](https://huggingface.co/akatz-ai) ·
[GitHub](https://github.com/akatz-ai) · [X](https://x.com/akatz_ai) ·
[Sponsors](https://github.com/sponsors/akatz-ai) ·
[Patreon](https://www.patreon.com/Akatz) · [Ko-fi](https://ko-fi.com/akatz)
