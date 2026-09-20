"""Phase 2.8 default recall: the session's MemoryTreeBackend surfaces Memory Tree context at
the start of every chat turn, through host.call_tool (CDG/audit in the loop), degrading to
no-recall on any failure rather than killing the turn.

Unit tests fake the host; the integration test spawns the real memory-mcp-server over stdio
(skipped if that package isn't installed in this venv - tau-core's own suite stays standalone).
"""

import json
import os
import sys
from pathlib import Path

import pytest
from mcp.types import CallToolResult, TextContent
from pydantic_ai import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from tau_core.config import TauCoreSettings
from tau_core.host import TauCoreHost, ToolCallOutcome, ToolCallStatus
from tau_core.llm.agent import TauAssistant
from tau_core.mcp_client import MCPClientManager, ServerConfig, ServerRegistry
from tau_core.session.memory_tree import MEMORY_SERVER, MemoryTreeBackend

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


class FakeHost:
    def __init__(self, connected: list[str], outcome: ToolCallOutcome | None = None, error: Exception | None = None):
        self.mcp = self
        self._connected = connected
        self._outcome = outcome
        self._error = error
        self.calls: list[tuple[str, str, dict]] = []

    def connected_servers(self) -> list[str]:
        return self._connected

    async def call_tool(self, server, tool, arguments, **kwargs):
        self.calls.append((server, tool, arguments))
        if self._error:
            raise self._error
        return self._outcome


def _executed(nodes: list[dict]) -> ToolCallOutcome:
    content = [TextContent(type="text", text=json.dumps(node)) for node in nodes]
    return ToolCallOutcome(
        status=ToolCallStatus.EXECUTED, reason="ok", result=CallToolResult(content=content)
    )


async def test_recall_formats_nodes_from_search_memory():
    host = FakeHost(
        connected=[MEMORY_SERVER],
        outcome=_executed(
            [
                {"title": "Kitchen lighting", "content": "Dim to 40% after 22:00.", "score": 1.5},
                {"title": "Printer", "content": "PLA at 205C.", "score": 1.0},
            ]
        ),
    )

    recalled = await MemoryTreeBackend(host).recall("session", "kitchen lights", limit=5)

    assert recalled == ["Kitchen lighting: Dim to 40% after 22:00.", "Printer: PLA at 205C."]
    assert host.calls == [
        (MEMORY_SERVER, "search_memory", {"query": "kitchen lights", "limit": 5, "owner": ""})
    ]


async def test_recall_passes_speaker_owner_to_search():
    """Phase 13.5: the identified speaker scopes recall - search_memory is called with owner set,
    so one person's memories don't surface for another."""
    host = FakeHost(connected=[MEMORY_SERVER], outcome=_executed([]))

    await MemoryTreeBackend(host).recall("session", "codeword", limit=5, owner="zion")

    assert host.calls == [
        (MEMORY_SERVER, "search_memory", {"query": "codeword", "limit": 5, "owner": "zion"})
    ]


async def test_recall_returns_empty_when_memory_server_not_connected():
    host = FakeHost(connected=["echo"])

    assert await MemoryTreeBackend(host).recall("session", "anything") == []
    assert host.calls == []


async def test_recall_degrades_to_empty_on_tool_failure():
    host = FakeHost(connected=[MEMORY_SERVER], error=RuntimeError("memory server exploded"))

    assert await MemoryTreeBackend(host).recall("session", "anything") == []


async def test_remember_is_a_noop_by_design():
    """store_memory requires human approval; the backend must never auto-write."""
    host = FakeHost(connected=[MEMORY_SERVER])

    await MemoryTreeBackend(host).remember("session", "should not be stored")

    assert host.calls == []


async def test_chat_turn_recalls_memory_tree_context(tmp_path):
    """End to end against the real memory-mcp-server over stdio: a node seeded in the Memory
    Tree shows up in the prompt the main model sees, via the host's default session backend
    (nothing test-injected on the recall path)."""
    memory_tree = pytest.importorskip(
        "memory_mcp_server.memory_tree", reason="memory-mcp-server not installed in this venv"
    )

    store = memory_tree.MemoryTreeStore(tmp_path / "tree.sqlite", tmp_path / "vault")
    store.create_node("Kitchen lighting decision", "Dim the kitchen lights to 40% after 22:00.")

    registry = ServerRegistry(
        servers=[
            ServerConfig(
                name=MEMORY_SERVER,
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

    seen_prompts: list[str] = []

    def main_fn(messages, info):
        seen_prompts.append(str(messages))
        return ModelResponse(parts=[TextPart("noted")])

    def router_fn(messages, info):
        return ModelResponse(
            parts=[ToolCallPart(tool_name="final_result", args={"confidence": 0.9, "reasoning": "stub"})]
        )

    settings = TauCoreSettings(cdg_rules_path=CONFIG_DIR / "cdg_rules.yaml")
    async with MCPClientManager(registry) as manager:
        await manager.connect_all()
        host = TauCoreHost(manager, settings=settings)
        assistant = TauAssistant(
            host, main_model=FunctionModel(main_fn), router_model=FunctionModel(router_fn)
        )

        turn = await assistant.chat("what did we decide about the kitchen lights?")

    assert turn.reply == "noted"
    assert "Dim the kitchen lights to 40%" in seen_prompts[0]
    assert "long-term memories" in seen_prompts[0]
