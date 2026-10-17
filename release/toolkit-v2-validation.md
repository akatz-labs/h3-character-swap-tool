# H3 Relay toolkit integration — app 0.2.0

Local qualification completed on 2026-10-17. Remote publication and CI are
separate release checks; this record does not claim new Docker images.

The app now installs canonical H3 Relay 2.0 code from a verified source archive
(`fd9502a516d8fd7521e991b8dfe2a590bb7f0fa6`, SHA-256
`22a9e2e236db02e9cb1a1c2d0d64fe474b321a20896ad6bddbd6b5c7d5535439`).
The recipe uses native ComfyUI model loading, reference conditioning, sampling,
loops and video saving. Relay supplies shared planning, history, masks and
assembly. The app retains its reviewed anchors, NPZ masks, independent segment
contract, source soundtrack and raw decoded output. It no longer maintains a
second copy of the Relay timeline implementation.

## Executed checks

- Clean isolated worker installation verified all eight required model files.
  The actual render admission records recipe revision
  `7c7bed9edd90f4e11e59b007d5bc7ea035c9b7d6a28260b28d512ca81af09172`.
- Real SAM produced 43 masks with zero empty frames. A whole-shot V1.5 render
  completed through the installed shared executor.
- Two independent segment jobs delivered 22 and 21 frames at 960 × 512.
  The second used exactly 18 frames decoded from the first segment's 16-bit
  RGB FFV1 master. The staged history file retained its SHA-256.
- Automatic assembly produced 43 frames at 24 fps. Decoded master pixels exactly
  matched the concatenated segment masters. No overlap or supplied history was
  duplicated in delivery.
- Actual browser comparison playback advanced through the generated take with
  zero page or HTTP errors. The downloaded MP4 fully decoded and matched the
  stored output byte-for-byte: SHA-256
  `9c66f6c19c882d88de6f81a6c651c079899493a36c2c584bbe4146a526450670`.
- Source-audio correlation at zero lag was 0.999972 after AAC encoding. At 32 kHz
  the original had 57,334 samples and the encoded file 57,344; the extra ten
  samples are codec tail padding, not a sample-exact audio claim.
- 313 Python tests, 11 Node runtime checks, TypeScript/Vite builds, source
  allowlist checks and reproducible rebuilds of both shared packages passed.

The standalone old/new workflow comparison using V1.5 preserved all 120 decoded
frames and every decoded source-audio sample exactly. Character Swap and Person
Removal two-window comparisons were visually close, with mean absolute pixel
differences of 1.482121/255 and 0.827001/255 respectively. They were not
pixel-identical. Full SAM removal and a real browser reroll after a ComfyUI
restart preserved the exact preceding window and recovered cached source masks.

## Qualified environment and limits

Linux, RTX 4090 (24 GB), ComfyUI
`79be670e2d9be63e238785af307369d2b9039ed1`, frontend 1.53.6, Python 3.12,
PyTorch 2.11.0 + CUDA 12.8 and comfy-kitchen 0.2.35. CPU, RAM and swap were
unrestricted. Full residency exhausted the GPU's physical capacity; normal
offloading completed the unchanged settings with optimized kernels enabled.
The included memory policies preserve explicit high-VRAM behavior.

These fixtures qualify the migration, not universal visual quality. V1 retains
a known return-to-source-identity failure; V1.5 is the recommended new-shot
default. Existing saved choices remain intact. Other GPUs, WSL2, new Docker
images and public registry installation were not qualified in this run.
