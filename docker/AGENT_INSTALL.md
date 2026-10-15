# Same-machine Linux NVIDIA installation

Use the versioned installation bundle distributed with the image pair. It
contains the Compose files, exact image pins, model manifest, notices and
this guide. Do not substitute `latest` or mix app and worker releases.

## Before starting

Use Linux x86-64 with a supported NVIDIA driver, Docker Engine, Docker Compose
and working NVIDIA Container Toolkit GPU access. WSL2 is a separate, unqualified
target because its active GPU-process observations differ from Linux. A CUDA
probe alone does not qualify the application there.

Check available disk space for both images, 43.2 GiB of initial models, projects,
outputs and working space. The initial profile is SAM masking plus character
swap V1 with the exact FL2V Turbo8 adapter in `models-v1.json`. Other operation
models are excluded from this MVP. No weights or example footage are inside the images.

Check for existing GPU work and native executors before starting. The worker
must be the sole dispatcher for the physical GPU. Preserve running work; on a
managed machine, use a verified idle handoff and retain the restoration receipt.
Do not change the Docker daemon, stop unrelated containers, or prune storage.

## Start the matched release

The release version is `0.1.0-alpha.3`, targeting the `akatzai` Docker Hub
namespace for future publication. The current offline bundle contains
versioned tags in `images.env`; load its checksum-verified image archive before starting. A later published
release will contain registry manifest digests and support Compose pull.

Place `compose.yaml`, `compose.cdi.yaml` and `images.env` in one directory.
Run from that directory, after verifying the bundle's checksum:

```sh
docker compose --env-file images.env config --quiet
docker load --input h3-workbench-images.tar
# For a published registry bundle, use Compose pull instead of docker load.
docker compose --env-file images.env run --rm --no-deps \
  --entrypoint /opt/h3-worker/workspace/environments/h3-docker/.venv/bin/python \
  worker /opt/h3-worker/scripts/native_preflight.py
docker compose --env-file images.env up -d --no-build
docker compose --env-file images.env ps
```

The preflight executes the actual CUDA FP8 and INT8 attention kernels before
starting an executor. It must pass; preserve the returned JSON with any failure.
The qualified Linux host used NVIDIA driver `610.43.02`. Torch's `cu128` label
alone does not establish compatibility with the bundled native CUDA extensions.
Do not fix a failed preflight by upgrading packages inside the image.

On a host already configured with NVIDIA CDI instead of the NVIDIA runtime,
add `-f compose.yaml -f compose.cdi.yaml` to each Compose command. Use one GPU
mechanism. Compose must support the `!reset` override tag for this option.

The app is served on host loopback port 8898 by default. Open it in a browser on
that same machine. A remote preview needs a separately verified private route.
Executor and ComfyUI ports remain private to the paired containers.

## Acquire models and make the first result

1. Open Resources and select GPU worker. With an empty model volume it must
   show the installed runtime and missing models, rather than an installation
   failure. Review each upstream model's terms before downloading.
2. If reusing a ComfyUI model folder, set `H3_MODELS_DIR` and include
   `compose.models.yaml` as described in `README.md`, then choose Scan. Choose
   Install required to download the missing models in `models-v1.json`. The
   worker verifies SHA-256 and size, then indexes the files. Keep the model
   volume for updates and keep its container path at `/models`.
3. Create a project, import a short clip with audio, approve its shot boundaries,
   and upload a character reference. A first-frame anchor is optional.
4. Add a person selection and track the shot. Scrub through every frame. Correct
   empty or incorrect masks with include/exclude points and Update frame before
   rendering. Tracking quality on arbitrary footage is not guaranteed.
5. Use the local GPU, edit the prompt/seed/size (saved automatically), then generate once.
   Retain its accepted job ID. If the browser or app restarts, collect that
   attempt; do not click Generate again to recover an uncertain submission.
6. Play and download the completed MP4. Verify dimensions, frame count, audio,
   and that the saved prompt, seed, reference, anchor and mask match the job.

Model offloading is enabled by default for new renders to reduce GPU memory use.
The Render panel can disable it for the next render. This execution choice is
recorded with the accepted job; prompts, seeds and generation inputs are unchanged.
CPU and RAM are unrestricted. Report any remaining capacity failure without
silently reducing size or duration.

Transcription runs on the app container's CPU. Its pinned medium.en model is
installed from Resources (about 1.5 GB), then reused from the shared models
volume, including offline. No SSH worker or runtime package installation is needed.
Download/transcription progress and failures appear beside Transcribe clip.

## Restart and update

An app-only restart must retain projects and collect the same worker job:

```sh
docker compose --env-file images.env restart app
```

Wait for accepted work to become terminal and release its runtime before
replacing the worker image. Keep app-data, worker-state and models volumes.
Never use `down -v` for an update. A forced worker interruption may require
reconciliation; it must not cause an automatic new render.

Record the image IDs/digests, driver/GPU, available RAM/VRAM, effective process
limits, accepted IDs, checksums, download and restart results. Report precisely
which host configuration and operation passed. The publisher and friend have confirmed their required model authorization.
A Linux test does not establish WSL2 qualification.
