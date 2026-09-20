# tau-core is the MCP Host - the only network-facing piece of Project Tau (the frontend talks
# to it over tau_core.web). Unlike the domain servers, it already binds 0.0.0.0:8000 by default
# (see src/tau_core/web/__main__.py) so no transport switch is needed here.
#
# In docker-compose.yml, TAU_SERVERS_CONFIG_PATH is set to config/servers.docker.yaml so tau-core
# reaches every domain server over the network (streamable_http) instead of spawning it as a
# local subprocess - which also means, unlike local dev, this image does NOT need any domain
# server package `pip install -e`'d alongside it (see README's "Known gaps" section 6/7).
#
# ONE exception: openscad-mcp-server (Phase 25's third-party CAD server, npm). Its published
# HTTP mode is genuinely broken in two independent ways as of 1.0.5 - a bootstrap-argument bug
# that makes it always start in stdio mode regardless of flags, AND (found while trying to patch
# around the first bug and actually run it as its own streamable_http container, 2026-08-26) its
# HTTP transport is constructed once at startup and reused across requests despite being built in
# STATELESS mode, which its own SDK rejects at the second request ("Stateless transport cannot be
# reused across requests"). Filed upstream 2026-08-26: github.com/fboldo/openscad-mcp-server/
# issues/3 (the bootstrap bug) and /issues/4 (the stateless-transport-reuse bug) - once both are
# fixed there, drop this workaround and move this server to its own streamable_http container
# like every other domain server (see servers.docker.yaml).
# stdio is the one mode the package's own maintainer has ever actually exercised (bootstrap always
# resolves there today, bug or not), so tau-core spawns it as a subprocess of itself here - the
# same code path local/non-Docker dev already uses via servers.yaml, just pre-installed at build
# time instead of npx-installing over the network on every cold start. See
# config/servers.docker.yaml's own entry for this server. Revisit (move it to its own container,
# like every other domain server) once the HTTP-mode bugs are fixed upstream.
FROM python:3.11-slim

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
COPY config ./config
RUN pip install --no-cache-dir -e .

# Debian bookworm's own nodejs/npm packages - no external apt repo needed, this is a WASM-backed
# tool with no native build step of its own that would need a newer toolchain.
RUN apt-get update && \
    apt-get install -y --no-install-recommends nodejs npm && \
    rm -rf /var/lib/apt/lists/* && \
    npm install -g openscad-mcp-server@1.0.5

ENV PYTHONUNBUFFERED=1 \
    TAU_WEB_HOST=0.0.0.0 \
    TAU_WEB_PORT=8000

EXPOSE 8000
CMD ["python", "-m", "tau_core.web"]
