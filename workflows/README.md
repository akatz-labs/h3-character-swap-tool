# H3 Character Swap for ComfyUI

A focused workflow for replacing a person with a reference character while preserving the source performance and soundtrack. It uses the same V1 character LoRA, FL2V Turbo 8-step recipe, mask expansion and relay continuation as the app.

- **[H3 Character Swap.json](H3%20Character%20Swap.json)**: open in the ComfyUI canvas. Five colored sections and six Markdown notes explain the flow.
- **[H3 Character Swap.api.json](H3%20Character%20Swap.api.json)**: the matching API graph, exported by the ComfyUI frontend. It includes SAM detection, tracking, empty-frame recovery and expansion.
- **[../templates/render.json](../templates/render.json)**: the app's smaller render API template. The app supplies its reviewed NPZ mask instead of rerunning SAM; generation uses the same nodes.

## Install

The qualified target is Linux x86-64 with an NVIDIA GPU, Python 3.12, PyTorch 2.11.0 + CUDA 12.8, and ComfyUI commit `79be670e2d9be63e238785af307369d2b9039ed1`. Use an isolated environment rather than modifying an active ComfyUI installation. Exact dependencies are in [runtime/comfyui](../runtime/comfyui).

With ComfyGit installed, from the repository root:

```sh
cg materialize runtime/comfyui --name h3-character-swap --torch-backend cu128 --models skip
cg -e h3-character-swap node dev-link h3_character_swap --path worker_nodes/h3_character_swap
```

Keep the checkout available: `dev-link` links the node folder. Alternatively, copy `worker_nodes/h3_character_swap` into the pinned ComfyUI installation's `custom_nodes` directory. This is the only additional node package. FFmpeg and FFprobe must be available on PATH. The package does not install models or start background services.

Place the seven model files at the paths below, relative to ComfyUI's model directory. Download URLs, exact sizes, SHA-256 identities and upstream license links are in [models-v1.json](../docker/models-v1.json); the downloadable catalog is [resource-catalog.json](../docker/resource-catalog.json). Model weights are not included.

| Model | Destination |
| --- | --- |
| SAM 3.1 | `checkpoints/sam3.1_multiplex_fp16.safetensors` |
| H3 Ref2VA INT8 | `diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors` |
| FL2V Turbo 8-step | `loras/minimax_h3_fl2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors` |
| Character Swap V1 | `loras/h3-character-swap-v1/h3_character_swap_pro4500_1000.safetensors` |
| H3 Qwen text encoder | `text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors` |
| H3 video VAE | `vae/minimax_h3_video_vae_fp16.safetensors` |
| H3 audio VAE | `vae/minimax_h3_audio_vae_fp32.safetensors` |

Use `--reserve-vram 0 --disable-dynamic-vram --enable-triton-backend --cache-classic`. Add `--highvram` for full residency. The tested 24 GB 4090 exhausted memory while loading the combined LoRAs in full-residency mode; normal ComfyUI model offloading is needed on that test machine. No quality, resolution or duration setting is changed by selecting offloading. The INT8 pointer-offset fix is included and applies only to the qualified comfy-kitchen version.

## Run the canvas workflow

1. Open the canvas JSON and upload your source video and character reference. Use one continuous shot; split cuts beforehand.
2. Leave the optional anchor at **(none)**, or upload a first-frame anchor. The anchor's presence controls its conditioning automatically.
3. Describe the subject in the SAM text box. Select the largest, leftmost, rightmost or indexed detected person. The selected strategy also controls recovery if tracking loses the subject.
4. Review the green expanded-mask preview at several frame indices. Expansion is a Euclidean radius in prepared-video pixels, defaulting to 64. For a mask-only check, use ComfyUI's partial execution on the mask preview.
5. Replace the starter prompt with a specific description of the reference character’s face, skin, hair, proportions and clothing, followed by the source action to preserve. A generic “replace the person” prompt performed poorly in qualification. `<Picture 1>` is the character reference; `<Picture 2>` is the optional anchor; `<Video 1>` is the source performance. Keep the default 8 steps, CFG 1, `er_sde` sampler and `normal` scheduler for the pinned Turbo LoRA.
6. Run. The result node previews the MP4. Files and a JSON timeline report are saved under `output/H3-Character-Swap/`.

Preparation converts video to 24 fps and preserves a 32-pixel-aligned source canvas already within the pixel budget. Otherwise it scales and center-crops to that budget. `768` means up to 1 MP; `480` means up to 0.5 MP. Duration 0 uses the whole clip. Positive durations trim before H3 padding; export removes that padding.

**Audio guide** is enabled by default and uses source audio for reference conditioning and the frame-zero guide. Turning it off changes conditioning; the export still retains the source soundtrack. Silent inputs receive silence for conditioning. The optional anchor and masked diffusion can be disabled independently.

The relay uses 124-frame windows and 18-frame history by default. Source video and reference audio include H−1 past frames for continuation; the target mask and frame-zero audio guide remain current-window-only. Final output is raw decoded generation. There is no alpha extraction, LTX pass, face pass, backplate generation, or pasted-mask finishing.

## API use

Upload the source video and reference to ComfyUI, then set node `30.inputs.file` and `9.inputs.image` to their uploaded names. Set `309.inputs.image` to `(none)` or an uploaded anchor. Node `801` contains the prompt, seed and audio-guide switch. Node `104` chooses the subject; node `110` controls expansion. Submit the API JSON as the `prompt` field to ComfyUI's `/prompt` endpoint and read `/history/{prompt_id}` for completion. The saved output is recorded in node `807` under `h3_character_swap[0].output`.

The app render API additionally replaces file placeholders, source geometry and the saved mask path. Do not submit its placeholder template without adapting those inputs. App and worker must use the same recipe revision; these files do not upgrade an already-running older worker.

## Scope and licenses

This package prepares the main character-swap flow for release; visual quality remains experimental. The generic-prompt test retained much of the source identity, even though the graph executed correctly. An anchor improved the opening frame but did not ensure sustained identity. Review identity, limbs, occlusions and window boundaries on your own material. See [VALIDATION.md](VALIDATION.md) for actual runtime evidence and limits.

The focused node package is GPL-3.0, including its H3 Relay / H3 Performance derivatives and vendored continuation helper. Its [source provenance](../worker_nodes/h3_character_swap/SOURCE_PROVENANCE.json) records the original source hashes. The small NVIDIA kernel derivative retains its Apache-2.0 notice. The application is separately MIT-licensed; models retain their upstream terms. See [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md).
