# Schedule update — 2026-11-17

Promoted the corrected Pika schedule with both LoRAs at 1.0. Both canvases
were reloaded and serialized through the real ComfyUI frontend; their exported
inputs match the API graphs, including the exact six-step ManualSigmas values.
55 focused Python tests passed. The shared core builds successfully. This update
does not include a new GPU quality comparison.

# Standalone Pika validation — 2026-11-16

Both workflows use the editor's `h3workbench.guide_render.graph` model/sampler
recipe. The standalone builder substitutes native uploads, source preparation,
an editable prompt node and source-length trimming. It does not change the
Pika/V1.5 strengths or sampling schedule.

Verified for both downloads:

- Imported API graphs through the actual ComfyUI frontend, arranged the canvas,
  exported canvas JSON, then reloaded it in a separate browser context.
- Every executable node type resolved. The reloaded canvas exported the same
  executable classes and inputs as the API file, including dynamic reference
  inputs and Save Video's format/codec widgets.
- Native server-side `execution.validate_prompt` accepted both graphs with
  synthetic source/reference inputs and the existing model inventory.
- CPU source preparation produced 22 padded frames from a 12-frame, 24 fps clip;
  output trimming returned exactly 12 frames. No source footage was included.
- Inspected the canvas overview, readable input/setup view and expanded Save Video
  preview. A synthetic portrait test clip loaded at 256 × 384 in the preview;
  the result column remained clear of the decode/assembly controls. This preview
  checks layout and playback, not generated visual quality.
- All six pinned model download URLs returned HTTP 200 to metadata-only HEAD
  requests; no model files were re-downloaded.
- Focused tests check recipe strengths, six-step sampling, source/audio routing,
  timed-guide conditioning, exported API consistency and canvas link integrity.

ComfyUI: `c844cb672186f187bf9455e2808821417a10d296`; frontend 1.59.1.
H3 Relay: `8c313cc6bd23298be3b475d95a3af4cde6e7f10b`.

The portable files intentionally contain placeholder `source.mp4`, `character.png`
and, for the guided variant, `guide.png`. ComfyUI will flag these as missing until
you upload/select your own files. They are not bundled assets.

No new GPU diffusion render was run for this canvas release. The model/sampling
stack is shared with the editor, but these checks do not establish visual quality
on arbitrary footage, memory fit at the maximum length, or long-video continuity.
These examples process a single window; they do not claim multi-window Relay or
SAM support. No active GPU service or user job was interrupted for validation.

To refresh the executable examples after an intentional recipe change:

```sh
uv run --locked python scripts/export_pika_workflows.py
```

Then import those API graphs in ComfyUI, preserve/reapply the layout and notes,
export the canvas files, and repeat the frontend reload/input comparison. The
script does not guess canvas socket IDs or dynamic widget serialization.
