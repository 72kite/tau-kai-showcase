"""Trivial stdio MCP server used to validate Tau Core's client plumbing end to end
(Phase 1 exit criteria: connect, list tools, call a tool, get a logged result).

Run directly for manual testing:
    python examples/echo_mcp_server.py
"""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("echo")


@mcp.tool()
def echo(text: str) -> str:
    """Return the given text unchanged."""
    return text


@mcp.resource(uri="echo://status")
def status() -> str:
    """Trivial resource used to validate MCPClientManager.read_resource end to end."""
    return "ok"


if __name__ == "__main__":
    mcp.run()
