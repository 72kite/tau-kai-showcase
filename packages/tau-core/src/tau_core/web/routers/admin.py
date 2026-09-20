"""Admin-gated dashboard snapshots and logs: system/people/security status, the draft-memory
review queue, the unified cross-device transcript, and the audit-event feed.

Split out of web/server.py's create_app() in Phase 49.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Header

from tau_core.access import DEFAULT_POLICY
from tau_core.host import ToolCallStatus
from tau_core.logging_setup import recent_audit_events
from tau_core.web.deps import SharedDeps, _device_id, _require_admin, _sanitized_error, _tool_json

logger = logging.getLogger(__name__)


def build_admin_router(deps: SharedDeps) -> APIRouter:
    router = APIRouter()
    settings = deps.settings
    host = deps.host

    @router.get("/api/admin/system")
    async def admin_system(
        x_tau_voice_token: str | None = Header(default=None),
        x_tau_admin_service_token: str | None = Header(default=None),
        x_tau_device_id: str | None = Header(default=None),
        x_tau_device_token: str | None = Header(default=None),
    ) -> dict:
        """Hardware/model snapshot (utility-mcp-server.get_system_status), admin-gated.

        Phase 16: this used to be a direct, ungated call to the generic /api/tools passthrough
        from the frontend - self-knowledge, but still a live picture of the host machine (model,
        VRAM, uptime) that belongs behind the same gate as the rest of the admin surface, not
        reachable by anything on the LAN.
        """
        _require_admin(deps, x_tau_voice_token, x_tau_admin_service_token)
        _device_id(deps, x_tau_device_id, x_tau_device_token)
        try:
            return await _tool_json(
                deps, "utility-mcp-server", "get_system_status", {},
                requested_by="tau-core-web-admin",
            )
        except Exception as exc:  # noqa: BLE001 - utility server not connected yet
            logger.warning("Could not fetch system status: %s", exc)
            return {}

    @router.get("/api/admin/people")
    async def admin_people(
        x_tau_voice_token: str | None = Header(default=None),
        x_tau_admin_service_token: str | None = Header(default=None),
        x_tau_device_id: str | None = Header(default=None),
        x_tau_device_token: str | None = Header(default=None),
    ) -> dict:
        """Person roster with access levels (memory-mcp-server.list_people), admin-gated.

        Not to be confused with the *read-only* people roster the non-admin SystemDrawer already
        shows (usePeople(), still on the open passthrough - that visibility was already a
        deliberate product choice, unchanged here). This is the admin-only view the new User
        Profiles panel drives its access-level editor from.
        """
        _require_admin(deps, x_tau_voice_token, x_tau_admin_service_token)
        _device_id(deps, x_tau_device_id, x_tau_device_token)
        try:
            return await _tool_json(
                deps, "memory-mcp-server", "list_people", {},
                requested_by="tau-core-web-admin",
            )
        except Exception as exc:  # noqa: BLE001 - memory server not connected yet
            logger.warning("Could not fetch people roster: %s", exc)
            return {"people": []}

    @router.get("/api/admin/security")
    async def admin_security(
        x_tau_voice_token: str | None = Header(default=None),
        x_tau_admin_service_token: str | None = Header(default=None),
        x_tau_device_id: str | None = Header(default=None),
        x_tau_device_token: str | None = Header(default=None),
    ) -> dict:
        """Security posture snapshot, admin-gated: whether voice approval is required (and thus
        whether this very gate is doing anything beyond LAN-trust), the calibrated voice-match
        distance, and the access-tier policy that decides who may approve what. Read-only in
        this slice - DEFAULT_POLICY is hardcoded Python and require_voice_approval needs a
        process restart to take effect, so there is no write endpoint to pair with this yet.
        """
        _require_admin(deps, x_tau_voice_token, x_tau_admin_service_token)
        _device_id(deps, x_tau_device_id, x_tau_device_token)
        return {
            "require_voice_approval": settings.require_voice_approval,
            "voice_match_max_distance": settings.voice_match_max_distance,
            "tier_policy": [
                {"server": r.server, "tool": r.tool, "min_tier": r.min_tier, "reason": r.reason}
                for r in DEFAULT_POLICY
            ],
        }

    @router.get("/api/drafts")
    async def list_drafts(
        x_tau_voice_token: str | None = Header(default=None),
        x_tau_admin_service_token: str | None = Header(default=None),
        x_tau_device_id: str | None = Header(default=None),
        x_tau_device_token: str | None = Header(default=None),
        limit: int = 50,
    ) -> list[dict]:
        """Unverified draft memories awaiting human review - admin-only (Phase 8.C).

        This is the review queue that makes the draft tier's "batch-reviewed" real. Without a
        surface, `draft_memory` would be exactly the silent auto-memory project-tau-plan.md §7.3
        declined: Tau writing notes about the household that nobody ever reads.

        Admin-gated for the same reason the unified transcript is - it is a dump of everything
        Tau has inferred about people, including kids. Degrades to an empty list rather than an
        error when memory-mcp-server isn't connected: an empty review queue is honest ("nothing
        to review right now"), and a dashboard panel should not 502 because one domain server is
        down.
        """
        _require_admin(deps, x_tau_voice_token, x_tau_admin_service_token)
        _device_id(deps, x_tau_device_id, x_tau_device_token)
        try:
            outcome = await host.call_tool(
                "memory-mcp-server", "list_drafts", {"limit": limit},
                requested_by="tau-core-web-admin",
            )
        except Exception as exc:  # noqa: BLE001 - see docstring
            logger.warning("Could not list drafts: %s", exc)
            return []
        if outcome.status is not ToolCallStatus.EXECUTED or not outcome.result:
            return []
        drafts = []
        # FastMCP returns one content block per list item, so this reads every block - the bug
        # that silently truncated list-returning tools to their first element (Phase 3, UI batch 2).
        for item in outcome.result.content:
            text = getattr(item, "text", None)
            if not text:
                continue
            try:
                drafts.append(json.loads(text))
            except json.JSONDecodeError:
                continue
        return drafts

    @router.get("/api/drafts/count")
    async def drafts_count() -> dict:
        """How many drafts are awaiting review. **Deliberately not admin-gated.**

        This is the nudge (Phase 9): the ADMIN toolbar entry wears it as a badge, because a
        review queue nobody is told about silts up, and §8.7 called that out as the thing most
        undercutting the draft tier. Gating the prompt behind the very dashboard it exists to
        send you to would be circular - you would only learn there was something to review after
        deciding to go and look.

        A count is metadata, not content: "there are 5 unreviewed notes" reveals nothing about
        the household, while the notes themselves stay behind `_require_admin` on /api/drafts.
        That is the line - and it is the whole reason this is a separate endpoint rather than
        the kiosk polling /api/drafts and counting the array, which would ship every draft's
        body to every device on the LAN to render one number.
        """
        try:
            outcome = await host.call_tool(
                "memory-mcp-server", "list_drafts", {"limit": 200},
                requested_by="tau-core-web-draft-count",
            )
        except Exception:  # noqa: BLE001 - a badge must never break the toolbar
            return {"count": 0}
        if outcome.status is not ToolCallStatus.EXECUTED or not outcome.result:
            return {"count": 0}
        return {"count": sum(1 for item in outcome.result.content if getattr(item, "text", None))}

    @router.get("/api/transcript/unified")
    async def unified_transcript(
        x_tau_voice_token: str | None = Header(default=None),
        x_tau_admin_service_token: str | None = Header(default=None),
        x_tau_device_id: str | None = Header(default=None),
        x_tau_device_token: str | None = Header(default=None),
    ) -> dict:
        """The full cross-device conversation log - admin-only (Phase 6.D). The record kept
        server-side while normal device views show only their own turns.

        **Not durable, despite what this docstring used to claim** (Phase 7 Tier 0 item 7,
        corrected 2026-07-15). It is a module-level global in `ui-bridge-mcp-server/server.py`,
        so it dies with that container - `docker compose restart ui-bridge-mcp-server` silently
        empties it. That matters more than it looks: 6.D's privacy design leans on this being
        where the durable record lives *instead of* on the glass (6.F removed kiosk scrollback on
        exactly that basis), so today the honest summary is that older turns are retained
        nowhere. Persisting it is tracked in Phase 10; the claim is corrected here rather than
        left as a lie in the meantime."""
        _require_admin(deps, x_tau_voice_token, x_tau_admin_service_token)
        _device_id(deps, x_tau_device_id, x_tau_device_token)
        try:
            data = await _tool_json(
                deps, "ui-bridge-mcp-server", "get_unified_transcript", {},
                requested_by="tau-core-web-admin",
            )
        except Exception as exc:  # noqa: BLE001 - sanitized (Phase 7 Tier 1 #9); logged below
            raise _sanitized_error(
                exc, 502, "Could not fetch the unified transcript.", "unified_transcript"
            ) from exc
        return data if isinstance(data, dict) else {"history": []}

    @router.get("/api/activity")
    async def activity(
        limit: int = 100,
        x_tau_voice_token: str | None = Header(default=None),
        x_tau_admin_service_token: str | None = Header(default=None),
        x_tau_device_id: str | None = Header(default=None),
        x_tau_device_token: str | None = Header(default=None),
    ) -> list[dict]:
        """Recent audit events (tool calls with their CDG effect and outcome), newest first -
        the audit trail from project-tau-plan.md section 1. Served from the in-process ring
        buffer, so it reflects this bridge process's lifetime; the durable JSON stderr stream is
        unchanged.

        Admin-gated (Phase 16) - this is the same kind of household-activity record the unified
        transcript and draft queue are already gated behind, and had no gate at all before this.
        """
        _require_admin(deps, x_tau_voice_token, x_tau_admin_service_token)
        _device_id(deps, x_tau_device_id, x_tau_device_token)
        return recent_audit_events(limit=min(max(limit, 1), 500))

    return router
