# Compiled, hash-verified CPU payload assembled by prepare_context.py.
FROM debian:bookworm-slim@sha256:7c7b2c966bc9ee8cedfeef67e0e279108992c77681fa595db4a9d65c06ccc587
LABEL org.opencontainers.image.title="H3 Character Swap app" \
      org.opencontainers.image.version="0.1.0-alpha.3" \
      org.opencontainers.image.licenses="MIT"
RUN apt-get update && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends ca-certificates libgomp1 libstdc++6 fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir /data && chown 1000:1000 /data
COPY --chown=1000:1000 payload /opt/h3-workbench
# The CPU app previews the same pure window planner used by the worker.
COPY --chown=1000:1000 worker/recipe/nodes/h3_character_swap/planning.py /opt/h3-workbench/worker_nodes/h3_character_swap/planning.py
COPY --chown=1000:1000 release-notices /usr/share/doc/h3-release
ENV AKATZ_DESKTOP_REGISTRY=/data/registry H3_CONTAINER_PAYLOAD=/opt/h3-workbench \
    H3_CONTAINER_DATA=/data/projects PYTHONDONTWRITEBYTECODE=1 HOME=/data/home
USER 1000:1000
WORKDIR /opt/h3-workbench
EXPOSE 8080
ENTRYPOINT ["/opt/h3-workbench/runtime/node/node", "/opt/h3-workbench/dist/runtime/container/app.js"]
