# Frontend development in the app container

The development overlay runs Vite on the app's existing port 8080 and the CPU
app edge on loopback port 8081. API, App Kit, media, and WebSocket requests proxy
to that backend. The packaged CPU payload and existing data volume remain the
backend's source of truth.

Install the lockfile dependencies and compile the small runtime entry once:

```sh
npm ci
npm run build:runtime
```

Set `H3_DEV_SOURCE` to this checkout on the Docker host and
`H3_DEV_NODE_MODULES` to its installed `node_modules` directory. For a private
reverse proxy, set `H3_DEV_PUBLIC_ORIGIN` to its complete browser origin and
include that origin in the normal `H3_TRUSTED_ORIGINS` setting. Vite uses that
exact hostname and WebSocket endpoint. Keep the existing loopback host-port
mapping.

With the normal release variables loaded, apply only the app service:

```sh
docker compose -f docker/compose.yaml -f docker/compose.dev.yaml \
  up -d --no-deps --no-build --pull never app
```

Before recreating the app, let active application jobs finish. Check
`/__h3/dev` for Vite readiness and `/api/health` for backend readiness. The
worker does not need recreation.

CSS and compatible React components update in place. The React root module
can require a page reload. Changes in `static/` or
`scripts/build_legacy_bridge.mjs` rebuild the generated editor bridge and
reload the browser. A full reload can discard unsaved browser drafts; save
them before editing those UI sources. Do not edit `web/generated/` directly.
`web/public/static` serves the current static assets during development only.

When editing on a different host, prepare a dedicated source mirror containing
`package.json`, `package-lock.json`, `web/`, `static/`,
`scripts/build_legacy_bridge.mjs`, `scripts/dev_container.mjs`, and the compiled
`dist/runtime/` directory. Supply matching Linux dependency files separately.
Then run the frontend-only synchronizer from the authoritative checkout:

```sh
python3 scripts/sync_frontend_dev.py \
  --host USER@GPU-HOST --destination /absolute/owned/dev/source --watch
```

It mirrors an explicit list of frontend inputs, including deletions within
those directories. It does not copy data, models, backend source, credentials,
or the entire repository. Dependency changes require refreshing the installed
dependencies; backend/runtime changes still need their normal build and
restart. Removing the development overlay restores the compiled frontend.

Vite proxy and WebSocket options follow the
[Vite server documentation](https://vite.dev/config/server-options).
