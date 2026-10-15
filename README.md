# H3 Character Swap

A local video workbench for preparing performances, correcting masks and
rendering character swaps. Build and run the browser UI and GPU worker directly from source on a Linux
x86-64 NVIDIA machine. Docker images are the prebuilt alternative.

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
