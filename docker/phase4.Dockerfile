# phase4-upgrade-pipeline is flat files at its own repo root (no src/ layout, no __init__.py
# package) - `pip install -e .` installs its pyproject.toml dependencies (mcp, ollama) but
# doesn't need to discover any package, since the .py files are copied straight into WORKDIR
# and resolve each other via sys.path[0] the same way they do when run locally.
FROM python:3.11-slim

WORKDIR /app
COPY pyproject.toml ./
# crypto_store.py (Phase 10: encryption-at-rest for the proposal store) was added after this
# list was first written and never added here - found by actually booting the stack (Phase
# 10.5): proposal_store.py imports it, so the container crash-looped with
# "ModuleNotFoundError: No module named 'crypto_store'" on every start, and the healthcheck
# never had a chance to pass. List every module this package's own files import from each
# other, not just the entry point, since there's no src/ layout to COPY as a whole directory.
COPY phase4_server.py proposal_store.py reviewer_agents.py crypto_store.py ./
RUN pip install --no-cache-dir -e .

ENV PYTHONUNBUFFERED=1 \
    FASTMCP_PORT=8000

CMD ["python", "-m", "phase4_server"]
