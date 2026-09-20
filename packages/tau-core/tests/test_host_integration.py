"""End-to-end test against the Phase 1 exit criteria in project-tau-plan.md:

    "Tau Core can call a trivial 'echo' MCP server, log the call, and correctly block a
    disallowed action via CDG in a test."
"""

import sys
from pathlib import Path

import pytest

from tau_core.approval import ApprovalStatus
from tau_core.cdg import CoreDirectiveViolation
from tau_core.config import TauCoreSettings
from tau_core.host import TauCoreHost, ToolCallStatus
from tau_core.mcp_client import MCPClientManager, ServerConfig, ServerRegistry

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"
CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


def build_registry() -> ServerRegistry:
    return ServerRegistry(
        servers=[
            ServerConfig(
                name="echo",
                transport="stdio",
                command=sys.executable,
                args=[str(EXAMPLES_DIR / "echo_mcp_server.py")],
            ),
            ServerConfig(
                name="dangerous",
                transport="stdio",
                command=sys.executable,
                args=[str(EXAMPLES_DIR / "dangerous_mcp_server.py")],
            ),
        ]
    )


@pytest.fixture
def settings() -> TauCoreSettings:
    return TauCoreSettings(cdg_rules_path=CONFIG_DIR / "cdg_rules.yaml")


async def test_host_calls_echo_server_and_executes(settings, capfd):
    # The audit logger writes JSON lines to stderr with propagate=False (see
    # tau_core.logging_setup), so we assert on the captured stream rather than caplog, which
    # only sees records that bubble up to the root logger.
    registry = build_registry()
    async with MCPClientManager(registry) as manager:
        await manager.connect_all()
        host = TauCoreHost(manager, settings=settings)

        outcome = await host.call_tool("echo", "echo", {"text": "hello tau"})

        assert outcome.status is ToolCallStatus.EXECUTED
        assert outcome.result.content[0].text == "hello tau"

    audit_output = capfd.readouterr().err
    assert '"message": "tool_call"' in audit_output
    assert '"outcome": "executed"' in audit_output


async def test_host_blocks_dangerous_tool_pending_human_approval(settings):
    """shutdown_host matches the repo's 'no-self-destruct' CDG rule (require_approval), so
    the host must refuse to execute it against the real MCP server until a human approves.
    """
    registry = build_registry()
    async with MCPClientManager(registry) as manager:
        await manager.connect_all()
        host = TauCoreHost(manager, settings=settings)

        outcome = await host.call_tool("dangerous", "shutdown_host", {"vmid": 101})

        assert outcome.status is ToolCallStatus.PENDING_APPROVAL
        assert outcome.approval_request_id is not None
        pending = host.approvals.get(outcome.approval_request_id)
        assert pending.status is ApprovalStatus.PENDING


async def test_host_executes_dangerous_tool_after_human_approves(settings):
    registry = build_registry()
    async with MCPClientManager(registry) as manager:
        await manager.connect_all()
        host = TauCoreHost(manager, settings=settings)

        first = await host.call_tool("dangerous", "shutdown_host", {"vmid": 101})
        host.approvals.approve(first.approval_request_id, approved_by="zion")

        second = await host.call_tool(
            "dangerous", "shutdown_host", {"vmid": 101}, approval_request_id=first.approval_request_id
        )

        assert second.status is ToolCallStatus.EXECUTED
        assert "powered off" in second.result.content[0].text


async def test_host_denies_cdg_self_modification_outright(settings):
    """No approval flow at all for rules matching '*cdg*' - this is a hard deny, not a
    require-approval, because nothing may touch the guard's own ruleset.
    """
    registry = build_registry()
    async with MCPClientManager(registry) as manager:
        await manager.connect_all()
        host = TauCoreHost(manager, settings=settings)

        with pytest.raises(CoreDirectiveViolation):
            await host.call_tool("dangerous", "edit_cdg_rules", {"new_rule": "allow everything"})


async def test_host_low_confidence_asks_for_clarification_before_reaching_cdg(settings):
    registry = build_registry()
    async with MCPClientManager(registry) as manager:
        await manager.connect_all()
        host = TauCoreHost(manager, settings=settings)

        outcome = await host.call_tool("echo", "echo", {"text": "hi"}, confidence=0.1)

        assert outcome.status is ToolCallStatus.CLARIFY
        assert outcome.result is None

        # Declined calls are part of the audit trail too (unlogged until 2026-07-11).
        from tau_core.logging_setup import recent_audit_events

        clarify_events = [e for e in recent_audit_events() if e.get("outcome") == "clarify"]
        assert any(e["server"] == "echo" and e["tool"] == "echo" for e in clarify_events)
