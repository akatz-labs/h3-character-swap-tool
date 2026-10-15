# Contributing

Start with [the source installation guide](SOURCE_INSTALL.md). Use Node.js 24,
Python 3.12, uv and FFmpeg/ffprobe. GPU worker installation is only needed for
rendering; the following checks run on a CPU machine:

```sh
npm ci
uv sync --locked --extra test --python 3.12
npm run source:build
npm run check:runtime
uv run --locked --extra test pytest
python3 scripts/check_source.py
uv run --no-project --python 3.12 python scripts/verify_vendor.py --rebuild
```

The CI workflow runs these checks on Linux. A prepared workflow does not mean a
GitHub run has passed; see [validation](release/SOURCE_VALIDATION.md) for executed
checks and GPU limitations. A normal build must leave tracked files unchanged.

For frontend development, follow the hot-reload instructions in
[SOURCE_INSTALL.md](SOURCE_INSTALL.md). Review desktop and narrow layouts, keyboard
navigation, the affected editor and browser console after UI changes. Confirm
that unsaved edits, project revisions and accepted worker jobs survive the flow.
Use disposable project data for test imports and deletions. Never interrupt an
active render to test controls.

Keep credentials, user media, model weights, databases and machine-specific
configuration outside Git. Changes to shared packages need a new immutable
archive, corresponding source, checksum manifest and lockfile update; see
[vendor/README.md](vendor/README.md). Add public source files to
`release/source-files.json` so exports and CI review the same contents.

The source launcher defaults to local state and explicitly configured workers.
Advanced deployments that place media on a mount should set
`H3_WORKBENCH_MEDIA_MOUNT` to its mount point and `H3_WORKBENCH_MEDIA` to a directory
inside it. Startup and imports refuse a missing mount. Keep SQLite state local.
Legacy SSH integrations can set `H3_WORKBENCH_SHARED_ROOT` and an explicit worker
configuration; they are not the supported source-worker installation path.

Submit focused changes with a description of the problem, behavior and validation.
GPU behavior needs actual runtime and output evidence, including model, workflow,
seed and input provenance. CPU tests alone do not qualify a GPU or driver.
