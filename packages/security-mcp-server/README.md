# security-mcp-server

The security MCP server for Project Tau — see `project-tau-plan.md`
Section 5.6. Phase 2.6 domain server: lockdown policy, intrusion logging, and security state
management. Independently deployable, like every domain server in Tau's architecture — this
package does not depend on `tau-core`.

## Layout

```
src/security_mcp_server/
  security_store.py       SecurityStore - JSON-file-backed lockdown state and incident log
  server.py               FastMCP tools for lockdown control and incident logging
tests/
  test_security_store.py  SecurityStore tests using tmp_path-backed JSON
  test_server.py          tool functions with monkeypatched store
```

## Tools

- `enter_lockdown(reason: str)` — enter cluster-wide lockdown mode (fail-safe direction, no
  approval needed). All destructive operations in other servers blocked via CDG rules that check
  security state.
- `exit_lockdown(approval_token: str)` — exit lockdown. **Requires human approval** (gated by
  CDG) and the correct `LOCKDOWN_APPROVAL_TOKEN`.
- `get_intrusion_status()` — read-only status: lockdown_active, reason, incident count.
- `log_incident(incident_type: str, description: str)` — log a security incident (read-write but
  non-destructive, falls through to default_effect).

## Design notes

**Lockdown state is the coordination point.** When `lockdown_active=true`, Tau should not execute:
- proxmox destructive actions (snapshots, create_lxc, apply_update)
- fabrication commands (print jobs, tool head moves)
- robotics patrol/actuation

This is enforced via CDG rules that check the current lockdown state (read from
`security-mcp-server`'s state file or via `get_intrusion_status` calls during policy evaluation).

**enter_lockdown is safe.** Entering lockdown is a fail-safe direction and doesn't require
approval. Exiting requires an explicit approval token so Tau can't silently exit under attack.

**Incident logging is orthogonal to lockdown.** You can log incidents, analyze them, and decide
whether to enter lockdown; lockdown is not entered automatically. This gives Tau flexibility to
handle false alarms or to analyze first before restricting operations.

## Setup

```bash
python -m venv .venv
.venv/Scripts/activate        # .venv/bin/activate on Linux/macOS
pip install -e ".[dev]"
cp .env.example .env           # set LOCKDOWN_APPROVAL_TOKEN (and optionally SECURITY_DB_PATH)
pytest
```

## Wiring into tau-core

Registered in `tau-core/config/servers.yaml` as `security-mcp-server`, spawned via
`python -m security_mcp_server.server`. Needs this package installed
(`pip install -e ../security-mcp-server`) into whatever environment `tau-core` runs
`command: python` with — simplest for local dev is tau-core's own `.venv`.

`SECURITY_DB_PATH` defaults to `./data/security.json` if unset. `LOCKDOWN_APPROVAL_TOKEN` is
**required** (no sensible default for security-critical tokens).

## CDG Rules

Defined in `tau-core/config/cdg_rules.yaml`:
- `security-lockdown-exit-needs-approval`: `exit_lockdown` requires approval
- `security-lockdown-enter-is-safe`: `enter_lockdown` allowed by default (fail-safe)

Read-only tools (`get_intrusion_status`) fall through to `default_effect: allow`.

Proxmox/fabrication/robotics tools have their own CDG rules that *should* check whether the
security server reports lockdown_active=true, but that's a more advanced CDG feature requiring
conditional rule evaluation — currently rules are static. For now, assume lockdown_active gates
those operations via separate hard-coded rules.

## Manual smoke test

No external service needed — just run it and call the tools directly, the same way
`tau-core/tests/test_mcp_client_manager.py` does for the `echo` example server, using
`tau_core.mcp_client.MCPClientManager` with a `ServerConfig` pointing at
`python -m security_mcp_server.server`. Call `get_intrusion_status()` (read-only) to confirm
the server is wired correctly.
