# Bundled shared packages

These are immutable npm dependencies, maintained separately from H3. The two
`.tgz` files are the exact packages used by `npm ci`. `manifest.json` records
versions, SHA-256 checksums, MIT licenses, source archives and provenance.

The `-source.tar.gz` archives include the TypeScript/JavaScript/Python/Rust/UI
sources, package lock and build configuration needed to rebuild the shared
libraries. Extract an archive into a new directory and run `npm ci --ignore-scripts`
and `npm run build` inside `package/`. Public npm dependencies require internet.
The included desktop Rust/UI sources are build inputs, not compiled native apps.

Run `uv run --no-project --python 3.12 python scripts/verify_vendor.py --rebuild` from the H3 root to verify hashes
and compare rebuilt `dist/` files byte for byte with the installed package archive.
Temporary builds use the OS temporary directory (override `TMPDIR` if needed).

App Config's source is taken from the shipped package. App Kit's original source
was captured from its integration checkout and verified against the shipped output.
The listed upstream commits are base commits followed by local development:
archive hashes, not those commits alone, identify this release. The source build
configuration was made standalone; it does not need the original monorepo.

To update a dependency, prepare a new version and source snapshot, rebuild and
verify it, update the manifest and npm lock together, and run the H3 checks. Do
not edit an existing archive in place or require a sibling checkout. Publishing
these libraries independently can later replace the local file dependencies.

App Kit `0.1.0-alpha.9-source.3` adds reviewed model-removal controls, explicit
model-location notes, and consistent gear/recycling icons. App Config
`0.11.0-alpha.10-source.1` allows model removal when an executor-owned runtime is
confirmed stopped and its loopback port refuses connections; timeouts, unknown
owners and active sessions remain blocking. These snapshots include only the
reviewed changes to the prior source release, excluding unrelated upstream edits.
Neither package is claimed as an npm registry publication.

App Kit `0.1.0-alpha.9-source.4` adopts `lucide-react` 1.57.0 for the shared
refresh icon. Its public npm dependency is pinned in the app and source archive
lockfiles. Upstream license notices are included. Other existing icon names and
glyphs are unchanged. This supersedes the source.3 recycling glyph.

App Config `0.11.0-alpha.10-source.3` adds the shared ecosystem path resolver,
completed-installation discovery, reuse/startup of the existing media and executor
services, and a Python lease helper for the Hugging Face cache. Its corresponding
source archive contains the focused ecosystem integration tests and usage contract.
H3 consumes these shared APIs instead of creating a second model index or dispatcher.
