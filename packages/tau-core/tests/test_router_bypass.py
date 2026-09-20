"""CDG-tiered router bypass: RouterConfidenceChecker.score fires 3 concurrent local-model calls
per proposed tool call (median-of-3, see router.py), but the CDG - not the router - is the actual
safety layer, and it runs regardless of what the router says (ToolRoutingPolicy's own docstring).
So scoring a call the CDG ruleset already trusts to run unattended (Effect.ALLOW, not pinned to
always-confirm) buys nothing: ToolRoutingPolicy.decide(confidence=None) already proceeds and lets
the CDG gate it, identical to what a high-confidence score would have produced. Skipping the
router entirely for that tier removes pure latency with no change in what gets allowed.

These tests drive `_make_wrapped_tool` directly against a recording host, same style as
test_owner_scoping.py, so they pin the bypass without needing a live Ollama or MCP subprocess.
"""

from mcp.types import CallToolResult, TextContent
from mcp.types import Tool as MCPTool

from tau_core.cdg.guard import CoreDirectiveGuard
from tau_core.cdg.rules import Effect, Rule, RuleSet
from tau_core.host import ToolCallOutcome, ToolCallStatus
from tau_core.llm.toolset import _make_wrapped_tool
from tau_core.routing.policy import ToolRoutingPolicy


class RecordingHost:
    def __init__(self, ruleset: RuleSet, routing: ToolRoutingPolicy | None = None):
        self.cdg = CoreDirectiveGuard(ruleset)
        self.routing = routing or ToolRoutingPolicy()
        self.calls: list[tuple[str, str, dict, float | None]] = []

    async def call_tool(self, server, tool, arguments, *, confidence=1.0, **kwargs):
        self.calls.append((server, tool, dict(arguments), confidence))
        return ToolCallOutcome(
            status=ToolCallStatus.EXECUTED,
            reason="ok",
            result=CallToolResult(content=[TextContent(type="text", text="done")]),
        )


class CountingChecker:
    def __init__(self):
        self.calls = 0

    async def score(self, *args, **kwargs):
        self.calls += 1
        return 0.9


def _tool(name: str) -> MCPTool:
    return MCPTool(name=name, description="x", inputSchema={"type": "object", "properties": {}})


def _ruleset(**rule_kwargs) -> RuleSet:
    rules = [Rule(id="r", **rule_kwargs)] if rule_kwargs else []
    return RuleSet(default_effect=Effect.ALLOW, rules=rules)


async def test_router_skipped_for_cdg_allowed_tool():
    host = RecordingHost(_ruleset())  # no rules -> default_effect ALLOW
    checker = CountingChecker()
    wrapped = _make_wrapped_tool(host, checker, "what's the printer status", "some-server", _tool("get_status"), [])

    await wrapped.function()

    assert checker.calls == 0
    assert host.calls[0][3] is None  # confidence passed through as "no opinion" -> CDG still gates


async def test_router_still_scores_require_approval_tool():
    ruleset = _ruleset(server="some-server", tool="dangerous_action", effect=Effect.REQUIRE_APPROVAL, reason="r")
    host = RecordingHost(ruleset)
    checker = CountingChecker()
    wrapped = _make_wrapped_tool(
        host, checker, "do the dangerous thing", "some-server", _tool("dangerous_action"), []
    )

    await wrapped.function()

    assert checker.calls == 1
    assert host.calls[0][3] == 0.9


async def test_router_still_scores_deny_tool():
    ruleset = _ruleset(server="some-server", tool="blocked_action", effect=Effect.DENY, reason="r")
    host = RecordingHost(ruleset)
    checker = CountingChecker()
    wrapped = _make_wrapped_tool(host, checker, "do the blocked thing", "some-server", _tool("blocked_action"), [])

    await wrapped.function()

    assert checker.calls == 1


async def test_always_clarify_tool_is_scored_even_when_cdg_allows_it():
    """An operator-pinned always-confirm tool must not be short-circuited by CDG-allow: the whole
    point of always_clarify_tools is a human instruction the router hint must still see."""
    host = RecordingHost(_ruleset(), routing=ToolRoutingPolicy(always_clarify_tools={"sensitive_read"}))
    checker = CountingChecker()
    wrapped = _make_wrapped_tool(host, checker, "read the sensitive thing", "some-server", _tool("sensitive_read"), [])

    await wrapped.function()

    assert checker.calls == 1
