"""Phase 15 #1: the unauthenticated /api/tools passthrough must not let a caller reach the
per-speaker memory tools with an attacker-chosen `owner`.

Phase 13.5 stamps `owner` from the voice-identified speaker on the *model* path only. The
passthrough forwards arguments verbatim, so left open it let any LAN client:
  - write a memory attributed to anyone (draft_memory/store_memory), or
  - read anyone's private memories by supplying `owner` (search_memory).

Fix under test: the two writes are blocked here (403); search stays reachable for the admin
MemoryPanel but is forced to shared-only recall regardless of any supplied `owner`.

The 403 tests need no backend (the block precedes host.call_tool), so they reuse the echo/
dangerous example registry. The search-forcing test spins up the real memory-mcp-server over
stdio (skipped if that package isn't installed - tau-core's own suite stays standalone), the
same way test_memory_recall.py's end-to-end test does.
"""

import json
import os
import sys
from pathlib import Path

import httpx
import pytest
from httpx import ASGITransport

from tau_core.config import TauCoreSettings
from tau_core.host import TauCoreHost
from tau_core.mcp_client import MCPClientManager, ServerConfig, ServerRegistry
from tau_core.web.server import create_app

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"
CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


@pytest.fixture
def settings() -> TauCoreSettings:
    return TauCoreSettings(cdg_rules_path=CONFIG_DIR / "cdg_rules.yaml")


def _example_registry() -> ServerRegistry:
    return ServerRegistry(
        servers=[
            ServerConfig(
                name="echo", transport="stdio", command=sys.executable,
                args=[str(EXAMPLES_DIR / "echo_mcp_server.py")],
            ),
        ]
    )


@pytest.mark.parametrize("tool", ["draft_memory", "store_memory"])
async def test_memory_writes_are_blocked_from_the_generic_passthrough(settings, tool):
    """A write carries an `owner` (who the memory belongs to). The passthrough has no verified
    speaker to attribute it to, so any client could otherwise forge a memory as anyone - the
    block, not the CDG, is what stops that (the memory server need not even be present)."""
    async with MCPClientManager(_example_registry()) as manager:
        await manager.connect_all()
        host = TauCoreHost(manager, settings=settings)
        app = create_app(settings=settings, host=host)

        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                f"/api/tools/memory-mcp-server/{tool}",
                json={"arguments": {"title": "t", "content": "c", "owner": "victim"}},
            )

    assert resp.status_code == 403


async def test_search_memory_passthrough_ignores_a_caller_supplied_owner(tmp_path, settings):
    """The leak was: POST search_memory {"owner":"zion"} returned zion's private memories. The
    passthrough must force shared-only recall, so a supplied owner buys nothing - the private
    node stays hidden while the shared one still comes back (MemoryPanel keeps working)."""
    memory_tree = pytest.importorskip(
        "memory_mcp_server.memory_tree", reason="memory-mcp-server not installed in this venv"
    )

    store = memory_tree.MemoryTreeStore(tmp_path / "tree.sqlite", tmp_path / "vault")
    store.create_node("Zion private", "the codeword is hunter2", owner="zion")
    store.create_node("House shared", "the codeword is guestnet")  # owner="" (shared)

    registry = ServerRegistry(
        servers=[
            ServerConfig(
                name="memory-mcp-server",
                transport="stdio",
                command=sys.executable,
                args=["-m", "memory_mcp_server.server"],
                env={
                    **os.environ,
                    "MEMORY_TREE_DB_PATH": str(tmp_path / "tree.sqlite"),
                    "MEMORY_VAULT_PATH": str(tmp_path / "vault"),
                    "MEMORY_DB_PATH": str(tmp_path / "chroma"),
                    "MEMORY_PROFILES_PATH": str(tmp_path / "profiles.json"),
                },
            )
        ]
    )

    async with MCPClientManager(registry) as manager:
        await manager.connect_all()
        host = TauCoreHost(manager, settings=settings)
        app = create_app(settings=settings, host=host)

        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/tools/memory-mcp-server/search_memory",
                json={"arguments": {"query": "codeword", "owner": "zion"}},
            )

    assert resp.status_code == 200, resp.text
    contents = [json.loads(text)["content"] for text in resp.json()["results"]]
    assert "the codeword is guestnet" in contents  # shared node still recalled
    assert "the codeword is hunter2" not in contents  # zion's private node NOT leaked
