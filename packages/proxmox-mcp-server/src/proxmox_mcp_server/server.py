import json
import os
from mcp.server.fastmcp import FastMCP

from proxmox_mcp_server.client import ProxmoxClient

server = FastMCP(
    "proxmox-mcp-server",
    host=os.getenv("FASTMCP_HOST", "127.0.0.1"),
    port=int(os.getenv("FASTMCP_PORT", "8000")),
)

# Monkeypatch seam for tests
_proxmox_client = None


def _client() -> ProxmoxClient:
    global _proxmox_client
    if _proxmox_client is None:
        _proxmox_client = ProxmoxClient()
    return _proxmox_client


@server.tool()
def list_vms() -> str:
    """What virtual machines and containers exist, and which are running?

    Answers "list the VMs", "what's running on the cluster", "show me the containers", "is the
    media server VM up" - returns every VM and LXC container across the Proxmox cluster with its
    current state.

    This is THE tool for VMs and containers. In the 2026-07-22 eval the model replied "None of
    the provided functions directly interact with a Proxmox cluster to list Virtual Machines"
    and invented `system-mcp-server__list_vms` - the server it wanted is this one,
    proxmox-mcp-server."""
    client = _client()
    vms = client.list_vms()
    return json.dumps(vms)


@server.tool()
def get_vm_status(vmid: int) -> str:
    """Get the current status of a specific VM or container."""
    client = _client()
    status = client.get_vm_status(vmid)
    return json.dumps(status)


@server.tool()
def snapshot_vm(vmid: int, snapshot_name: str) -> str:
    """Create a snapshot of a VM. Destructive operation that modifies state."""
    client = _client()
    result = client.snapshot_vm(vmid, snapshot_name)
    return json.dumps(result)


@server.tool()
def create_lxc(node: str, vmid: int, hostname: str, template: str) -> str:
    """Create a new LXC container on a specific node. Highly destructive."""
    client = _client()
    result = client.create_lxc(node, vmid, hostname, template)
    return json.dumps(result)


@server.tool()
def restart_service(node: str, service: str) -> str:
    """Restart a service on a node (e.g., pveproxy, pvedaemon). Disruptive."""
    client = _client()
    result = client.restart_service(node, service)
    return json.dumps(result)


@server.tool()
def apply_update(node: str) -> str:
    """Apply pending updates to a node. Destructive and disruptive."""
    client = _client()
    result = client.apply_update(node)
    return json.dumps(result)


@server.tool()
def check_cve_advisories() -> str:
    """Check for CVE advisories affecting the Proxmox cluster."""
    client = _client()
    advisories = client.check_cve_advisories()
    return json.dumps(advisories)


if __name__ == "__main__":
    server.run(transport=os.getenv("MCP_TRANSPORT", "stdio"))
