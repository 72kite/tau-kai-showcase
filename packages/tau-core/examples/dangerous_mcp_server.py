"""A second stdio MCP server exposing a tool that the CDG must block by default, used in
integration tests to prove the guard actually sits in front of real MCP tool calls (not just
the in-memory unit tests in tests/test_cdg.py).
"""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("dangerous")


@mcp.tool()
def shutdown_host(vmid: int) -> str:
    """Power off a VM. Gated by the CDG 'no-self-destruct' rule - requires human approval."""
    return f"vm {vmid} powered off"


if __name__ == "__main__":
    mcp.run()
