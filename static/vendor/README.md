# H3 report player

`h3-player.js` is derived from the shared Akatz Labs `H3Player.Atlas` report
player, authored for the H3 comparison tooling on 2026-10-03. This first-party
code is covered by the repository MIT license. The original upstream file is
identified by its content hash below.
Upstream SHA-256: `967be85500e8f7edb44036b120b84a40080d433fbc20bb05b2162c94b0c898bd`.

The capture, cached drawing, CSS wipe, coalesced seek and cancellable playback
logic is retained. Workbench changes remove global styles/DOM observers in
favor of modal-scoped CSS and add `dispose()` for listeners, frame callbacks,
media and the debug registry. Keep this lifecycle behavior when refreshing
from the shared source. Follow the build and browser review steps in [CONTRIBUTING.md](../../CONTRIBUTING.md) after changes.

`app-kit-icons.js` is generated during `npm run build:web` by rendering the
pinned App Kit Icon component. It currently provides Lucide RefreshCw to the
legacy editor. Do not edit its SVG paths; change the shared component and rebuild.
Upstream license notices are generated alongside it in app-kit-icons.LICENSE.txt.
