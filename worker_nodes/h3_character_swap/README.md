# H3 Character Swap nodes

Focused ComfyUI nodes for the main H3 character-swap flow. Copy this complete folder into `ComfyUI/custom_nodes`, or link it with ComfyGit. The companion `workflows/README.md` in the source repository documents the pinned runtime, seven models, canvas workflow and API inputs.

Registered node identifiers use `H3CharacterSwap` to coexist with H3 Relay and H3 Performance. This package does not require those older packages or ComfyUI-LTXVideo. `LTXVSeparateAVLatent` is a native ComfyUI utility used to separate H3's audio/video latent; it does not load an LTX model.

Only the required preparation, SAM selection/recovery/expansion, reference conditioning, masked diffusion, continuation, cache and MP4 export code is included. App session hooks are inactive unless the app explicitly owns the runtime. Output uses raw generated frames and the source soundtrack.

## Derivative source and licenses

This is a modified GPL-3.0 derivative of H3 Relay and H3 Performance. Changes remove unused face/pose/backplate passes and dependencies, add standalone optional-anchor and SAM recovery controls, and provide a single source-audio output. `SOURCE_PROVENANCE.json` identifies the original source files and hashes.

The vendored context-loop helper retains its GPL-3.0 license and NikoDemon80 attribution. Its mask compatibility helper credits seitanism/ComfyUI-H3-Motion-Context-MultiRef. The small INT8 Triton kernel derivative retains NVIDIA copyright and Apache-2.0 terms in `LICENSE-Apache-2.0`; it is modified to use 64-bit matrix pointer offsets. No model weights are bundled.
