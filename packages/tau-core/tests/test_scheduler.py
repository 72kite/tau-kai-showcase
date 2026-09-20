"""ProposingScheduler (project-tau-plan.md §10.3 item 10 / §5.1's CVE-polling job) - a background
job that calls a read-only MCP tool on an interval and, only when its own `propose` function says
so, drafts a memory a human can review later. Uses a fake host (duck-typed: `.mcp.connected_
servers()` + async `.call_tool(...)`) rather than a real TauCoreHost/MCP transport, since these
tests are about the scheduler's own loop/skip/error-handling logic, not CDG/transport integration
(covered elsewhere, e.g. test_subagent.py, test_host_integration.py).
"""

from __future__ import annotations

import asyncio

import pytest
from mcp.types import CallToolResult, TextContent

from tau_core.host import ToolCallOutcome, ToolCallStatus
from tau_core.scheduler import ProposingScheduler, ScheduledJob, ScheduledProposal, cve_job


class _FakeMcp:
    def __init__(self, connected: list[str]):
        self._connected = connected

    def connected_servers(self) -> list[str]:
        return self._connected


class _FakeHost:
    """Records every call_tool invocation; returns canned outcomes keyed by (server, tool),
    falling back to a default EXECUTED-with-no-content outcome for anything unqueued (draft_memory
    calls, in most tests, since only the polled tool's response is usually under test)."""

    def __init__(self, connected: list[str] | None = None):
        self.mcp = _FakeMcp(connected if connected is not None else ["proxmox-mcp-server"])
        self.calls: list[tuple[str, str, dict]] = []
        self._outcomes: dict[tuple[str, str], ToolCallOutcome | Exception] = {}

    def queue_result(self, server: str, tool: str, texts: list[str]) -> None:
        content = [TextContent(type="text", text=t) for t in texts]
        self._outcomes[(server, tool)] = ToolCallOutcome(
            status=ToolCallStatus.EXECUTED,
            reason="ok",
            result=CallToolResult(content=content),
        )

    def queue_outcome(self, server: str, tool: str, outcome: ToolCallOutcome) -> None:
        self._outcomes[(server, tool)] = outcome

    def queue_raises(self, server: str, tool: str, exc: Exception) -> None:
        self._outcomes[(server, tool)] = exc

    async def call_tool(self, server, tool, arguments, *, requested_by="tau-core", **_kw):
        self.calls.append((server, tool, arguments))
        outcome = self._outcomes.get((server, tool))
        if isinstance(outcome, Exception):
            raise outcome
        if outcome is not None:
            return outcome
        return ToolCallOutcome(status=ToolCallStatus.EXECUTED, reason="ok", result=CallToolResult(content=[]))


def _always_propose(_snippets) -> ScheduledProposal:
    return ScheduledProposal(title="Something found", content="details", tags=["test"])


def _never_propose(_snippets) -> None:
    return None


async def test_run_once_skips_when_server_not_connected():
    host = _FakeHost(connected=[])
    job = ScheduledJob("j", 60, "proxmox-mcp-server", "check_cve_advisories", {}, _always_propose)
    scheduler = ProposingScheduler(host, [job])

    result = await scheduler.run_once(job)

    assert result is None
    assert host.calls == []


async def test_run_once_returns_none_when_tool_did_not_execute():
    host = _FakeHost()
    host.queue_outcome(
        "proxmox-mcp-server", "check_cve_advisories",
        ToolCallOutcome(status=ToolCallStatus.PENDING_APPROVAL, reason="gated", approval_request_id="x"),
    )
    job = ScheduledJob("j", 60, "proxmox-mcp-server", "check_cve_advisories", {}, _always_propose)
    scheduler = ProposingScheduler(host, [job])

    result = await scheduler.run_once(job)

    assert result is None
    # Only the polled tool was called - a job that didn't execute must never reach draft_memory.
    assert host.calls == [("proxmox-mcp-server", "check_cve_advisories", {})]


async def test_run_once_drafts_a_memory_when_propose_returns_something():
    host = _FakeHost()
    host.queue_result("proxmox-mcp-server", "check_cve_advisories", ["[]"])
    job = ScheduledJob("j", 60, "proxmox-mcp-server", "check_cve_advisories", {}, _always_propose)
    scheduler = ProposingScheduler(host, [job])

    result = await scheduler.run_once(job)

    assert result == ScheduledProposal(title="Something found", content="details", tags=["test"])
    assert host.calls == [
        ("proxmox-mcp-server", "check_cve_advisories", {}),
        ("memory-mcp-server", "draft_memory", {"title": "Something found", "content": "details", "tags": "test"}),
    ]


async def test_run_once_drafts_nothing_when_propose_returns_none():
    host = _FakeHost()
    host.queue_result("proxmox-mcp-server", "check_cve_advisories", ["[]"])
    job = ScheduledJob("j", 60, "proxmox-mcp-server", "check_cve_advisories", {}, _never_propose)
    scheduler = ProposingScheduler(host, [job])

    result = await scheduler.run_once(job)

    assert result is None
    assert host.calls == [("proxmox-mcp-server", "check_cve_advisories", {})]


async def test_start_runs_the_job_repeatedly_and_stop_ends_it():
    host = _FakeHost()
    host.queue_result("proxmox-mcp-server", "check_cve_advisories", ["[]"])
    job = ScheduledJob("j", 0.01, "proxmox-mcp-server", "check_cve_advisories", {}, _never_propose)
    scheduler = ProposingScheduler(host, [job])

    scheduler.start()
    await asyncio.sleep(0.05)
    await scheduler.stop()
    count_after_stop = len(host.calls)
    await asyncio.sleep(0.05)

    assert count_after_stop >= 2
    assert len(host.calls) == count_after_stop  # nothing ran after stop()


async def test_start_is_idempotent():
    host = _FakeHost()
    host.queue_result("proxmox-mcp-server", "check_cve_advisories", ["[]"])
    job = ScheduledJob("j", 10, "proxmox-mcp-server", "check_cve_advisories", {}, _never_propose)
    scheduler = ProposingScheduler(host, [job])

    scheduler.start()
    tasks_first = list(scheduler._tasks)
    scheduler.start()

    assert scheduler._tasks == tasks_first
    await scheduler.stop()


async def test_run_forever_survives_one_failing_iteration():
    """A job whose call_tool raises must not kill the loop - it logs and tries again next
    interval, same reasoning as every other durability path in this codebase."""
    host = _FakeHost()
    host.queue_raises("proxmox-mcp-server", "check_cve_advisories", RuntimeError("transient"))
    job = ScheduledJob("j", 0.01, "proxmox-mcp-server", "check_cve_advisories", {}, _never_propose)
    scheduler = ProposingScheduler(host, [job])

    scheduler.start()
    await asyncio.sleep(0.05)
    await scheduler.stop()

    assert len(host.calls) >= 2  # kept retrying despite every call raising


def test_cve_job_ignores_info_only_advisories():
    job = cve_job()
    # The real check_cve_advisories stub's exact shape: one text block containing a JSON array.
    snippets = [[{"severity": "info", "message": "not yet implemented"}]]

    assert job.propose(snippets) is None


def test_cve_job_proposes_for_real_advisories():
    job = cve_job()
    snippets = [[{"severity": "critical", "message": "CVE-2099-0001 affects pve-manager"}]]

    proposal = job.propose(snippets)

    assert proposal is not None
    assert "1" in proposal.title
    assert "CVE-2099-0001" in proposal.content
    assert "critical" in proposal.content
    assert proposal.tags == ["security", "cve", "proxmox"]


def test_cve_job_default_interval_is_daily():
    job = cve_job()
    assert job.interval_seconds == 24 * 60 * 60
