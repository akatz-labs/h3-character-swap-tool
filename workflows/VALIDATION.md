# Workflow qualification — 2026-10-15

The standalone canvas and exported API were exercised in a new ComfyGit environment with only `h3_character_swap` installed. The pinned ComfyUI core supplies native H3, SAM and audio/video utility nodes. The old six-pack recipe was not loaded.

## Checks completed

- Canvas: 36 nodes, including six Markdown notes, arranged in five colored groups. Reopening it through the ComfyUI frontend produced the exact same 30-node API graph.
- Browser execution: the canvas queued successfully, kept its fixed seed, and displayed a playable completed MP4 in the result node without JavaScript errors.
- Standalone API: SAM detection, selected-person tracking, per-frame recovery and 64 px mask expansion completed before generation. The test recovered six missing tracking frames after the source-timing correction.
- App API: the app's own graph builder produced a successful 19-node anchor-free saved-mask render. Its source geometry and 26-frame duration were preserved.
- Optional controls: anchor-free and anchor-enabled generation completed. A separate generation with both masked diffusion and audio guidance disabled also completed and retained source audio.
- Continuation: a deliberately shortened 22-frame window exercised two windows on a 26-frame clip. The second started at frame 21, used 18-frame history and discarded its one-frame overlap. The final result contained exactly 26 frames.
- Runtime checks: silent input conditioning, optional-anchor validation and resizing, Euclidean mask expansion, empty-mask rejection, and H−1 source-video/reference-audio alignment passed in the pinned Python environment.
- App/source checks: 232 Python tests, 10 Node runtime tests, web build, runtime build and Python compilation passed.

The test source was 576 × 416 at 24 fps. This qualified wiring, controls and timeline behavior; it was not a new 1 MP quality benchmark. Preparation preserves an aligned source already below the selected pixel budget. A rounding bug inherited from the prior preparation helper was fixed: the 26-frame CFR input no longer acquires a duplicated 27th tail frame.

The continuation output's source-audio correlation measured **0.999573** over 51,999 decoded mono samples at 48 kHz. AAC decoder padding is separate from the MP4's reported video/audio timeline duration.

## Matched comparison

A further render used the previous app result's exact source, reference, anchor, saved mask, detailed prompt, seed 194731, model chain and 124/18 window settings. Both outputs contained 26 frames. Opening, middle and final frames showed consistent character behavior. They were not pixel-identical (decoded-frame PSNR 26.31 dB, mean absolute RGB error 4.95/255); this is a visual regression check on one short clip, not a general quality claim.

The detailed prompt sustained the reference character much better than the generic starter prompt. Describe the character's distinctive face, skin, hair, proportions and clothing, then the source action and continuity to preserve. The starter text in the canvas is intended to be edited.

## Capacity and quality

The test host was a 24 GB RTX 4090 with approximately 128 GB RAM. CPU quotas, RAM and swap limits were unrestricted. The full-residency attempt exhausted GPU memory while applying the LoRAs: 22.05 GiB allocated, 18.5 MiB free, with a further 220.5 MiB requested. Retrying with normal ComfyUI model offloading passed without changing the generation graph or settings.

Character retention remains experimental. The short generic-prompt test retained much of the source person's identity; an anchor established the character at the opening but did not ensure sustained identity. These are functional qualification clips, not a quality-approved showcase.

## Release boundary

The source includes the focused app render template, worker adapter and reproducible recipe builder. Existing alpha.3 Docker images and the running app still use their previous frozen recipe. A new matched Docker app/worker bundle requires its own build and qualification. No registry or public repository publication is part of this workflow package.

The downloadable source package includes workflows, node source, dependency locks, model catalogs and licenses. It contains no model weights, test footage or user project data.
