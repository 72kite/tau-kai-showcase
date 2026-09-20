# Generic image for tau-kai's src-layout MCP domain servers (pyproject.toml + src/<package>/).
#
# Build context is the individual server's own directory; MODULE is that package's import name.
# Example (also see ../docker-compose.yml):
#   docker build -f docker/mcp-server.Dockerfile --build-arg MODULE=proxmox_mcp_server \
#     -t tau-kai/proxmox-mcp-server ./packages/proxmox-mcp-server
#
# Runs stdio by default (matches non-Docker dev); docker-compose.yml sets MCP_TRANSPORT=
# streamable-http + FASTMCP_HOST=0.0.0.0 so tau-core can reach it over the compose network
# instead of spawning it as a local subprocess.
FROM python:3.11-slim

ARG MODULE
ENV MODULE=${MODULE}

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir -e .

ENV PYTHONUNBUFFERED=1 \
    FASTMCP_PORT=8000

# `sh -c "exec python ..."`, not bare shell-form: we still need the shell to expand ${MODULE},
# but `exec` replaces the shell with python so python is PID 1 and receives SIGTERM directly.
# Without exec, /bin/sh stays PID 1 and does not forward signals, so `docker stop` is ignored
# and every server is SIGKILLed after the grace period - an ungraceful shutdown on every stop.
CMD ["sh", "-c", "exec python -m ${MODULE}.server"]
