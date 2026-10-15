# H3 Character Swap — Linux container qualification

## UI release — 0.1.0-alpha.3

This release updates the interface to match the four shot-workspace mockups and
renames the visible app to **H3 Character Swap**. It adds compact line icons,
fixed-width frame/time controls, clearer panel spacing, shorter helper text and
a seed-randomize shortcut using the existing autosave.

App image: `sha256:daa1bf49c032f9461d1ab6f22c43bf7bd19415dd6e90ef4a295a8bd7fe5e53bc`.
Payload revision: `565e5823f8bdd95f1a1fe36cccbe400a0eb825fcf11a5c67a0528da30aba07d5`.
The GPU worker is the exact alpha.2 image below; its generation runtime and
seven-model profile are retained. Generation evidence below belongs to alpha.2.
This UI release does not claim a new quality fix or another GPU render.

The production build and ten Node runtime tests passed. Real-browser layout
checks cover frame transitions 9/10 and 99/100 at 1500, 1280, 1000, 760 and
390 px, fixed Play/Pause geometry, comparison playback, phone controls and light
theme. Autosave tests cover navigation, retries, conflicts, target isolation,
optional anchors and the seed shortcut, with project writes intercepted.
The actual image's 9,989 payload files and desktop launch references passed
checksum verification. Updating the existing app preserved both projects,
all 14 jobs and the SHA-256 of all five render files. The GPU worker
process was retained. Screenshots and detailed checks are recorded in
`docs/ui-polish-20261015.md` and the bundle's qualification receipt.

## Generation baseline — 0.1.0-alpha.2

The operator accepted this experimental MVP on 2026-10-15 after completing a
real render and resolving one identity-drift case with a different seed.
Character consistency remains experimental; technical qualification does not
promise that every seed or clip will produce a good swap.

### Exact offline image pair

| Component | Local image ID |
| --- | --- |
| App | `sha256:2c494e2db8fa84fab1fd256974c78a28b445f83432442d7800b1797042ceab7e` |
| Worker | `sha256:1644472593b025d2fc2b387c9320bf224d7f343d2d3378be36d621a5af3522cc` |

App payload revision: `7660ade3f6087d5b280ae3d0e2d38bf215b489f9f04b6d279ca73f8468fd32e9`.
The bundle includes versioned tags and its image archive checksums. These local
image IDs are not published registry digest pins.

Test host: Linux x86-64, RTX 4090 (24 GB VRAM), 128 GB installed RAM,
NVIDIA driver 610.43.02, Docker 29.5.2, Compose 5.1.4, NVIDIA CDI.
The standard NVIDIA-runtime Compose file is syntax checked; CDI is the executed
configuration. Other GPUs, lower-RAM hosts, older drivers and WSL2 remain
unqualified. Run the included native-kernel preflight on the target host.

### Fresh setup and model reuse

- Started with fresh project, worker-state and model volumes, without a host
  source checkout, native ComfyUI environment or shared-drive mount in either
  container. Node, Python, ComfyGit, FFmpeg and the pinned GPU runtime are bundled.
- Resources opens first and reports seven required models. Install required
  acquired all seven from their pinned public sources into the empty model
  volume. All 46,327,435,639 bytes were verified against the catalog's full SHA-256
  values before indexing. LTX, DWPose, V2 and removal models are excluded.
- The first download lost its connection during Qwen. Four completed files
  remained intact. A real UI retry reused those files and completed the remaining
  downloads. An incomplete file restarts on retry; byte-range resumption is not
  implemented. The UI explains recovery and shows progress near Continue.
- Browser reload and app-only image replacement during acquisition retained the
  same accepted worker job and downloaded bytes. The final worker image scanned
  the persisted downloaded files and reported all seven models ready.
- The existing-folder Compose override was exercised against the existing
  ComfyUI model store. Scan reached seven ready models without downloading or
  changing source file identity, size or modification time. Reuse currently
  requires the catalog's expected relative paths; arbitrary renamed files are
  not automatically matched.

### Real output and persistence

- A point-selected 26-frame shot completed SAM tracking and character-swap
  generation through the browser on the matched image pair. Sampled masks at
  frames 0, 13 and 25 track the face; hair/clothing mostly remain outside the
  selection. This does not qualify full-body segmentation.
- Render output: **1152 × 864, 26 frames, 24 fps,
  with source audio**. Browser playback, full decode and downloaded-file checksum
  matched: `e83c1f63591472357823b6e528b190fd9c19e61e5178f8a4d5456f122464a08b` (703,770 bytes).
- Review confirmed the selected completed take and assembled the final clip. Result,
  fixed 16:9 comparison, fitted comparison, source pair and provenance downloads
  all passed real browser download checks. The videos decode and retain audio.
- Source/result audio was compared as 32 kHz mono PCM. Render correlation at
  zero lag was 0.99923; final assembly was
  0.99923. Best measured offsets are recorded
  in the qualification receipt; normal codec tail padding is retained.
- A completed app/worker restart preserved the installation identity, project,
  accepted job IDs and output bytes. The user's existing projects and all five
  earlier render files survived the final compiled-image upgrade unchanged.
- Actual GPU process arguments and every parent cgroup were inspected. CPU,
  RAM and swap limits were unrestricted. SAM uses high VRAM and reserve zero.
  The 1 MP render uses the existing explicit model-offloading choice: the prior
  full-residency test on this 4090 failed with only 22.5 MiB free for a 220.5 MiB
  allocation. No resolution, duration, seed or model changes were used to hide
  that capacity limit. The historical failure is retained in private evidence.
- The qualification worker released its runtime and GPU ownership before the
  existing user app was restored. No unrelated job or service was interrupted.

### Visual limitation in this qualification

The frozen seed used for this test reproduces the known identity-drift failure.
The alien appears at frame zero with a visible mask boundary, but sampled frames
13 and 25 return to the human source identity. This is retained as a regression
example, not promoted as a successful visual demo. No model or recipe quality
fix is claimed by this packaging release.

### Interface and packaging checks

Desktop and phone views verified the menu below its top-right button, Escape
handling, Resources progress/recovery and navigation to Projects, Shared Library
and Settings. Shot prompt/settings autosave and optional uploaded anchors remain
in the MVP. Foreign-origin writes were rejected. All image archive blobs and
checksums were verified, and Docker loaded the exact final pair successfully.

Python suite: 230 passed. Node runtime/profile checks: 10 passed. Docker context
checks: 8 passed. SDK resource checks: 14 passed. TypeScript/Vite builds passed.
Browser and real GPU evidence supplement these automated checks.

### Distribution scope

This is an offline friend alpha. Model weights and user footage are excluded.
The bundle contains model terms, first-party licensing, dependency notices and
corresponding source/build materials for bundled GPL FFmpeg. Model permissions
for the publisher and friend were confirmed separately. Review upstream terms
before any wider distribution.

Docker Hub publication, GitHub source publication, CI, signing, Windows and
macOS qualification remain future work. No registry pull or clean second-machine
installation is claimed. Source is committed locally; bundle metadata records
its exact checkpoint and pinned SDK package checksums.

The offline bundle's `qualification.json` contains its portable result summary.
