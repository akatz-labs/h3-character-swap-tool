# H3 Character Swap

A local video workbench for preparing performances, correcting masks and
rendering character swaps. Build and run the browser UI and GPU worker directly from source on a Linux
x86-64 NVIDIA machine. Docker images are the prebuilt alternative.

https://github.com/user-attachments/assets/e9ed875e-bfe9-42b9-8667-c895ae79f9bc

## Shots and generation segments

Each shot contains editable segments, with one diffusion pass per segment.
Choose 5-, 10- or 15-second segments, split at the playhead, move a boundary,
or merge adjacent segments. Preview a new layout before applying it; previous
layouts retain their directions and takes.

Select a segment in Render to edit its dialogue, speaker, performance prompt,
seed and scene reference. Dialogue starts from the transcript for that segment's
source range. Update prompt rebuilds the performance prompt from the dialogue,
speaker and references; direct edits are sent to H3 exactly as shown. Each
segment saves its own text. The dialogue and prompt editors sit below the player;
generation settings stay in the right column. Anchors contains the character and
scene images.
Drag the Resize video bar beneath the canvas to give playback more room. Its
height is remembered in this browser; arrow keys also resize it, and double-click
resets it. Playback and mask controls stay grouped on wide screens, with stable
space for expansion save messages.
Continue a previous segment with a chosen amount of generated history, or start
fresh at a cut. A continuation can also use an additional reference image.

Generate one segment, or queue it while its predecessor is still rendering.
Queueing freezes that segment’s saved prompt and settings; it automatically
starts with the exact preceding take when ready, even after navigating away.
The popover below Generate offers Queue all segments and Queue from here.
While a render is active, these add the remaining segments without duplicating
active work or current takes. A failed or cancelled predecessor stops dependent
jobs with an explanation. Progress cards appear only for unfinished segments
covered by that job, with future segments shown as queued.
Selecting a different preceding take marks dependent
continuations as needing refresh. Once every selected take is current, the app
assembles the shot with the original source audio. Lossless continuation masters
preserve history across application and worker restarts. Existing whole-shot
takes remain available.

New shots default to Character Swap LoRA V1.5 on MiniMax H3. V1 and V1.5 are
LoRA adapters, not separate base models. Setup offers both as required downloads
from [Hugging Face](https://huggingface.co/akatz-ai/MiniMax-H3-Character-Swap-LoRA),
with pinned revisions, sizes and SHA-256 hashes in the
[resource catalog](docker/resource-catalog.json). Existing verified files are
reused, and existing shots keep their saved LoRA selection. V1 remains selectable
in Advanced settings. Internal checkpoint IDs and paths remain compatible with
previous installations.

## ComfyUI workflow

The [standalone workflow and API guide](workflows/README.md) covers source video, character reference, optional anchor, audio guidance, SAM masking and the V1 character LoRA. It uses native ComfyUI nodes and the same pinned H3 Relay toolkit as the app.

## Run from source

Start with [SOURCE_INSTALL.md](SOURCE_INSTALL.md). The checkout includes pinned
shared-package archives and their rebuildable source; no sibling repositories
are needed. Setup chooses shared model, cache, media and tooling locations
automatically and keeps H3 project data separate. Whisper uses the shared model
cache so other SDK apps can reuse it. Models and public third-party dependencies
download separately.

## Docker alpha

The app image contains the UI, application server, project storage and CPU media
tools. The worker image contains the frozen ComfyGit/ComfyUI runtime and GPU
dependencies. Model weights and user media stay in persistent volumes.

The alpha image release is being prepared for the `akatzai` Docker Hub namespace.
No published image digest is supplied yet. Use only a matched, reviewed release
bundle; local development tags are not a public installation target.

- [Installation and recovery](docker/AGENT_INSTALL.md)
- [Docker architecture](docker/README.md)
- [Validation and known limitations](docker/VALIDATION.md)
- [Release plan](docker/ROLLOUT.md)
- [Required model sources and checksums](docker/resource-catalog.json)

The current resource catalog includes SAM masking, MiniMax H3 with FL2V Turbo8,
and both character-swap LoRAs: eight required model files. See the pinned catalog
for exact sizes. The original V1-only profile remains in `docker/models-v1.json`.
The tested host is an RTX 4090 with driver 610.43.02 and 128 GB RAM. WSL2 is a
separate qualification target. A CUDA tensor probe alone does not establish
that the frozen native kernels work with another driver.

Current alpha limits include identity drift on the test clip and an explicit
offload retry needed after a measured full-residency OOM at 1 MP. See the
validation record before treating the example as a dependable quality demo.

## License

The app and Akatz App Kit use the [MIT license](LICENSE). The pinned H3 Relay
source archive in `vendor/` uses GPL-3.0;
its NVIDIA kernel derivative retains Apache-2.0 notices.
Third-party components and models retain their own terms. The release bundle
must include its exact notices and corresponding source/build information.

## Development

Keep project data and model stores outside source control. The Docker build
uses a verified payload manifest and a frozen worker recipe; ordinary container
startup does not install or resolve application dependencies. GPU admission and
accepted-attempt recovery remain owned by the existing shared executor.

See [CONTRIBUTING.md](CONTRIBUTING.md) for local checks and development setup.
The app is intended for a trusted local machine or private network; it does not
provide a public multi-user authentication boundary.
