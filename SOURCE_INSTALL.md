# Build and run from source

This is the primary installation path. The repository includes the shared App Kit
and App Config package archives, their corresponding source, and locked npm,
Python, ComfyGit and ComfyUI dependencies. No sibling Akatz repositories or private
package registries are needed. Internet is required for public dependencies,
ComfyUI source and missing model weights; this is not an offline dependency mirror.
Docker is the [prebuilt alternative](docker/README.md).

## Supported host and prerequisites

Use Linux x86-64 with an NVIDIA GPU and a compatible NVIDIA driver. The validated
GPU is an RTX 4090 with 24 GB VRAM; the worker uses Python 3.12 and Torch cu128.
Windows/WSL2 and macOS are not qualified by this release. GPU support depends on
the pinned native kernels as well as the driver; see [workflow validation](workflows/VALIDATION.md).

Install Git, Node.js 24 LTS with npm, uv, FFmpeg/ffprobe (with libx264), and the
normal Linux compiler toolchain. Python 3.12 can be supplied by uv. On Debian or
Ubuntu, the system packages include `git ffmpeg build-essential libgl1 libglib2.0-0
libgomp1`. Node/npm and uv are separate prerequisites. The browser UI needs no
Rust or desktop WebKit development packages.

Allow about 43.2 GiB for the seven models, plus ComfyUI, Python/CUDA dependencies,
package caches and media. Existing matching model files can be reused directly.
Use a drive with adequate free space; setup does not download models automatically.

## Install and build

Run these commands from the repository root:

```sh
uv run --no-project --python 3.12 python scripts/verify_vendor.py
npm ci
uv sync --locked --python 3.12
npm run source:build
```

The checkout Python environment contains the CPU application. Worker setup installs
pinned ComfyGit 0.7.1 tooling at a permanent shared location and gives ComfyUI its
own isolated environment. Your global `cg` installation is untouched.

## Install a worker

No folder selection is needed for a fresh installation:

```sh
npm run source:setup
npm run source:worker
npm run source:start
```

Setup installs the pinned ComfyUI commit and focused character-swap nodes. It
verifies existing required models without downloading missing weights. The worker
command registers the completed installation with the shared local executor and
returns; the app command stays in the foreground. Repeating setup reuses the same
completed revision. It rejects changed or incomplete installations rather than
overwriting them, and publishes the saved selection only after setup succeeds.

On Linux the default shared root is `~/.local/share/akatz` (or the XDG data home):

| Content | Location below the shared root |
| --- | --- |
| Model files | `models/` |
| Whisper / Hugging Face cache and index | `models/huggingface/hub/` |
| H3 projects and app database | `apps/h3-workbench/` |
| Shared media library | `media/` |
| Shared executor state | `apps/executor/` |
| ComfyGit tooling and shared service code | `tools/` |
| H3 worker revisions | `runtimes/h3-workbench/<revision>/` |
| Shared package cache | `cache/uv/` |

Inspect the resolved locations and saved worker with `npm run source:paths`.
Shared SDK configuration (`storage.json`, `models.json`, `media.json` under the
Akatz config directory) and `AKATZ_DATA_ROOT` / `AKATZ_MODELS_ROOT` are honored.
Choose another drive through that configuration before setup if needed. Other
apps using this SDK discover the same stores while retaining separate databases
and runtime environments. Hugging Face owns its cache index; ComfyGit owns GPU
model discovery and dependency state.

Use a local filesystem for databases and worker state. Model files and exports
may live on network storage; SQLite state on NFS is not qualified. Explicit
`--root`, `--models`, and `--cache` options on `source:setup` remain available.
The selected worker and model paths are saved, so subsequent starts need no paths.
`source:start -- --data /absolute/existing-projects` keeps an existing project
folder. Existing app-local media catalogs and Whisper caches remain in place.

Worker bindings reference permanent tooling and copied adapter/controller code,
not the source checkout. A new release creates a new runtime revision while
reusing the model store, cache, media library and app data. The CPU application
still runs from its source checkout; rebuild and restart it after source updates.
Do not edit a bound worker revision or remove a runtime with accepted jobs.

A failed setup preserves its partial directory for diagnosis. After resolving the
failure, use a new `--root` for a clean retry; no partial installation is selected.

## Start

For subsequent use, run `npm run source:worker` and `npm run source:start` from
the built checkout. The worker selection is discovered automatically.

Open the local address printed by the launcher on that computer (port 8898 by
default). The services bind to loopback. A phone or another computer needs an
authenticated private reverse proxy and its exact origin added through
`H3_WORKBENCH_TRUSTED_ORIGINS`; do not expose the executor or ComfyUI directly.

The app and shared services have separate lifetimes. Closing the app preserves
accepted jobs and the shared executor/media services. Other apps may be using
them. On Linux with a systemd user manager, shared services run in independent
user units. Otherwise they detach from the foreground app; a supervisor that
stops an entire process group needs to manage shared services independently.
Startup reuses compatible services and refuses occupied or incompatible
endpoints without stopping them. Service receipts and logs are under `services/`
in the shared root. Review their ownership and queues before stopping a service.

For an alternate executor port, pass `--executor-port` during setup. Use
`--comfy-port` during setup and `--port` on the app launcher for the other ports.
An existing project folder remembers its worker port and refuses an accidental
change; `--worker-port` selects the matching worker when opening such a folder.

In **Resources**, scan existing models or install missing required models.
Whisper Medium.en is an optional CPU transcription model (about 1.5 GB). Install
it from Resources before transcribing. New downloads use the shared Hugging Face
cache at an exact model revision, so another SDK app can reuse the same files.
Existing app-local downloads are preserved. Reviewed removal reports shared use;
active SDK consumers and downloads block removal across processes. Apps accessing
the cache outside this SDK must coordinate their own use.

Then create a project, import a video and character reference, optionally import
an anchor, prepare the SAM mask, and render. Model source revisions, checksums and
terms are listed in [the model catalog](docker/models-v1.json). No model weights,
user media, hosted inference account or API key is bundled or required by this flow.

The default worker uses full GPU residency (`--highvram --reserve-vram 0`). The
previous 24 GB test ran out of VRAM with full residency. Report an actual memory
failure before changing the memory policy or generation settings. The app’s
explicit model-offloading setting and capacity-failure retry use the existing
shared runtime policy; they do not require another environment installation.
They preserve the model, resolution, frame count and sampling settings. The
earlier offload validation is not a promise that every input fits.

## Develop and verify

Rebuild with `npm run source:build`, then restart the app process after source
changes. Do not rebuild worker code while it has accepted work. For frontend
hot reload, keep the app running and use:

```sh
H3_DEV_API_ORIGIN=http://127.0.0.1:8898 npm run dev:web
```

The browser frontend proxies API calls to the source app. Add the Vite browser
origin to `H3_WORKBENCH_TRUSTED_ORIGINS` when starting the source app.

```sh
uv run --no-project --python 3.12 python scripts/verify_vendor.py --rebuild
npm run check:runtime
uv sync --locked --extra test --python 3.12
uv run --locked --extra test pytest
```

Tests create isolated temporary data and synthetic worker fixtures. They never
use the development machine’s project database or contact its GPU workers.
These checks do not substitute for an actual GPU render. See
[source-release validation](release/SOURCE_VALIDATION.md) for what was run.

## Shared-library source and public export

See [vendor/README.md](vendor/README.md) for archive provenance and independent
rebuild commands. Shared changes belong in the libraries, followed by a reviewed
version update in H3. The executor currently ships within App Config.

`uv run --no-project --python 3.12 python scripts/export_source.py /new/output/path` creates the reviewed source
tree described by `release/source-files.json`, with a checksum manifest. It omits
lab scripts, old workflow experiments, local history, caches, credentials, models
and user media. This tree can become the first public repository; exporting it
does not publish it. The native desktop wrapper is a separate packaging target;
the supported source launch above uses the browser UI directly.
