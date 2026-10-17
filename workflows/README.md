# H3 Character Swap for ComfyUI

A focused workflow for replacing a person with a reference character while preserving the source performance and soundtrack. It uses the same V1 character LoRA, FL2V Turbo 8-step recipe, mask expansion and relay continuation as the app.

- **[H3 Character Swap.json](H3%20Character%20Swap.json)**: open in the ComfyUI canvas. The graph exposes the native loop, model chain, sampling and video output.
- **[H3 Character Swap.api.json](H3%20Character%20Swap.api.json)**: the matching API graph, exported by the ComfyUI frontend. It includes SAM detection, tracking, empty-frame recovery and expansion.
- **[../templates/render.json](../templates/render.json)**: the app's smaller render API template. The app supplies its reviewed NPZ mask instead of rerunning SAM; generation uses the same nodes.

## Install

The pinned target is Linux x86-64 with an NVIDIA GPU, Python 3.12, PyTorch 2.11.0 + CUDA 12.8, ComfyUI commit `79be670e2d9be63e238785af307369d2b9039ed1`, and frontend `1.53.6`. Native loop serialization requires this qualified runtime; an older minimum-version label alone is insufficient. Exact dependencies are in [runtime/comfyui](../runtime/comfyui).

From the repository root, build a new immutable recipe and install its canonical Relay package into an isolated ComfyGit environment:

```sh
python3 runtime/build_recipe.py /path/to/new-recipe
cg materialize runtime/comfyui --name h3-character-swap --torch-backend cu128 --models skip
cg -e h3-character-swap node dev-link h3-relay --path /path/to/new-recipe/nodes/h3_relay
```

Keep the extracted recipe available while the environment uses it. The archive's repository, commit and SHA-256 are in [runtime/relay-lock.json](../runtime/relay-lock.json). The app builds from that same archive; it does not maintain a separate copy of H3 generation code. FFmpeg and FFprobe must be available on PATH. The app worker adds a small session integration package that is unnecessary for standalone ComfyUI use.

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

Use `--reserve-vram 0 --disable-dynamic-vram --enable-triton-backend --cache-classic --highvram` for full residency. If that configuration exceeds the physical GPU, retain the measured failure before choosing model offloading. The runtime includes a narrowly versioned INT8 pointer-offset compatibility fix; optimized kernels remain enabled.

## Run the canvas workflow

1. Open the canvas JSON and upload your source video and character reference. Use one continuous shot; split cuts beforehand.
2. The supplied standalone graph starts without an anchor. For an anchored variant, use the canonical `character_swap_graph(use_anchor=True)` recipe and upload its anchor image. The app exposes anchor selection directly.
3. Describe the subject in the SAM text box. Select the largest, leftmost, rightmost or indexed detected person. The selected strategy also controls recovery if tracking loses the subject.
4. Review the green expanded-mask preview at several frame indices. Expansion is a Euclidean radius in prepared-video pixels, defaulting to 64. For a mask-only check, use ComfyUI's partial execution on the mask preview.
5. Replace the starter prompt with a specific description of the reference character’s face, skin, hair, proportions and clothing, followed by the source action to preserve. A generic “replace the person” prompt performed poorly in qualification. `<Picture 1>` is the character reference; `<Picture 2>` is the optional anchor; `<Video 1>` is the source performance. Keep the default 8 steps, CFG 1, `er_sde` sampler and `normal` scheduler for the pinned Turbo LoRA.
6. Run. Native Save Video previews and saves the MP4 under `output/h3-relay/character-swap/`. Finish Windows returns the exact timeline report in execution history.

Preparation converts video to 24 fps and preserves a 32-pixel-aligned source canvas already within the pixel budget. Otherwise it scales and center-crops to that budget. `768` means up to 1 MP; `480` means up to 0.5 MP. Duration 0 uses the whole clip. Positive durations trim before H3 padding; export removes that padding.

Source audio supplies both reference conditioning and the frame-zero guide; the export retains the source soundtrack. Silent inputs receive silence for conditioning. The canonical Python recipe supports `audio_guide=False`, `use_anchor` and `use_mask` choices without introducing a custom sampler/controller.

The relay uses 124-frame windows and 18-frame history by default. Source video and reference audio include H−1 past frames for continuation; the target mask and frame-zero audio guide remain current-window-only. Final output is raw decoded generation. There is no alpha extraction, LTX pass, face pass, backplate generation, or pasted-mask finishing.

## API use

The standalone API uses descriptive IDs: `source.inputs.file`, `reference.inputs.image`, `reference_conditioning.inputs.prompt`, `plan.inputs.seed`, `select` and `expand_mask`. Submit the API JSON as the `prompt` field to `/prompt`, then read `/history/{prompt_id}`. `finish.h3_relay_report[0]` contains timeline metadata; native `save.images[0]` identifies the saved video.

The app obtains its graph from the pinned `h3_relay/workflows.py`, binds prepared source pixels and reviewed masks, and preserves stable IDs at its external boundary: `805` is the plan, `801` reference conditioning, `803` window inputs, `807` the report and `809` native video saving. Segment operations use the explicit `segment` schedule, optional `H3RelayLoadHistory`, and `H3RelaySaveHistory` for 16-bit FFV1 continuation masters. [relay_contract.py](../h3workbench/relay_contract.py) validates retained output descriptors.

App and worker must use the same recipe revision. An already-running older installation is not upgraded by changing these source files.

## Scope and licenses

This package prepares the main character-swap flow for release; visual quality remains experimental. The generic-prompt test retained much of the source identity, even though the graph executed correctly. An anchor improved the opening frame but did not ensure sustained identity. Review identity, limbs, occlusions and window boundaries on your own material. See [VALIDATION.md](VALIDATION.md) for actual runtime evidence and limits.

The canonical Relay package is GPL-3.0; its source archive includes component provenance and licenses. The NVIDIA kernel derivative retains its Apache-2.0 notice. The application is separately MIT-licensed; models retain their upstream terms. See [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md).
