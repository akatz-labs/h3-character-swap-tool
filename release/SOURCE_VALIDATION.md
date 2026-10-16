# Source release qualification — 2026-10-15

## LoRA naming and acquisition — 2026-10-16

The interface identifies the adapters as Character Swap LoRA V1 and V1.5 on
MiniMax H3. Both are required downloadable resources; V1.5 now points to the
published Hugging Face checkpoint at a pinned revision with size and SHA-256
verification. V1.5 remains the default for new shots. Existing model IDs, paths
and saved selections are retained. The manual-install limitation recorded in
the earlier qualification below is superseded by this catalog update.

## Segments and Character Swap LoRA V1.5 — 2026-10-15

Shots now contain editable segments, each corresponding to one diffusion pass.
Each segment saves its directions, timestamp-derived dialogue, seed, connection,
optional reference and takes. Continuations use selected predecessor masters;
fresh starts end the dependency chain. Layout changes retain previous layouts
and their takes. Character Swap LoRA V1.5 names the existing V2-1000 checkpoint and
is the default for new shots; existing shot model selections are preserved.

Validation for this update includes:

- 295 Python tests, 11 Node runtime tests, frontend and runtime builds, and the
  201-file source allowlist check. Tests cover revision conflicts, partitioning,
  split/merge/boundary edits, frozen plans, dependency invalidation, source/mask
  crop alignment, continuation inputs and offload retry preservation.
- Browser checks cover 5/10/15-second layout controls, independent segment
  editing, autosave/reload, reference import, take playback and downloads,
  desktop and 390 px layouts. Dismissing the running-job stop confirmation
  sends no cancellation request. Switching old/new takes restores their saved
  settings. Editing the first segment invalidates its continuation but leaves
  a later fresh start current; restoring the saved take recovers the chain.
  Immediate take changes and edits after assembly refresh the clip revision
  while preserving plan and field conflict checks.
- A real V1.5 render produced three 40-frame segments at 1344 × 736, 24 fps.
  Every segment used one diffusion pass. Their history lengths were 0, 18 and
  0 frames; the middle segment also used an additional reference image.
  Its 18 history frames exactly matched the selected predecessor's final
  decoded RGB48 frames. The assembled 120-frame master exactly matched the
  concatenated selected masters, and source-audio correlation was 0.999626.
- Restarting the shared executor preserved its identity and ledger. A new
  middle-segment take completed after restart using byte-identical persisted
  history from the original predecessor, and its new assembly again matched
  all 120 selected master frames exactly.
- Full residency on the 24 GB RTX 4090 exhausted physical VRAM: 22.03 GiB was
  allocated, 22.50 MiB was free, and the next allocation requested 220.50 MiB.
  CPU, RAM and swap were unrestricted, with zero VRAM reservation. The failure
  was reported before retrying with explicit model offloading; resolution,
  duration, model, prompt and seed were retained.

This is short functional qualification. Full-length 10/15-second diffusion
passes and broader character-consistency comparisons remain unqualified. V1.5
weights require the exact manually supplied checkpoint; this update does not
publish model weights or container images. A matched container rebuild remains
separate.

## Automatic ecosystem integration — 2026-10-15

The source installer now defaults to the shared Akatz namespace and saves the
completed worker selection. Worker revisions and pinned tooling live outside the
checkout; model files, package caches and the media library are shared while app
databases remain separate. App Config's source.3 snapshot supplies the reusable
resolver, shared-service startup and Hugging Face cache leases.

Validation for this update includes:

- 251 Python tests, 10 Node runtime tests, and seven SDK ecosystem tests. Both
  library archives rebuild byte for byte from their bundled source. SDK tests
  exercise real concurrent media/executor reuse, restart identity, isolated app
  databases, incompatible endpoint rejection and installation-pointer conflicts.
- A fresh Linux NVIDIA worker installed from the exported source using Node
  24.12 and the shared defaults in an isolated XDG namespace. It reused all seven
  verified GPU model files and an existing uv cache. A second setup reused the
  completed runtime; app and worker start discovered their saved paths.
- A real SAM frame correction completed through the new shared executor. Its
  ComfyUI process used `--highvram --reserve-vram 0`, without
  `--disable-smart-memory`; CPU, RAM and swap limits were unrestricted. The owned
  runtime was released afterward and the GPU compute list was empty.
- H3 and a second consumer each transcribed the same five-second audio offline
  from the shared default Hugging Face cache. Both resolved the same model inode
  and produced the same text. This used a disposable hard-linked cache of the
  existing weights; originals were preserved and no download was needed.
  Separate-process tests verify that active use blocks reviewed removal.
- Restarting the app preserved its identity and projects. Stopping its systemd
  unit left the shared services running in their own independent user units.
- A second SDK consumer reused the running executor and media library, registered
  its own app identity, and resolved the same model store with separate app data.
- Browser checks cover the default project location, the seven installed GPU
  resources, the shared Whisper location, desktop and 390 px layouts, and no
  JavaScript errors.

Existing app-local media catalogs and transcription caches remain usable without
being moved. This update does not repeat the H3 generation-quality comparison
below, qualify other operating systems, or rebuild/publish Docker images. The
Compose source now mounts the shared models volume in the CPU app too; a matched
container rebuild remains a separate qualification step. Future apps must use the
shared SDK contract to participate in discovery and cache-use coordination.

## Public main preparation — 2026-10-15

The public tree is prepared for a new repository with one initial commit on
`main`. Development history and the independent Cloud branch remain separate.
Nothing in this preparation publishes source, packages or container images.

The exported tree passes the Node 24.12 source build, 245 Python tests and 10
Node runtime tests. Both bundled libraries rebuild byte for byte; npm dependency
installation reports zero known vulnerabilities. The bounded source check reviews
the explicit allowlist, local documentation links, archive contents and obvious
credential or private-path patterns; it is not a comprehensive security audit.

The source defaults no longer depend on lab workers or storage paths. A configured
media mount is checked before directory creation and imports. Tests cover missing
mount refusal, local defaults and shared-root path boundaries.

Browser checks on the exported tree verify development-server static assets,
project creation, desktop and 390 px layouts, keyboard focus, and no JavaScript
errors. One legacy source edit produces one reload and then settles; a no-op
bridge build preserves generated-file timestamps. Generated inputs are ignored
by Git and the development watcher. No GPU render was repeated for this pass;
the earlier functional render and quality limitations below still apply.

A CPU-only GitHub Actions workflow is included with pinned action revisions,
read-only repository permissions and no publishing steps. It has not run on
GitHub. Clean-checkout validation is local.


## Lucide refresh update — 2026-10-15

App Kit now pins Lucide React 1.57.0 and maps its existing `refresh` name to
RefreshCw. H3 generates the legacy editor's SVG from the shared component at
build time, including the upstream license notices. All current refresh consumers
(including Update frame and seed refresh) share that icon. No CDN is needed.
Other icons are unchanged; broader adoption is incremental.

The App Kit check suite, H3 frontend/runtime builds and 10 runtime tests pass;
the bundled libraries rebuild byte for byte. Browser checks verify the rendered
refresh controls, accessible button names and mobile layout. The earlier 242-test
Python result below remains applicable; Python code did not change in this update.

## Resources and interaction update — 2026-10-15

The updated clean export passes 242 Python tests, 10 Node runtime tests, and the
frontend/runtime builds. Both bundled shared libraries rebuild byte for byte.
App Kit's checks pass 93 Node tests; App Config's model-resource checks pass 11.

Whisper Medium.en is an optional Resources entry with explicit install and
reviewed removal. Existing transcription caches are recognized. Required GPU
readiness still counts the seven GPU models separately. Removal checks active
work and validates the reviewed file identities before deleting anything.

Browser checks exercised cancel and confirmed removal against a disposable
Whisper cache, preserving an unrelated file. Additional tests cover shared Hugging Face blobs,
preservation of blobs still used by another model, and rejection of external links.
Real GPU models were reviewed and
cancelled only. The resolution dropdown saves numeric values from 0.5 through
1.0; pointer clicks leave the mask canvas without an outline, while keyboard
focus remains visible. Gear and recycling icons, desktop and 390 px layouts
passed with no JavaScript errors. Existing user projects were unchanged.

This update was also installed into the private source-test app. Earlier GPU
render qualification below describes the initial source export; it was not
repeated for these resource-management and interface changes. Public registries
and the separate Docker application remain unchanged.

## Initial clean source checks

The reviewed export was built without sibling Akatz checkouts, private package
registries, Docker images, model weights or a pre-existing application environment.
Public npm/Python dependencies were installed from the included lockfiles.

- `npm ci`, `npm run source:build`, locked CPU Python installation and locked
  ComfyGit tooling installation passed.
- 233 Python tests and 10 Node runtime tests passed. The source-launcher test
  starts the real UI/API processes, rejects a second owner of the same data,
  rejects a foreign-origin mutation, restarts, and verifies project persistence.
- Both shared libraries rebuild from their included source. Rebuilt `dist/` and
  distributed `src/`/`desktop/` files match the shipped npm archives byte for byte.
  App Kit's source.1 packaging revision removes one stray Python bytecode cache;
  executable source and compiled JavaScript are unchanged from alpha.9.
- A clean path containing spaces exposed and fixed a frontend-build URL decoding
  bug. Test fixtures now use isolated data and synthetic workers, so they do not
  depend on the development machine's databases, worker names or retired workflows.
- Actual browser checks covered project creation, library/resources navigation,
  desktop and 390 px layouts, completed-render preview and a download whose SHA-256
  matches the API-delivered file. No JavaScript errors were observed.

## Fresh GPU worker

The exported source installed a fresh ComfyGit/ComfyUI environment on Linux x86-64,
using Node 24.12, ComfyGit 0.7.1, Python 3.12 and Torch 2.11.0+cu128. The machine had
an RTX 4090, 24 GB VRAM, 128 GB RAM and driver 610.43.02. The seven existing model
files were hash-verified and reused; no private node package or old worker
installation was required.

A second fresh installation used an empty model folder and the first installation's
uv package cache. It installed successfully with zero models. Its browser Resources
page showed runtime ready (3/3 components), models missing (0/7), and install controls.
Generation remained unavailable until required models were present. This check did
not redownload the model weights.

The app's ordinary source UI/API and shared executor completed a SAM tracking job
with coverage on all 26 frames, then a V1 character-swap render with source video,
reference, anchor, audio guidance, expanded mask, seed 194731 and the detailed
character prompt. The output was **1184 × 832, 26 frames, 24 fps**, with audio.
FFmpeg decoded the complete file; source-audio correlation was **0.999573** over
51,999 mono samples at 48 kHz. The downloaded original and the browser comparison
proxy have different display dimensions; original download checks used the full
1184 × 832 file.

## Memory and limits

Full-residency generation failed on the 24 GB GPU: 22.27 GiB was allocated,
18.5 MiB was free, and the next allocation requested 110.25 MiB. The process and
all parent cgroups had unrestricted CPU, RAM and swap; VRAM reserve was zero.
The app recorded this capacity failure before its explicit offload retry. That
retry completed with the same inputs, prompt, seed, model and generation settings.
The source worker launches with full resource access; per-job model offloading is
the existing app setting, enabled by default in the UI. The full-residency test
explicitly disabled it.

This is one short functional render, not a general character-consistency or GPU
compatibility claim. Windows/WSL2, macOS, other GPUs and longer videos remain
unqualified. Use local storage for SQLite state; an NFS state-folder trial caused
health timeouts and is not a supported configuration.

## Distribution boundary

The source tree includes app code, the standalone workflows and focused custom
nodes, exact shared-package archives, corresponding source, licenses, model catalogs,
lockfiles and setup/start/export scripts. Public third-party dependencies and model
weights download separately. It is not an offline package mirror.

No GitHub repository, npm package, Docker image or other public registry was
published by this source preparation. The existing running app and Docker images
were not upgraded. A matched Docker rebuild remains a separate release task.
