# forgejo-mcp-server

The Forgejo MCP server for Project Tau — Phase 31, gives Tau tool access to the private git
hosting stood up in Phase 28 (see `project-tau-plan.md`). Wraps
Forgejo's REST API (Gitea-compatible) as MCP tools. Independently deployable, like every domain
server in Tau's architecture — this package does not depend on `tau-core`.

## Layout

```
src/forgejo_mcp_server/
  client.py   ForgejoClient - thin async httpx wrapper around Forgejo's REST API
  server.py   FastMCP server: tool definitions, .env loading, response-shape simplification
tests/
  test_client.py   ForgejoClient against httpx.MockTransport - no real network
  test_server.py   tool functions called directly, with a monkeypatched client seam
```

## Tools

- `list_repos()` — every repo on the instance: name, description, private/public, default branch,
  last updated.
- `get_repo(repo_name)` — full details of one repo.
- `list_commits(repo_name, limit=10)` — recent commits on the default branch.
- `list_issues(repo_name, state="open")` — issues, filtered by state.
- `create_issue(repo_name, title, body="")` — files a new issue. This is the tool
  `tau-core/config/cdg_rules.yaml`'s `forgejo-create-issue-needs-approval` rule gates behind human
  approval — it's the one tool here that writes real state, everything else is read-only.

## Setup

```bash
python -m venv .venv
.venv/Scripts/activate        # .venv/bin/activate on Linux/macOS
pip install -e ".[dev]"
cp .env.example .env           # fill in FORGEJO_URL + a token, never commit .env
pytest
```

Generate `FORGEJO_TOKEN` from Forgejo's own UI (Settings → Applications → Generate New Token,
scopes `read:repository` + `write:issue`) or non-interactively via its CLI on the server:
`docker exec -u git forgejo-server-forgejo-1 forgejo admin user generate-access-token --username
<user> --token-name tau-mcp-server --scopes read:repository,write:issue` (used to create the token
this deployment actually runs with, since Forgejo's HTTP API can't issue new tokens for a 2FA-
protected account without an OTP — the CLI bypasses that entirely).

## Wiring into tau-core

Registered in `tau-core/config/servers.docker.yaml` (Docker Compose deployment, streamable_http)
and `servers.yaml` (local/non-Docker dev, stdio subprocess — needs
`pip install -e ../forgejo-mcp-server` into whatever environment `tau-core` runs `command: python`
with). `FORGEJO_URL`/`FORGEJO_TOKEN`/`FORGEJO_OWNER` are read from *this* package's own `.env` (via
`python-dotenv`), not tau-core's — keeps this server's credentials self-contained regardless of
which process spawns it, same pattern as every other domain server here.

The server intentionally reads its env vars lazily, per tool call, rather than at process startup,
so it can be registered and its tools listed even before real Forgejo credentials exist — only
calling a tool without them set returns a clear `RuntimeError`.

## Manual smoke test

Either:
- Point `tau-core/examples/chat_repl.py` at it (once this package is installed into tau-core's
  venv) and ask it "what repos do I have" (should list them, read-only, executes immediately) vs.
  "file an issue on tau-kai titled X" (should come back pending approval, never executed until a
  human approves it), or
- Connect directly the same way `tau-core/tests/test_mcp_client_manager.py` does for the `echo`
  example server, using `tau_core.mcp_client.MCPClientManager` with a `ServerConfig` pointing at
  `python -m forgejo_mcp_server.server`.
