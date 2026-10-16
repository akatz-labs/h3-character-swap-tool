# H3 Character Swap

A local video workbench for preparing performances, correcting masks and
rendering character swaps. Build and run the browser UI and GPU worker directly from source on a Linux
x86-64 NVIDIA machine. Docker images are the prebuilt alternative.

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

Generate one segment, regenerate from the selected segment to the end, or
generate the whole shot. Selecting a different preceding take marks dependent
continuations as needing refresh. Once every selected take is current, the app
assembles the shot with the original source audio. Lossless continuation masters
preserve history across application and worker restarts. Existing whole-shot
takes remain available.

New shots default to CharacterSwap Pro V1.5, the checkpoint previously labeled
V2-1000. Existing shots keep their saved model. V1.5's exact identity is in the
[resource catalog](docker/resource-catalog.json); its weights currently require
manual installation. This source change does not publish new model weights or
Docker images. V1 remains selectable in Advanced settings.

## ComfyUI workflow

The [standalone workflow and API guide](workflows/README.md) covers source video, character reference, optional anchor, audio guidance, SAM masking and the V1 character LoRA. It uses one focused node package and the app’s generation core.

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
- [Required model sources and checksums](docker/models-v1.json)

The initial model profile is SAM masking plus V1 character swap with FL2V
Turbo8: seven required model files. See the pinned catalog for exact sizes.
The tested host is an RTX 4090 with driver 610.43.02 and 128 GB RAM. WSL2 is a
separate qualification target. A CUDA tensor probe alone does not establish
that the frozen native kernels work with another driver.

Current alpha limits include identity drift on the test clip and an explicit
offload retry needed after a measured full-residency OOM at 1 MP. See the
validation record before treating the example as a dependable quality demo.

## License

The app and Akatz App Kit use the [MIT license](LICENSE). The focused
ComfyUI node package under `worker_nodes/h3_character_swap` uses GPL-3.0;
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
