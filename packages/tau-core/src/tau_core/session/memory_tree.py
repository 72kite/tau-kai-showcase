from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tau_core.host import TauCoreHost

logger = logging.getLogger(__name__)

MEMORY_SERVER = "memory-mcp-server"

# Prefix marking a recalled memory that Tau wrote about someone and nobody has checked
# (Phase 8.B). Also what the UI's recall line keys off, so an unverified memory is visible as
# unverified on the glass too - not just inside the prompt.
UNVERIFIED_PREFIX = "[unverified draft]"


def _format_node(node: dict) -> str:
    """Renders one recalled node for the prompt, marking drafts.

    The marker is inline in the snippet rather than a separate structured field because this
    string is what lands in the prompt: a status field the prompt renderer might or might not
    use is a label that can go missing, and the one thing this tier cannot afford is Tau quoting
    its own unchecked guess back as established fact.
    """
    title = node.get("title", "")
    content = node.get("content", "")
    prefix = f"{UNVERIFIED_PREFIX} " if node.get("status") == "draft" else ""
    return f"{prefix}{title}: {content}"


class MemoryTreeBackend:
    """MemoryBackend backed by memory-mcp-server's Memory Tree (Phase 2.8), called through
    `host.call_tool` like any other caller - recall shows up in the CDG pipeline and audit log,
    not as a privileged side channel.

    `recall()` uses the read-only `search_memory` tool. `remember()` remains a no-op here:
    Phase 8.B did NOT turn it into an auto-write. Learning is something the model chooses to do
    by calling `draft_memory` when it notices something worth keeping, not something this backend
    does behind its back on every turn - a mechanical "write the last exchange to memory" hook
    produces a vault full of transcript sludge, and the point of the Memory Tree is that a human
    can read it. See project-tau-plan.md §8.8.

    **Draft memories are labelled on the way in, always.** A draft is something Tau inferred and
    nobody checked; presenting it to the model identically to a human-approved memory would make
    the tier meaningless at exactly the moment it matters - when the model is deciding what it
    believes about someone. Recall is the only path Memory Tree content reaches the model's
    prompt, so this is the right chokepoint for that labelling.

    Every failure path degrades to "no memories recalled" rather than an exception: long-term
    memory being down must never take the chat turn down with it (same posture as
    `RouterConfidenceChecker`'s 0.0 fallback).
    """

    def __init__(self, host: "TauCoreHost"):
        self._host = host

    async def remember(self, session_id: str, text: str) -> None:
        return None

    async def recall(
        self, session_id: str, query: str, limit: int = 5, owner: str = ""
    ) -> list[str]:
        if MEMORY_SERVER not in self._host.mcp.connected_servers():
            return []
        try:
            outcome = await self._host.call_tool(
                MEMORY_SERVER, "search_memory", {"query": query, "limit": limit, "owner": owner}
            )
        except Exception:  # noqa: BLE001 - degraded recall, never a dead turn
            logger.warning("Memory Tree recall failed; continuing without long-term memory", exc_info=True)
            return []
        if outcome.result is None:
            return []

        snippets: list[str] = []
        for item in outcome.result.content:
            payload = getattr(item, "text", None)
            if not payload:
                continue
            # FastMCP serializes each returned node as one JSON text block; fall back to the
            # raw text if a future server version changes that shape.
            try:
                node = json.loads(payload)
                snippets.append(_format_node(node))
            except (ValueError, TypeError, KeyError):
                snippets.append(payload)
        return snippets
