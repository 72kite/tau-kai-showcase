# proxmox-mcp-server

The Proxmox MCP server for Project Tau — see `project-tau-plan.md`
Section 5.1. Phase 2.1 domain server: wraps Proxmox VE REST API as MCP tools for infrastructure
management. Independently deployable, like every domain server in Tau's architecture — this
package does not depend on `tau-core`.

## Layout

```
src/proxmox_mcp_server/
  client.py           ProxmoxClient - httpx wrapper around Proxmox REST API via proxmoxer
  server.py           FastMCP server: tool definitions, .env loading
tests/
  test_client.py      ProxmoxClient against mocked ProxmoxAPI - no real network
  test_server.py      tool functions called directly, with a monkeypatched client seam
```

## Tools

- `list_vms() -> str` — lists all VMs and containers across all nodes (read-only).
- `get_vm_status(vmid: int) -> str` — get current status of a specific VM/container (read-only).
- `snapshot_vm(vmid: int, snapshot_name: str) -> str` — create a snapshot of a VM. **Requires
  human approval** — snapshots modify cluster state.
- `create_lxc(node: str, vmid: int, hostname: str, template: str) -> str` — create a new LXC
  container. **Requires human approval** — highly destructive (allocates VMID, creates container).
- `restart_service(node: str, service: str) -> str` — restart a Proxmox service (pveproxy,
  pvedaemon, etc.). **Requires human approval** — disruptive (causes downtime).
- `apply_update(node: str) -> str` — apply pending package updates to a node. **Requires human
  approval** — destructive and disruptive (may require reboots).
- `check_cve_advisories() -> str` — check for CVE advisories affecting Proxmox (read-only).
  Currently a stub; real implementation would poll NVD API periodically.

## Setup

```bash
python -m venv .venv
.venv/Scripts/activate        # .venv/bin/activate on Linux/macOS
pip install -e ".[dev]"
cp .env.example .env           # fill in PROXMOX_HOST, PROXMOX_USER, PROXMOX_PASSWORD
pytest
```

## Wiring into tau-core

Registered in `tau-core/config/servers.yaml` as `proxmox-mcp-server`, spawned via
`python -m proxmox_mcp_server.server`. Needs this package installed
(`pip install -e ../proxmox-mcp-server`) into whatever environment `tau-core` runs
`command: python` with — simplest for local dev is tau-core's own `.venv`.

Unlike the other domain servers, `PROXMOX_PASSWORD` is **required** (no sensible default for
infrastructure access). The other env vars default to localhost/root@pam/port 8006, suitable for
a local dev Proxmox instance or one on the home network.

## CDG Rules

Defined in `tau-core/config/cdg_rules.yaml`:
- `proxmox-snapshot-needs-approval`: `snapshot_vm` requires approval
- `proxmox-create-lxc-needs-approval`: `create_lxc` requires approval
- `proxmox-restart-service-needs-approval`: `restart_service` requires approval
- `proxmox-apply-update-needs-approval`: `apply_update` requires approval

Read-only tools (`list_vms`, `get_vm_status`, `check_cve_advisories`) fall through to
`default_effect: allow`.

## Backends

Proxmox VE itself — either a real cluster or a local single-node installation (PVE can run in a
VM or container). This MCP server is a thin wrapper; it does not deploy Proxmox.

## Manual smoke test (once Proxmox is actually running)

Install this package and tau-core, then connect the same way
`tau-core/tests/test_mcp_client_manager.py` does for the `echo` example server, using
`tau_core.mcp_client.MCPClientManager` with a `ServerConfig` pointing at
`python -m proxmox_mcp_server.server`. Call `list_vms()` (read-only, always safe) to confirm
the server is wired correctly and can reach your Proxmox instance.
