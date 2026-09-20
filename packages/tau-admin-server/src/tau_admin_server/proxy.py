"""Thin async client for the admin-data surface this service fronts on tau-core (Phase 38). Every
call carries X-Tau-Admin-Service-Token, which satisfies tau-core's _require_admin gate the same
way an admin voice token would - see web/server.py's docstring on that credential. Also carries a
fixed X-Tau-Device-Id ("tau-admin-server") since a few of the routes proxied here (the drafts
promote/discard passthrough) are only reachable through /api/tools/{server}/{tool}, which is
gated by tau-core's separate _device_id() chokepoint, not _require_admin. Also carries
X-Tau-Device-Token when TAU_ADMIN_TAU_CORE_DEVICE_TOKEN is set - since Phase 48,
TAU_REQUIRE_DEVICE_TOKEN is ON by default over on tau-core, so that synthetic device needs
approving from the admin dashboard and its minted token pasted into this service's .env, the same
as any other non-anonymous client (README's pre-flip checklist covers it). Skipping that step
doesn't break tau-admin-server as a whole - only the drafts promote/discard buttons 403 until it's
approved, everything gated by _require_admin alone still works.

No retry/circuit-breaker logic here on purpose: this proxies a small, low-traffic admin surface
(a human clicking buttons), not the wake-word poll path - a failed call should surface as an
error to the admin, not be silently retried against a possibly-still-broken tau-core.
"""

from __future__ import annotations

import json
from typing import Any

import httpx


class TauCoreError(RuntimeError):
    """Wraps a non-2xx response from tau-core, preserving its status code and detail so the
    admin-server route can re-raise the same shape (428 needs admin verification, 403 forbidden,
    etc.) instead of collapsing everything to a generic 502."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _extract_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
        if isinstance(body, dict) and "detail" in body:
            return str(body["detail"])
    except (json.JSONDecodeError, ValueError):
        pass
    return response.text or f"tau-core returned {response.status_code}"


class TauCoreProxy:
    def __init__(
        self,
        base_url: str,
        service_token: str | None,
        timeout_seconds: float = 15.0,
        admin_device_id: str = "tau-admin-server",
        device_token: str | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._service_token = service_token
        self._timeout = timeout_seconds
        self._admin_device_id = admin_device_id
        self._device_token = device_token
        # Test seam: a MockTransport swaps in a fake tau-core without a real socket. None (the
        # default) uses httpx's normal transport for real deployments.
        self._transport = transport

    def _headers(self) -> dict[str, str]:
        headers = {"X-Tau-Device-Id": self._admin_device_id}
        if self._service_token:
            headers["X-Tau-Admin-Service-Token"] = self._service_token
        if self._device_token:
            headers["X-Tau-Device-Token"] = self._device_token
        return headers

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        async with httpx.AsyncClient(
            base_url=self._base_url, timeout=self._timeout, transport=self._transport
        ) as client:
            response = await client.request(method, path, headers=self._headers(), **kwargs)
        if response.status_code >= 400:
            raise TauCoreError(response.status_code, _extract_detail(response))
        return response.json() if response.content else None

    # --- devices ----------------------------------------------------------------------------
    async def list_devices(self) -> list[dict]:
        return await self._request("GET", "/api/devices")

    async def approve_device(self, device_id: str) -> dict:
        return await self._request("POST", f"/api/admin/devices/{device_id}/approve")

    async def block_device(self, device_id: str) -> dict:
        return await self._request("POST", f"/api/admin/devices/{device_id}/block")

    async def unblock_device(self, device_id: str) -> dict:
        return await self._request("POST", f"/api/admin/devices/{device_id}/unblock")

    # --- snapshots ----------------------------------------------------------------------------
    async def system_snapshot(self) -> dict:
        return await self._request("GET", "/api/admin/system")

    async def people_snapshot(self) -> dict:
        return await self._request("GET", "/api/admin/people")

    async def security_snapshot(self) -> dict:
        return await self._request("GET", "/api/admin/security")

    async def unified_transcript(self) -> dict:
        return await self._request("GET", "/api/transcript/unified")

    async def activity_feed(self, limit: int = 50) -> list[dict]:
        return await self._request("GET", "/api/activity", params={"limit": limit})

    # --- people / access levels -----------------------------------------------------------------
    # Same reasoning as drafts promote/discard below: on tau-core, set_access_level rides the
    # generic /api/tools passthrough (device-token-gated only, off by default), not
    # _require_admin. This route requires a real admin session instead of mirroring that shape.
    async def set_access_level(
        self, person_id: str, access_level: str, approval_request_id: str | None = None
    ) -> dict:
        body: dict[str, Any] = {
            "arguments": {"person_id": person_id, "access_level": access_level},
            "requested_by": "tau-admin-server",
        }
        if approval_request_id:
            body["approval_request_id"] = approval_request_id
        return await self._request("POST", "/api/tools/memory-mcp-server/set_access_level", json=body)

    # --- draft memory review -------------------------------------------------------------------
    async def list_drafts(self) -> list[dict]:
        return await self._request("GET", "/api/drafts")

    async def promote_draft(self, node_id: str, approval_request_id: str | None = None) -> dict:
        body: dict[str, Any] = {"arguments": {"node_id": node_id}, "requested_by": "tau-admin-server"}
        if approval_request_id:
            body["approval_request_id"] = approval_request_id
        return await self._request("POST", "/api/tools/memory-mcp-server/promote_memory", json=body)

    async def discard_draft(self, node_id: str) -> dict:
        body = {"arguments": {"node_id": node_id}, "requested_by": "tau-admin-server"}
        return await self._request("POST", "/api/tools/memory-mcp-server/discard_draft", json=body)

    # --- wake word ------------------------------------------------------------------------------
    async def list_wake_words(self) -> list[dict]:
        return await self._request("GET", "/api/voice/wake-words")

    async def set_wake_word(self, wake_id: str, approval_request_id: str | None = None) -> dict:
        body: dict[str, Any] = {"wake_id": wake_id}
        if approval_request_id:
            body["approval_request_id"] = approval_request_id
        return await self._request("POST", "/api/voice/wake-words", json=body)

    # --- spoken voice ---------------------------------------------------------------------------
    async def list_voices(self) -> list[dict]:
        return await self._request("GET", "/api/voice/voices")

    async def set_voice(self, voice_id: str, approval_request_id: str | None = None) -> dict:
        body: dict[str, Any] = {"voice_id": voice_id}
        if approval_request_id:
            body["approval_request_id"] = approval_request_id
        return await self._request("POST", "/api/voice/voices", json=body)

    # --- approvals ------------------------------------------------------------------------------
    # Unlike drafts/set_access_level above, tau-core already has a real, dedicated endpoint here
    # (/api/approvals, gated by _device_id() rather than _require_admin - see that route's own
    # docstring on why: the kiosk's ApprovalQueue card needs to read it with no admin credential
    # at all) - so this is a direct mirror, not a passthrough call. If tau-core has
    # TAU_REQUIRE_VOICE_APPROVAL on, approve/deny there also needs a verified X-Tau-Voice-Token,
    # which this proxy has no way to supply (it authenticates as a service, not a spoken person) -
    # that surfaces as a 428 from tau-core, forwarded as-is rather than silently swallowed.
    async def list_approvals(self) -> list[dict]:
        return await self._request("GET", "/api/approvals")

    async def approve_action(self, request_id: str, decided_by: str, note: str | None = None) -> dict:
        body: dict[str, Any] = {"decided_by": decided_by}
        if note:
            body["note"] = note
        return await self._request("POST", f"/api/approvals/{request_id}/approve", json=body)

    async def deny_action(self, request_id: str, decided_by: str, note: str | None = None) -> dict:
        body: dict[str, Any] = {"decided_by": decided_by}
        if note:
            body["note"] = note
        return await self._request("POST", f"/api/approvals/{request_id}/deny", json=body)
