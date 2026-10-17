# Third-party components

The repository MIT license covers first-party H3 Workbench, Akatz App Kit and
the Akatz-authored H3 report-player derivative. It does not replace the terms
of bundled software or downloaded models.

The source release includes exact MIT-licensed App Config and App Kit archives
and their corresponding source in `vendor/`; `vendor/manifest.json` identifies
their versions and hashes. npm, Python and ComfyUI dependencies are downloaded
from their pinned public sources and retain their package licenses. A source
installation uses the host's FFmpeg; it does not bundle an FFmpeg executable.

The Docker release includes a file inventory and full collected notices at
`/usr/share/doc/h3-release`. The installation bundle carries the same records.
They identify exact package versions and available upstream license texts.

- Node.js carries its upstream `LICENSE`, including its third-party notices.
- CPython carries its upstream license; the standalone distribution and Python
  dependencies retain their own component notices.
- React, React DOM, Zod and the shared SDK/Kit dependencies retain their
  upstream package notices.
- The source-built FFmpeg executable is distributed under GPL version 3.
  Exact FFmpeg, x264, zlib, FreeType and HarfBuzz source archives, the build recipe and recorded
  configuration accompany the release. FFmpeg runs as a separate process.
- The exact GPL-3.0 H3 Relay source archive in `vendor/` is pinned by repository,
  commit and SHA-256 in `runtime/relay-lock.json`. Its `LICENSE`, `NOTICE.md` and
  component licenses are included in the archive and worker installation.
  The INT8 kernel derivative retains NVIDIA copyright and Apache-2.0 terms.
  `worker_nodes/h3_workbench_runtime` contains only first-party session integration.
- ComfyUI, its custom nodes, PyTorch, CUDA libraries and other worker packages
  retain the terms recorded in the worker inventory and their source/package
  notices. The frozen recipe and materialized source are preserved.
- Debian packages retain their notices in `/usr/share/doc` and their package
  metadata in the image inventory.

No model weights or user footage are included. `docker/models-v1.json` records
the model sources, hashes and separate license links. Review those terms before
acquiring and using a model. Public download availability does not replace its
license.
