# Shared editor ownership

The reusable UI, project rules and workflow recipe live in `packages/editor-core`.
Both applications mount the same Workbench: project selection, Shots/Assets,
Edit/Outputs, mask and segment editors, guide creation, prompts and dialogue.
The application shell supplies only its header and backend adapter.

The local shell in `web/src` uses `local-backend.ts` for native HTTP, assets,
health and execution preferences. `WorkbenchHeader.tsx` supplies the Akatz Labs
brand, GitHub, Support and Settings. `LocalSettings.tsx` reuses app-kit model and
worker setup controls and stores worker/offloading preferences in browser-local
storage. It makes no Platform authentication or wallet requests. Offloading is
an explicit opt-in and defaults off. Project documents do not store this machine
preference; each accepted job freezes its effective execution settings.

The private hosted shell imports the pinned core package. Its adapter owns
Platform transport, account/session handling, uploads and wallet updates. It
uses the shared Platform header and server-owned execution settings. Hosted API
planning runs in-process; ordinary reads and edits never start Python containers.
Python rendering and filesystem persistence remain in the local backend.

## One workflow

`packages/editor-core/workflow.json` pins FL2VA, Pika six-step sampling and the
Character Swap V1.5 LoRA at strength 1.0. Both render admission paths enforce that
recipe. Timeline guides are timed AddGuide inputs, separate from character
picture references and the source video. The model catalog provisions this
workflow and SAM; it does not remove previously downloaded model files.

On opening an older shot, the editor requests a revision-guarded workflow update.
The backend archives its exact prior segment layout, then updates only workflow,
LoRA and connection fields. Masks, guides, manual prompts, characters, seeds,
outputs and frozen job inputs remain intact. Active jobs block this update. Reads
alone do not change stored projects. Historical parsers and frozen-job execution
remain readable; they are not alternate choices for new submissions.

## Ownership and verification

Shared changes belong in the core package. Local machine controls belong in
`web/src/LocalSettings.tsx`; account/header controls belong in the private hosted
shell. Keep provider, authentication and storage details behind adapters. Server
validation remains authoritative regardless of browser previews.

Build/test the local consumer, pack the core, record its commit and checksum in
the private consumer, then test that consumer before deployment. The domain
fixtures cover preserved history as well as the new workflow admission rules.
The original development worktree is untouched, with its source captured on
`preserve/local-editor-before-core-20261113`. No public push is implied.

## Reusing an existing remote worker

The source launcher reads `<data>/source-workers.json`. With the editor stopped,
set its existing `local.target` to an SSH executor target, for example:

```json
{"local":{"target":{"kind":"ssh","host":"user@gpu-worker","port":8841}}}
```

`local` is the local backend's worker slot; the GPU may be on another machine.
The launcher validates the saved target and uses it for both setup/status and
job transport. Restarting preserves the target and project store. An explicit
conflicting `--worker-port` is rejected, rather than silently redirecting jobs.
SSH uses the existing account configuration; no browser credentials are required.

The executor must advertise managed-runtime-v1, accepted-attempt-v1 and
cancel-admission-v1. Register existing verified H3 installations through its
plan/install API without a setup command to adopt their files without downloading
models. Separate SAM and Pika registrations are supported; readiness combines
available operations. Keep their existing ComfyGit paths, model store, managed
session ownership and GPU admission contract.

## Local OpenAI guide credentials

The source launcher defaults to `local-openai`. Local settings → Guide generation
accepts an OpenAI API key through `PUT /api/local-settings/openai`; GET and DELETE
return only `{configured: boolean}` with `Cache-Control: no-store`. These routes
are unavailable when another provider is selected. The key is kept in
`<data>/.credentials/openai-api-key`, an owner-only (0600) file inside a 0700
directory, outside the project database, media, and job receipts. This is private
filesystem storage, not encrypted storage or an OS keychain; the machine owner
can inspect it. There is no API or UI to reveal the saved value. The browser
clears its password field on submission and never persists it in web storage.
The key belongs to the computer running the local backend, even when its browser
is opened from another computer.

Local guide jobs freeze source/reference assets, prompt, dimensions and quality,
then call the [direct Image Edits API](https://developers.openai.com/api/reference/resources/images/methods/edit)
using the saved key. Source is first, followed by selected references. Flare and
medium quality retain the existing guide defaults; low/high remain available.
A durable submission receipt prevents paid retries after ambiguous failures.
Collection can reattach a verified output already saved to disk but cannot
recover a synchronous response lost before saving. Provider error bodies and
credentials are never persisted in job errors. Hosted credentials and billing
continue to belong to the hosted backend.
