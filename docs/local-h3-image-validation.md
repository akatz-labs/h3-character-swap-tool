# Optional local H3 Image Edit

Validated on 2026-11-16 with the shared editor and source worker on Linux,
RTX 4090 (24 GB), using an existing model store and shared uv cache.
No model weights were downloaded again. No hosted services were changed.

## Recipe

- Optional feature: `h3-image-edit-v1` in `docker/resource-catalog.json`.
- Comfy-Org MiniMax-H3-Image pinned revision
  `0bb149f51e3156ed4b90dd45d5620346bfe1df8f`, INT8 ConvRot checkpoint and image VAE.
- Existing Qwen H3 text encoder; Akatz Character Swap V1.5 at **1.0**.
- Native `MiniMaxH3ImageConditioning`, including VAE reference latents;
  source is Picture 1 and up to eight additional references follow it.
- Six steps, Euler/simple, CFG 1. The image checkpoint has distilled heads.
- Source aspect preserved, rounded to the model's 32-pixel canvas alignment.
- ComfyUI pinned to `c844cb672186f187bf9455e2808821417a10d296`;
  Python dependency lock updated to its native image-node runtime requirements.

## Actual guide results

Each job used a source frame and one character reference, the shared
character-swap helper, the local worker queue, and no OpenAI inference.

| Quality | Actual canvas | App acceptance through completion |
| --- | --- | --- |
| Low / 1 MP | 736 × 1344 | 71.5 seconds |
| Medium / 2 MP | 1056 × 1920 | 51.2 seconds |
| High / 4 MP | 1472 × 2688 | 55.1 seconds |

These are individual validation runs, not performance guarantees. The first
run includes runtime startup and verification; subsequent runs reuse it.
The controller's preflight and process log verified dynamic VRAM, unrestricted
resources and zero reserved VRAM. The model reported **208 patches attached**;
no unmatched LoRA-key or shape-mismatch errors were logged.
Images returned to the guide history and the latest result became the active
version. Visual inspection confirmed an anime character replacement with the
source framing/background retained; identity/style fidelity still needs human
review and varies with prompts and references.

The live check caught a pre-existing disconnect between the offloading setting
and runtime launch flags. The portable runtime now starts with offloading and
recycles only its own idle process if the next job requests different residency.
No active job is interrupted by that transition. An initial successful 2 MP
run before this correction is retained separately from the three runs above.

## Checks

- 78 focused Python tests passed, including optional dependencies, guide API
  routing, quality sizing, hosted rejection, and owned residency transitions.
- 230 shared-editor tests and 12 shared-runtime tests passed.
- Runtime and frontend builds passed.
- Live Chromium verified the model options, all three quality labels,
  restored model/quality after closing/reopening, and rendered guide history.
- Local settings → Models displayed both optional image weights as Installed,
  alongside their sizes and the existing model-management controls.
- Optional weights are excluded from the six-model minimum video setup.
- Cancellation of running local image jobs uses the shared executor's existing
  cooperative cancellation path; API-backed OpenAI jobs retain their policy.

The current local comparison contains a project named **H3 Image local validation**
with the original source, reference and four generated guide versions. The last
three versions cover Low, High, then Medium with dynamic VRAM enabled.
This change has not been pushed or publicly released. Video recipes and the
hosted image provider were not replaced; this record qualifies local image guides.
