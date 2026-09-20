"""Phase 13.5 - per-speaker memory: tau-core stamps the voice-identified speaker into the
`owner` argument of the memory tools SERVER-SIDE, overriding anything the model supplies. Owner
is an identity claim (who a memory belongs to / who may recall it), so the model must never get
to assert it - otherwise one turn could write into, or read out of, another person's memories.

These tests drive the wrapper `_make_wrapped_tool` directly with a recording host, so they pin
the injection without needing a live Ollama or MCP subprocess.
"""

from mcp.types import CallToolResult, TextContent
from mcp.types import Tool as MCPTool

from tau_core.cdg.guard import CoreDirectiveGuard
from tau_core.cdg.rules import RuleSet
from tau_core.host import ToolCallOutcome, ToolCallStatus
from tau_core.llm.toolset import (
    OWNER_SCOPED_TOOLS,
    PASSTHROUGH_BLOCKED_MEMORY_WRITES,
    PASSTHROUGH_SHARED_ONLY_MEMORY_READS,
    _make_wrapped_tool,
)
from tau_core.routing.policy import ToolRoutingPolicy


class RecordingHost:
    """Every tool here falls to the default-allow ruleset - these tests are about owner
    stamping, not the CDG-tier router bypass (see test_router_bypass.py), so cdg/routing are
    just the plain defaults `_make_wrapped_tool` needs to exist on the host."""

    def __init__(self):
        self.cdg = CoreDirectiveGuard(RuleSet())
        self.routing = ToolRoutingPolicy()
        self.calls: list[tuple[str, str, dict]] = []

    async def call_tool(self, server, tool, arguments, *, confidence=1.0, **kwargs):
        self.calls.append((server, tool, dict(arguments)))
        return ToolCallOutcome(
            status=ToolCallStatus.EXECUTED,
            reason="ok",
            result=CallToolResult(content=[TextContent(type="text", text="done")]),
        )


class StubChecker:
    async def score(self, *args, **kwargs):
        return 1.0


def _tool(name: str) -> MCPTool:
    return MCPTool(
        name=name,
        description="x",
        inputSchema={"type": "object", "properties": {"owner": {"type": "string"}}},
    )


def _wrap(host, server, tool_name, speaker):
    return _make_wrapped_tool(host, StubChecker(), "text", server, _tool(tool_name), [], speaker=speaker)


async def test_draft_memory_owner_is_stamped_from_speaker_overriding_model():
    """The model supplies owner="attacker"; tau-core overwrites it with the real speaker."""
    host = RecordingHost()
    wrapped = _wrap(host, "memory-mcp-server", "draft_memory", speaker="zion")

    await wrapped.function(title="t", content="c", owner="attacker")

    assert host.calls[0][2]["owner"] == "zion"


async def test_search_memory_owner_is_stamped_from_speaker():
    host = RecordingHost()
    wrapped = _wrap(host, "memory-mcp-server", "search_memory", speaker="amara")

    await wrapped.function(query="codeword", owner="zion")

    assert host.calls[0][2]["owner"] == "amara"


async def test_unknown_speaker_stamps_empty_owner():
    host = RecordingHost()
    wrapped = _wrap(host, "memory-mcp-server", "draft_memory", speaker="")

    await wrapped.function(title="t", content="c")

    assert host.calls[0][2]["owner"] == ""


async def test_non_memory_tool_gets_no_owner_injected():
    """A device/tool call must not sprout an owner argument it never had - injection is scoped
    to exactly the memory tools that take one."""
    host = RecordingHost()
    wrapped = _wrap(host, "home-assistant-mcp-server", "call_service", speaker="zion")

    await wrapped.function(entity_id="light.kitchen")

    assert "owner" not in host.calls[0][2]


def test_owner_scoped_set_is_exactly_the_memory_write_and_search_tools():
    assert OWNER_SCOPED_TOOLS == frozenset(
        {
            ("memory-mcp-server", "draft_memory"),
            ("memory-mcp-server", "store_memory"),
            ("memory-mcp-server", "search_memory"),
        }
    )


def test_passthrough_policy_partitions_every_owner_scoped_tool():
    """Phase 15 #1: each owner-scoped tool must have exactly one passthrough policy - blocked
    (writes) or shared-only (search). A partition, so adding a fourth owner-scoped tool without
    classifying it here fails loudly rather than defaulting to the old verbatim-forward leak."""
    assert PASSTHROUGH_BLOCKED_MEMORY_WRITES.isdisjoint(PASSTHROUGH_SHARED_ONLY_MEMORY_READS)
    assert (
        PASSTHROUGH_BLOCKED_MEMORY_WRITES | PASSTHROUGH_SHARED_ONLY_MEMORY_READS
    ) == OWNER_SCOPED_TOOLS
