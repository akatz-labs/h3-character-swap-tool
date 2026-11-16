# H3 Character Swap

A local video workbench for preparing performances, correcting masks and
rendering character swaps. Build and run the browser UI and GPU worker directly from source on a Linux
x86-64 NVIDIA machine. Source installation is the supported path; Docker packaging is still experimental.

https://github.com/user-attachments/assets/e9ed875e-bfe9-42b9-8667-c895ae79f9bc

## Shared editor and local controls

The public repository includes the shared editor, its domain rules and the local
backend. The hosted application imports that same editor package through a
backend adapter. See [shared ownership](docs/shared-core.md).

The local header has GitHub, Support and **Settings**. Settings contains execution
preferences, model downloads and worker setup. The hosted header contains account
and credit controls. Everything below the header uses the same editor components:
project picker, Shots/Assets, Edit/Outputs, timeline, guides and segment settings.

Create or switch projects with the sidebar dropdown. In **Assets → Characters**,
upload a reference image or character sheet and describe its appearance. Add
characters separately to each segment. Each character can optionally select a segment mask. Selected masks are combined
into one edit region; they do not target individual character identities.

## Shots, segments and guides

Use the timeline to select segments, scrub, split, merge or adjust boundaries.
Each segment retains its characters, masks, prompt, dialogue and seed. Manual
prompt edits pause automatic prompt updates. Existing takes retain their frozen
generation parameters when you change the editor's settings.

Add a guide at the playhead to capture a source frame. Upload an edited image or
use your OpenAI API key to generate an edit. Completed generations are selected
automatically; previous versions remain available. Timeline guides
are timed AddGuide conditioning, not additional picture references in the prompt.
Character images supply picture references; the original video supplies motion.
Select masks above the viewer, add include/exclude points, update a frame and
track the segment. Select a mask on a character card when you want to constrain
the edit region. Masks and character references are optional; timed guides or
continuation history can also guide a swap.

## Supported generation workflow

The segment **Model** menu defaults to **Viggle Remake Swap · MiniMax H3**.
**Pika + Character Swap V1.5** is available after installing its two optional LoRAs.
Both recipes use six steps and the same H3 FL2VA base, encoder and VAEs.
Each segment retains its selected recipe. Local GPU memory limits the resolutions
and durations that fit. **Settings → Execution → Model offloading** defaults on
for local use; hosted execution is server-managed. Continuation uses the previous
segment's generated history and trims that overlap from the resulting take.

Opening an older shot applies the current recipe through a revision-guarded edit
and retains its previous layout in history. Manual prompts, seeds, masks, guides,
characters and media are preserved. Existing frozen job records remain readable.
The active [model catalog](docker/resource-catalog.json) has pinned revisions,
sizes and checksums. Previously downloaded files are reused, not deleted.

Want to use ComfyUI directly? Download the **[Pika + Character Swap V1.5
workflows](workflows/pika/README.md)**, with character-reference and timed-guide
variants. They run without the editor. The [workflow inventory](workflows/README.md)
also retains the older Ref2VA/Turbo Relay examples.

## Run from source

**Requirements:** Linux x86-64, an NVIDIA GPU and compatible driver, Git,
Node.js 24 LTS/npm, [uv](https://docs.astral.sh/uv/getting-started/installation/),
FFmpeg/ffprobe with libx264, and a compiler toolchain. On Ubuntu/Debian the system
packages are `git ffmpeg build-essential libgl1 libglib2.0-0 libgomp1`.
Install Node/npm and uv separately. Python 3.12 is provisioned by uv.

The validated GPU is an RTX 4090 with 24 GB VRAM, with model offloading enabled.
Allow **56.9 GiB for required model weights**, plus runtime dependencies, caches
and your media. Windows, macOS and other GPU configurations are not yet qualified.

```sh
git clone https://github.com/akatz-labs/h3-character-swap-tool.git
cd h3-character-swap-tool
uv run --no-project --python 3.12 python scripts/verify_vendor.py
npm ci
uv sync --locked --python 3.12
npm run source:build
npm run source:setup
npm run source:worker
npm run source:start
```

Open **http://localhost:8898** on that computer, then:

1. Open **Settings → Models**, scan an existing model folder or install the
   required models. Setup does not automatically download model weights.
2. Create a project and import a video into **Shots**.
3. Optionally add character references, timeline guides and masks; set the
   segment prompt, then choose **Generate segment**. Later segments continue
   from the preceding generated segment.
4. Review takes in **Outputs**, then use **Create final** to join segments.

Guides can use your own OpenAI API key, saved under **Settings → OpenAI**, or
run locally with **H3 Image Edit**. In **Settings → Models**, install the optional
H3 Image Edit model, its image VAE, and Character Swap V1.5. The existing H3 text
encoder is reused. Once the worker reports those files ready, **H3 Image Edit**
appears in the guide modal's **Edit model** menu. No OpenAI key is needed for it.

H3 Image Edit uses the INT8 ConvRot checkpoint, six sampling steps, CFG 1, and
Character Swap V1.5 at strength 1.0. Quality selects the approximate output area:
**Low: 1 MP, Medium: 2 MP, High: 4 MP**, preserving source aspect ratio. The model
is tuned for larger images; Low and Medium trade detail for lower resource use.
Model offloading is enabled by default in local Execution settings. Actual memory
and speed depend on your GPU and reference images. Source and reference ordering,
edit instructions, quality, and model choice are retained for each guide.

The two image-specific files add **20.63 GiB**; V1.5 adds about 148 MiB if absent.
These downloads are optional and do not increase the minimum video setup.
Review the [H3 Image model license](https://huggingface.co/Comfy-Org/MiniMax-H3-Image)
before use; it is separate from this application's MIT license.
Uploading your own guides and rendering swaps need no API key.
**Settings → Transcription → Auto** prefers installed local Whisper, then a saved
OpenAI key. Whisper and the Pika recipe's two LoRAs are optional model downloads.
API requests are billed to your provider account; local mode has no hosted credits.

On subsequent starts, run `npm run source:worker` and `npm run source:start`.
Projects and models live outside the checkout under `~/.local/share/akatz` by
default; your XDG/shared-storage configuration is respected. Run
`npm run source:paths` to inspect the actual locations. Existing model files are
reused, and no private Akatz repositories or machines are required.

See [SOURCE_INSTALL.md](SOURCE_INSTALL.md) for changing storage locations, worker
setup, upgrades, troubleshooting and development commands, and
[the validation record](release/SOURCE_VALIDATION.md) for tested configurations.

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

The minimum resource catalog contains six required files (about 56.9 GiB):
SAM 3.1, H3 FL2VA, Viggle Remake Swap, the text encoder and both VAEs.
Pika and Character Swap V1.5 add about 2.0 GiB and are optional. Whisper Medium.en
is a separate optional 1.5 GB CPU download. **Settings → Transcription** offers
Auto, Local Whisper and OpenAI. Auto prefers installed Whisper, then a saved
OpenAI key; an explicit choice never silently changes provider. Without either,
the editor explains what to install or configure before submitting a job.

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
