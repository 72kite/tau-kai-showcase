import hmac
import json
import os
from mcp.server.fastmcp import FastMCP

from security_mcp_server.security_store import SecurityStore

server = FastMCP(
    "security-mcp-server",
    host=os.getenv("FASTMCP_HOST", "127.0.0.1"),
    port=int(os.getenv("FASTMCP_PORT", "8000")),
)

# Monkeypatch seam for tests
_security_store = None


def _store() -> SecurityStore:
    global _security_store
    if _security_store is None:
        db_path = os.getenv("SECURITY_DB_PATH", "./data/security.json")
        _security_store = SecurityStore(db_path)
    return _security_store


@server.tool()
def enter_lockdown(reason: str) -> str:
    """Enter lockdown mode: restrict all destructive operations cluster-wide.

    This is a fail-safe direction. CDG will reference lockdown state to gate
    proxmox, fabrication, and robotics operations until exit_lockdown is called.
    """
    store = _store()
    result = store.enter_lockdown(reason)
    return json.dumps(result)


@server.tool()
def exit_lockdown(approval_token: str) -> str:
    """Exit lockdown mode. Requires a valid LOCKDOWN_APPROVAL_TOKEN.

    This tool is gated by CDG (require_approval) to prevent Tau from exiting
    lockdown without explicit human authorization.
    """
    store = _store()
    expected_token = os.getenv("LOCKDOWN_APPROVAL_TOKEN", "")

    if not expected_token:
        raise RuntimeError("LOCKDOWN_APPROVAL_TOKEN not configured")

    # Constant-time comparison (Phase 11 audit): a plain `!=` leaks how many leading characters
    # matched through response-time differences. Low severity on a LAN-trust deployment where
    # this call is already CDG-gated behind human approval, but a one-line fix for a real
    # security-relevant token check.
    if not hmac.compare_digest(approval_token, expected_token):
        raise ValueError("Invalid approval token")

    result = store.exit_lockdown()
    return json.dumps(result)


@server.tool()
def get_intrusion_status() -> str:
    """Is the house secure right now? Is a lockdown active? Any intrusions?

    Answers "are we in lockdown", "is there a lockdown active", "is everything secure", "have
    there been any break-ins" - returns the current lockdown state and incident count.

    The tool NAME says "intrusion" but the question people actually ask says "lockdown", and in
    the 2026-07-22 eval "Is there a security lockdown active right now?" produced no call at all.
    The word the user uses now leads the description."""
    store = _store()
    status = store.get_intrusion_status()
    return json.dumps(status)


@server.tool()
def log_incident(incident_type: str, description: str) -> str:
    """Log a security incident. Does not enter lockdown automatically;
    that decision is left to Tau after analyzing the incident.
    """
    store = _store()
    result = store.log_incident(incident_type, description)
    return json.dumps(result)


if __name__ == "__main__":
    server.run(transport=os.getenv("MCP_TRANSPORT", "stdio"))
