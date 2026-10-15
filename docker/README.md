# H3 Character Swap — Docker alpha

A local character-swap workbench: import a clip and character reference, track
the actor, render takes, and assemble your chosen results. Shot anchors are
optional uploads. The app includes a prompt you can copy into your own image
chat to make one. Prompts and generation settings save automatically.

## What you need

- Linux x86-64, Docker Engine with Compose, and NVIDIA Container Toolkit.
- An NVIDIA GPU supported by the bundled runtime. The tested machine is an
  RTX 4090 (24 GB VRAM), 128 GB RAM, NVIDIA driver 610.43.02. Other GPUs/RAM
  configurations need validation; WSL2 has not been qualified yet.
- About 43.2 GiB for the seven models, plus the images, video files and scratch
  space. Existing matching ComfyUI model files can be reused.

You do not need Python, Node, ComfyUI, Codex or an OpenAI API key on the host.
No model weights or user footage are included in the images.

## Start

Unpack the installation bundle into a folder. It contains `compose.yaml`,
`images.env`, optional overrides, model source information and notices.

For an offline trial, first load the supplied image archive:

```sh
docker load --input h3-workbench-images.tar
```

For a registry release, use `docker compose --env-file images.env pull` instead.
The current candidate is **not yet published to Docker Hub**. Do not attempt to
pull these unpublished tags. A registry release will contain tested manifest-digest
pins in `images.env`; no `latest` tag is needed.

Then start the app:

```sh
docker compose --env-file images.env config --quiet
docker compose --env-file images.env up -d --no-build
docker compose --env-file images.env ps
```

Open port 8898 in a browser on the same computer. `H3_PORT` in `images.env`
changes that port. The port is bound to loopback; remote access needs a private
reverse proxy. Only the app is exposed, never ComfyUI or the executor.

On a Linux host already configured for NVIDIA CDI, add
`-f compose.yaml -f compose.cdi.yaml` to every Compose command.

## Reuse your ComfyUI models

Set an absolute host directory in `images.env`:

```dotenv
H3_MODELS_DIR=/absolute/path/to/ComfyUI/models
```

Start with the model-folder override:

```sh
docker compose --env-file images.env -f compose.yaml -f compose.models.yaml up -d --no-build
```

Add `-f compose.cdi.yaml` if needed. This mounts your existing folder directly
at `/models`; it does not copy the folder into Docker. The folder must exist
and be writable by container UID/GID 1000 so missing models can be installed.
Use a dedicated writable folder if your current store has incompatible permissions;
do not recursively change ownership of another application's files.

The directory should contain the normal `checkpoints`, `diffusion_models`,
`text_encoders`, `vae` and `loras` subfolders. Exact expected relative paths and
SHA-256 values are listed in `models-v1.json`. Keep the in-app storage location
at `/models`. Choose **Scan** to index and verify files already present.
Files with matching names but different contents are preserved and reported;
they are never silently overwritten.

## First result

1. The app opens **Resources**. Scan existing models, then choose **Install
   required** to acquire the missing ones. Progress stays on this page. The
   runtime is already installed; the GPU process starts when a job needs it.
   If a download stops, choose Install required again. Completed files are
   verified and reused; the interrupted file starts again.
2. Once all seven models are ready, choose **Continue to Projects** and create
   a project. Import a short clip with audio and confirm its shots.
3. Upload a character reference. Optionally upload a shot anchor made in your
   own image chat; **Get anchor prompt** provides the prompt and input downloads.
4. Open **Render**. Select the actor with include/exclude points, track the shot,
   and scrub through the mask. Apply any frame corrections before rendering.
5. Queue a take. Review it, try another seed if needed, and favorite promising
   takes. In **Review**, choose a take for each shot and assemble the final clip.
6. Play and download the output. Character consistency is experimental: the
   same inputs can succeed with one seed and drift with another.

All seven GPU models are required: SAM 3.1, MiniMax H3, Qwen encoder, audio/video
VAEs, FL2V Turbo8 and the V1 Character Swap Pro LoRA. LTX, DWPose, person-removal
and V2 models are outside this MVP. See `MODEL_TERMS.md` for their upstream terms.

## Keep your work

Projects, media, library entries, accepted jobs and downloaded models live in
persistent volumes. Keep the complete matched app/worker pair and those volumes
when updating. Use `docker compose stop` to stop the app after jobs finish.
**Do not use `down -v`** unless you intend to delete your projects and models.

Before an update, wait for current jobs to finish and back up the data volumes.
Load/pull the new matched pair, replace `images.env`, and run the same Compose
command. A browser refresh or app restart does not require submitting a job again.
Inspect the existing job before retrying an uncertain request.

See `AGENT_INSTALL.md` for GPU preflight and recovery, and `VALIDATION.md` for
what has actually been tested. Docker Hub/GitHub publication and automated image
builds are follow-up steps; a local source commit is not a published release.

## Focused workflow recipe

New builds use `python runtime/build_recipe.py DESTINATION`. It copies the tracked
ComfyUI dependency lock and the single `h3_character_swap` node package, and derives
model identities from the seven-file resource catalog. `prepare_context.py` rejects
broad legacy captures and uses `runtime/allowed_nodes.json` for the reviewed graph
surface. The app payload carries only `templates/render.json`; experimental alpha
workflows and face overlays are not included.

The standalone [ComfyUI workflow](../workflows/README.md) and app use this same
generation core. Existing alpha Docker bundles still contain their original frozen
worker recipe until a new matched app/worker release is built and qualified.
