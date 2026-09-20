"""Pending human-decision queue: list, approve, deny.

Split out of web/server.py's create_app() in Phase 49. `_verified_decider` is specific to this
domain (unlike the deps.py helpers) - it needs `ActionRequest`/`required_tier` to decide WHETHER
a verified voice identity may approve THIS particular action, which nothing outside approvals
needs.
"""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from tau_core.access import required_tier, tier_at_least
from tau_core.approval.queue import ActionRequest, ApprovalError
from tau_core.logging_setup import redact_arguments
from tau_core.web.deps import SharedDeps, _device_id


class ApprovalDecision(BaseModel):
    decided_by: str = "tablet-ui"
    note: str | None = None


def build_approvals_router(deps: SharedDeps) -> APIRouter:
    router = APIRouter()
    settings = deps.settings
    host = deps.host
    challenges = deps.challenges

    def _verified_decider(voice_token: str | None, request: ActionRequest, decided_by: str) -> str:
        """When TAU_REQUIRE_VOICE_APPROVAL is on, approval decisions need a verified voice
        challenge token carried in the X-Tau-Voice-Token header (Phase 16: moved off the JSON
        body, same URL/log-leakage reasoning as the admin-read endpoints); 428 tells the UI to
        run the challenge flow. Voice identity here answers WHO decided - the CDG still decides
        what needed approval in the first place - and the access tier gates WHETHER that person
        may approve *this* action (403 if not).
        """
        if not settings.require_voice_approval:
            return decided_by
        if not voice_token:
            raise HTTPException(
                status_code=428,
                detail="Voice verification required: complete a challenge (POST /api/voice/challenge) first",
            )
        identity = challenges.consume_token(voice_token)
        if identity is None:
            raise HTTPException(status_code=428, detail="Voice token invalid, expired, or already used")
        person_id, access_level = identity
        need = required_tier(request.server, request.tool)
        if not tier_at_least(access_level, need):
            raise HTTPException(
                status_code=403,
                detail=(
                    f"'{person_id}' (access '{access_level or 'unknown'}') may not approve "
                    f"{request.server}.{request.tool}: requires '{need}' access."
                ),
            )
        return f"voice:{person_id}"

    @router.get("/api/approvals")
    async def list_approvals(
        x_tau_device_id: str | None = Header(default=None),
        x_tau_device_token: str | None = Header(default=None),
    ) -> list[dict]:
        """Pending human decisions, for the kiosk's ApprovalQueue card.

        Arguments are redacted the same way the audit trail redacts them (Phase 7 Tier 0 item 6):
        this endpoint has no auth and is served to every device on the LAN, and a voice-enrolment
        approval carries a raw voiceprint embedding while an image one carries the frame. Nobody
        approving from a tablet can meaningfully review 384 floats anyway - "<redacted list, 384
        items>" tells them everything the decision actually turns on. The queue keeps the real
        arguments server-side (it must - the CDG binds the approval to their exact hash), so
        redacting the *display* costs nothing: the original caller re-sends its own arguments,
        it never reconstructs them from this response.

        Phase 27.A: this was the one route with *zero* gate of any kind (not even the weak
        `_require_admin` lan-trust default) - `/api/approvals`'s list leaked pending ids and
        mostly-unredacted arguments to any LAN client. It's still not `_require_admin`-gated (the
        kiosk's own ApprovalQueue card needs to read it without an admin voice token), but it now
        goes through the same device-token check as everything else in this phase.
        """
        _device_id(deps, x_tau_device_id, x_tau_device_token)
        return [
            {
                "id": r.id,
                "server": r.server,
                "tool": r.tool,
                "arguments": redact_arguments(r.arguments),
                "reason": r.reason,
                "requested_by": r.requested_by,
                "requested_at": r.requested_at.isoformat(),
                "expires_at": r.expires_at.isoformat() if r.expires_at else None,
            }
            for r in host.approvals.list_pending()
        ]

    @router.post("/api/approvals/{request_id}/approve")
    async def approve_action(
        request_id: str,
        decision: ApprovalDecision,
        x_tau_voice_token: str | None = Header(default=None),
    ) -> dict:
        try:
            request = host.approvals.get(request_id)
        except ApprovalError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        decided_by = _verified_decider(x_tau_voice_token, request, decision.decided_by)
        try:
            request = host.approvals.approve(request_id, decided_by, decision.note)
        except ApprovalError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"id": request.id, "status": request.status.value, "decided_by": decided_by}

    @router.post("/api/approvals/{request_id}/deny")
    async def deny_action(
        request_id: str,
        decision: ApprovalDecision,
        x_tau_voice_token: str | None = Header(default=None),
    ) -> dict:
        try:
            request = host.approvals.get(request_id)
        except ApprovalError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        decided_by = _verified_decider(x_tau_voice_token, request, decision.decided_by)
        try:
            request = host.approvals.deny(request_id, decided_by, decision.note)
        except ApprovalError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"id": request.id, "status": request.status.value, "decided_by": decided_by}

    return router
