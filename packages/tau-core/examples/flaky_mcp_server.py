"""Stdio MCP server that can be made to die or wedge on command - the fixture for Phase 7
Tier 0's resilience tests (tests/test_mcp_resilience.py).

`echo` and `dangerous` are both well-behaved, so nothing in the suite could reproduce the
failure that actually bit this deployment on 2026-07-15: recreating one domain server's
container left the bridge answering 502 for it permanently, until tau-core was restarted by
hand. That needs a server that stops existing mid-session, which is what `crash` is for.

Like echo/dangerous, this is a TEST FIXTURE and belongs in examples/ - never in the production
config/servers.yaml (see test_production_registry_excludes_test_fixture_servers).

Run directly for manual testing:
    python examples/flaky_mcp_server.py
"""

import os
import time

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("flaky")


@mcp.tool()
def ping(text: str = "pong") -> str:
    """Return the given text unchanged - proof the server is alive and serving."""
    return text


@mcp.tool()
def crash() -> str:
    """Kill this server process immediately, without replying.

    Simulates the real event: the container going away underneath a live session. os._exit
    skips interpreter cleanup deliberately - a graceful shutdown would close the stdio
    transport politely, which is NOT what a `docker compose up -d --force-recreate` looks like
    to the client.
    """
    os._exit(1)


@mcp.tool()
def hang(seconds: float = 60.0) -> str:
    """Block the server's event loop, so the client sees a request that never gets a reply.

    Deliberately a blocking sleep rather than an async one: this simulates a wedged server, and
    a wedged server does not politely keep serving other requests.
    """
    time.sleep(seconds)
    return "finally done"


if __name__ == "__main__":
    mcp.run()
