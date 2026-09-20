"""Proposing scheduler (project-tau-plan.md §10.3 item 10 / §5.1's CVE-polling job).

The shape was decided long before this was built: read-only tools run freely, and
memory-mcp-server's `draft_memory` is explicitly "no approval needed - drafts are clearly
labelled as unchecked" (see its own docstring). A background job calling a read-only tool and
handing its findings to `draft_memory` never touches the CDG's gated path, so nothing here needed
new governance - only something to actually run without a human turn starting it. That's the
"predict needs" half §8.D never built: until this, nothing in Tau ran on its own.

Deliberately generic, not CVE-specific: a `ScheduledJob` names one MCP tool to poll on an
interval and a pure function deciding whether/what to draft from its result. The concrete CVE job
(`proxmox-mcp-server.check_cve_advisories` -> a draft memory when it reports something worth a
human's attention, closing §5.1's "scheduled job polls CVE feeds... raises a resource Tau can
proactively surface") is wired up as ONE instance of this in `tau_core.scheduler.cve_job`, not
baked into the scheduler itself - the shape obviously wants more jobs later (HA entity health,
disk-space warnings, ...) and none of those should need scheduler changes, only a new job.

Honest limit: `check_cve_advisories` itself is still the stub project-tau-plan.md §5.1 always
said it was ("real implementation would poll NVD") - this file makes whatever it returns actually
reach a human via a draft, it does not implement real NVD polling.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

from tau_core.host import ToolCallStatus

if TYPE_CHECKING:
    from tau_core.host import TauCoreHost

logger = logging.getLogger(__name__)

MEMORY_SERVER = "memory-mcp-server"


@dataclass(frozen=True)
class ScheduledProposal:
    """What a job wants drafted into the Memory Tree - the human-reviewable review queue
    `draft_memory` already feeds (see memory_mcp_server.server.draft_memory / list_drafts)."""

    title: str
    content: str
    tags: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ScheduledJob:
    name: str
    interval_seconds: float
    server: str
    tool: str
    arguments: dict
    # Given the polled tool's result content as parsed-JSON-or-raw-text snippets (one per content
    # block, mirroring how tau_core.session.memory_tree.MemoryTreeBackend.recall reads a tool
    # result), decide whether it's worth a human's attention. None = nothing to propose this run -
    # the common case, since e.g. "no new CVEs" should not draft a memory every interval.
    propose: Callable[[list[object]], ScheduledProposal | None]


def _result_snippets(content) -> list[object]:
    """Each content block's text, JSON-parsed where possible - same fallback rule
    MemoryTreeBackend.recall already uses: fall back to the raw text if a server's result isn't
    JSON, never drop a block just because it didn't parse."""
    snippets: list[object] = []
    for item in content:
        payload = getattr(item, "text", None)
        if not payload:
            continue
        try:
            snippets.append(json.loads(payload))
        except (ValueError, TypeError):
            snippets.append(payload)
    return snippets


class ProposingScheduler:
    """Runs each `ScheduledJob` on its own interval, forever, until `stop()`. Every job's tool
    call and any resulting draft both go through `host.call_tool` - the same CDG/routing/audit
    pipeline any other caller uses, `requested_by="scheduler"` naming the source in the audit
    trail rather than pretending a human or the model asked for it.
    """

    def __init__(self, host: "TauCoreHost", jobs: list[ScheduledJob]):
        self._host = host
        self._jobs = jobs
        self._tasks: list[asyncio.Task] = []

    def start(self) -> None:
        if self._tasks:
            return  # already running - start() is idempotent, not a way to double up jobs
        self._tasks = [asyncio.create_task(self._run_forever(job)) for job in self._jobs]

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []

    async def _run_forever(self, job: ScheduledJob) -> None:
        while True:
            try:
                await self.run_once(job)
            except asyncio.CancelledError:
                raise
            except Exception:
                # One job's failure (a domain server down, a transient error) must not take
                # down every other scheduled job, or the loop itself - there is no human turn
                # here to report the failure to; it's logged and tried again next interval.
                logger.exception("scheduled job %r failed", job.name)
            try:
                await asyncio.sleep(job.interval_seconds)
            except asyncio.CancelledError:
                raise

    async def run_once(self, job: ScheduledJob) -> ScheduledProposal | None:
        """Runs `job` exactly once, immediately - used by `_run_forever`'s loop, and directly by
        tests/an admin "check now" action, so the interval wait is never in the way of proving
        a job actually works."""
        if job.server not in self._host.mcp.connected_servers():
            logger.info("scheduled job %r skipped: %s not connected", job.name, job.server)
            return None
        outcome = await self._host.call_tool(
            job.server, job.tool, job.arguments, requested_by="scheduler"
        )
        if outcome.status is not ToolCallStatus.EXECUTED or outcome.result is None:
            logger.info(
                "scheduled job %r did not execute: status=%s reason=%s",
                job.name, outcome.status, outcome.reason,
            )
            return None

        proposal = job.propose(_result_snippets(outcome.result.content))
        if proposal is None:
            return None

        await self._host.call_tool(
            MEMORY_SERVER,
            "draft_memory",
            {
                "title": proposal.title,
                "content": proposal.content,
                "tags": ",".join(proposal.tags),
            },
            requested_by="scheduler",
        )
        return proposal


def cve_job(interval_seconds: float = 24 * 60 * 60) -> ScheduledJob:
    """The concrete job §5.1 named: poll proxmox-mcp-server's CVE advisories once a day (its
    own default) and draft a memory when there's something other than the stub's placeholder
    "not yet implemented" info message - see this module's own docstring for that honesty note.
    """

    def propose(snippets: list[object]) -> ScheduledProposal | None:
        advisories = [
            item
            for entry in snippets
            if isinstance(entry, list)
            for item in entry
            if isinstance(item, dict) and item.get("severity") != "info"
        ]
        if not advisories:
            return None
        lines = [f"- **{a.get('severity', 'unknown')}**: {a.get('message', a)}" for a in advisories]
        return ScheduledProposal(
            title=f"CVE advisories ({len(advisories)})",
            content="Proxmox flagged the following on its scheduled check:\n\n" + "\n".join(lines),
            tags=["security", "cve", "proxmox"],
        )

    return ScheduledJob(
        name="proxmox-cve-advisories",
        interval_seconds=interval_seconds,
        server="proxmox-mcp-server",
        tool="check_cve_advisories",
        arguments={},
        propose=propose,
    )
