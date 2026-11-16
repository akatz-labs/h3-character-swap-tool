# H3 shared editor core

The public H3 repository owns this package. The local application consumes its
workspace; the private hosted application and API consume the same pinned tarball.
Do not copy its React, CSS, static scripts or planner into a downstream app.

- `@akatz/h3-editor-core`: `Workbench`, `configureEditor`, and adapter types.
- `@akatz/h3-editor-core/styles.css`: shared editor and component styling.
- `@akatz/h3-editor-core/domain`: synchronous, pure `plan(request, environment?)`.
- `@akatz/h3-editor-core/workflow`: the single versioned execution recipe.
- `@akatz/h3-editor-core/static/*`: assets for the consuming application's static route.

Call `configureEditor` before mounting. The adapter supplies transport, asset URLs,
project selection, identity, draft registration, optional browser upload, and job
notifications, health and execution preferences. The local shell owns its header
and Settings dialog; the hosted shell owns its Platform header. Both mount the
same Workbench without injecting field-specific controls. Hosted authentication, credits, shared header,
SDK, upload signing and account isolation stay in the private application.

The planner clones requests, validates edits, and returns derived views or changed
project documents. Callers own authorization, optimistic revision checks, durable
persistence, event publication and execution. Never trust browser planning as server
authorization. It has no filesystem, network, provider keys or service lifecycle.

Run `npm run build --workspace @akatz/h3-editor-core` from the repository root,
then `npm test --workspace @akatz/h3-editor-core`. Golden fixtures were captured
from the hosted Python planner at revision 039440e before extraction; they cover
Relay windows, segment state, prompts, character references, guides and imports.
Fixed time/IDs make parity checks repeatable; input immutability is checked too.

Build before packing with `npm pack --workspace @akatz/h3-editor-core`. Downstream
consumers must pin the archive and its digest, rebuild both UI/API, and test both
local and hosted transports before promotion. No npm publication is required.

The `segments/workflow` edit archives the old layout before applying the supported
recipe. New render admission rejects older layouts until that explicit update.
The adapter supplies local worker/offloading preferences; hosted render admission
uses server configuration. Frozen historical jobs and media are not rewritten.
