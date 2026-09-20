"""Shared, cross-domain request-handling helpers for tau-core's web routers.

Split out of web/server.py's create_app() in Phase 49. These five functions are used by
nearly every domain (devices, admin, approvals, voice, chat), so they live here instead of
inside any one domain router. `_client_key`/`_enforce_rate_limit` close over nothing and keep
their original signatures; `_device_id`/`_require_admin`/`_tool_json` take the `SharedDeps`
they used to close over as an explicit first argument instead.
"""

from __future__ import annotations

import hmac
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException, Request

from tau_core.access import tier_at_least
from tau_core.config import TauCoreSettings
from tau_core.host import TauCoreHost, ToolCallStatus
from tau_core.llm.agent import TauAssistant
from tau_core.web.devices import DeviceRegistry
from tau_core.web.identity import ChallengeStore
from tau_core.web.ratelimit import RateLimiter

logger = logging.getLogger(__name__)

# 10MB of raw file, before base64's ~4/3 inflation. Shared between voice.py (audio uploads) and
# chat.py (attachments) - both ride inside a JSON body (fine at this scale on a LAN); anything
# bigger deserves a real upload pipeline, not a bump.
MAX_ATTACHMENT_B64_CHARS = 14_000_000


def _sanitized_error(exc: Exception, status_code: int, message: str, context: str) -> HTTPException:
    """Logs the real exception server-side, returns a generic message to the client.

    Phase 7 Tier 1 #9: several endpoints here have no auth at all, so a broad `except Exception`
    handing back `str(exc)` was handing raw exception text - internal paths, a library's own
    error shapes, fragments of whatever a domain server's client was doing - to any device on the
    LAN. The full exception still reaches the server log for whoever is actually debugging it;
    only the HTTP response is generic. Callers who need the request to succeed already have
    everything they sent (server name, tool name, etc.) to retry or report; they don't need our
    internals to do that.
    """
    logger.warning("%s: %r", context, exc, exc_info=True)
    return HTTPException(status_code=status_code, detail=message)


@dataclass
class SharedDeps:
    """Everything create_app() used to close over, threaded explicitly into each domain
    router's build_*_router(deps) factory instead of being nested inside create_app() itself."""

    settings: TauCoreSettings
    host: TauCoreHost
    devices: DeviceRegistry
    challenges: ChallengeStore
    chat_limiter: RateLimiter
    voice_limiter: RateLimiter
    assistant_holder: dict[str, TauAssistant] = field(default_factory=dict)


def _client_key(request: Request, device_id: str = "") -> str:
    """Device id when the caller sent one (stable across a client's own reconnects/NAT);
    otherwise the connecting IP. Good enough to separate "this tablet" from "that tablet" on
    a LAN - not an identity claim, and grants nothing by itself."""
    if device_id:
        return f"device:{device_id}"
    client = request.client
    return f"ip:{client.host}" if client else "ip:unknown"


def _enforce_rate_limit(limiter: RateLimiter, key: str) -> None:
    if limiter.allow(key):
        return
    retry_after = limiter.retry_after(key)
    raise HTTPException(
        status_code=429,
        detail="Too many requests - please slow down.",
        headers={"Retry-After": str(max(1, int(retry_after) + 1))},
    )


def _device_id(deps: SharedDeps, header_value: str | None, token_header_value: str | None = None) -> str:
    """Normalize + record the calling device. Every request that carries X-Tau-Device-Id
    touches the registry (updates last_seen / creates a minimal entry), so the admin device
    list stays current without a separate heartbeat. Returns '' for an anonymous client.

    Phase 16 admin portal: also the enforcement point for device revocation. A device is
    touched first (so a revoked device's attempt is still visible to an admin - it did not
    vanish, it was refused), then blocked with a 403 if an admin has revoked it. Anonymous
    callers (no header) are unaffected, matching the existing "device id grants nothing by
    itself" model - it can now cost something, but never grants anything on its own.

    Phase 27.A: when TAU_REQUIRE_DEVICE_TOKEN is on, an identified device must also present a
    valid X-Tau-Device-Token bound to an admin-approved DeviceRegistry entry - checked after
    the revocation check above, so a blocked device gets the "revoked" message rather than a
    confusing "pending approval" one. A device with no token yet (never approved, or an
    invalid/stale one) is refused rather than silently treated as unblocked, same fail-closed
    direction as the revocation check. Off by default; still no effect on anonymous callers -
    this governs identified-but-unapproved devices, not whether identification is required at
    all (that's a per-route decision, made where each route calls this)."""
    device_id = (header_value or "").strip()
    if device_id:
        deps.devices.touch(device_id)
        device = deps.devices.get(device_id)
        if device is not None and device.blocked:
            raise HTTPException(status_code=403, detail="This device has been revoked.")
        if deps.settings.require_device_token and not deps.devices.verify_token(
            device_id, token_header_value or ""
        ):
            raise HTTPException(
                status_code=403, detail="Device is pending admin approval or its token is invalid."
            )
    return device_id


def _require_admin(deps: SharedDeps, voice_token: str | None, service_token: str | None = None) -> str:
    """Gate for admin-only reads (unified transcript, device list, audit feed, system/people
    snapshots - Phase 6.D/16). Consistent with the approval posture: when
    TAU_REQUIRE_VOICE_APPROVAL is off the bridge is in LAN-trust mode (any client may read -
    returns 'lan-trust'); when on, a verified admin voice token is required, carried in the
    X-Tau-Voice-Token header (not a query param - Phase 16 hardening, query strings leak into
    server/proxy logs and browser history). Uses peek_token (non-consuming) so an admin
    dashboard can poll within the token's life without burning it - a read is not a replay
    risk.

    Phase 38: `service_token` (X-Tau-Admin-Service-Token) is a second, independent way to
    satisfy this same gate - the standalone tau-admin-server's credential for calling this
    surface on behalf of a human who already authenticated with a real username/password
    login over there. Checked FIRST and regardless of require_voice_approval, since a fresh
    install with nobody enrolled for voice should still be able to run the separate admin
    panel. Constant-time compared; unset TAU_ADMIN_SERVICE_TOKEN (the default) disables this
    path entirely rather than comparing against None/empty-string, which would otherwise
    accept a blank header.
    """
    if deps.settings.admin_service_token and service_token:
        if hmac.compare_digest(service_token, deps.settings.admin_service_token):
            return "service:tau-admin-server"
    if not deps.settings.require_voice_approval:
        return "lan-trust"
    if not voice_token:
        raise HTTPException(status_code=428, detail="Admin voice verification required")
    identity = deps.challenges.peek_token(voice_token)
    if identity is None:
        raise HTTPException(status_code=428, detail="Voice token invalid or expired")
    person_id, access_level = identity
    if not tier_at_least(access_level, "admin"):
        raise HTTPException(
            status_code=403,
            detail=f"'{person_id}' (access '{access_level or 'unknown'}') is not admin",
        )
    return f"voice:{person_id}"


async def _tool_json(deps: SharedDeps, server_name: str, tool: str, arguments: dict, **kwargs) -> Any:
    """call_tool + parse-first-content-as-JSON, for the identity pipeline's orchestration
    calls. Raises on anything but a clean EXECUTED result - callers decide whether that's
    fatal (enrollment) or degrades (identification)."""
    outcome = await deps.host.call_tool(server_name, tool, arguments, **kwargs)
    if outcome.status is not ToolCallStatus.EXECUTED or not outcome.result:
        raise RuntimeError(f"{server_name}.{tool}: {outcome.status.value}: {outcome.reason}")
    content = outcome.result.content
    text = content[0].text if content else ""
    if getattr(outcome.result, "isError", False):
        raise RuntimeError(f"{server_name}.{tool} failed: {text}")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text
