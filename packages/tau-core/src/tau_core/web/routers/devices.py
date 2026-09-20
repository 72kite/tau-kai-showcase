"""Device registration and admin device management (register/list/block/unblock/approve).

Split out of web/server.py's create_app() in Phase 49.
"""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from tau_core.web.deps import SharedDeps, _device_id, _require_admin


class DeviceRegisterRequest(BaseModel):
    device_id: str
    name: str = ""


def build_devices_router(deps: SharedDeps) -> APIRouter:
    router = APIRouter()
    devices = deps.devices

    @router.post("/api/devices/register")
    async def register_device(body: DeviceRegisterRequest) -> dict:
        """Announce a device and (optionally) name it (Phase 6.D). Open by design: a brand-new
        client has no admin token yet, and registering only records an opaque client-chosen id +
        a display name - it grants nothing. What a device may *do* is still the CDG/tier system's
        job, never identity alone."""
        try:
            device = devices.register(body.device_id, body.name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return device.as_dict()

    @router.get("/api/devices")
    async def list_devices(
        x_tau_voice_token: str | None = Header(default=None),
        x_tau_admin_service_token: str | None = Header(default=None),
    ) -> list[dict]:
        """All known devices (admin-only - Phase 6.D/6.E). The device inventory is part of the
        admin surface, so it's tier-gated like the unified transcript."""
        _require_admin(deps, x_tau_voice_token, x_tau_admin_service_token)
        return [d.as_dict() for d in devices.list()]

    @router.post("/api/admin/devices/{device_id}/block")
    async def block_device(
        device_id: str,
        x_tau_voice_token: str | None = Header(default=None),
        x_tau_admin_service_token: str | None = Header(default=None),
        x_tau_device_id: str | None = Header(default=None),
        x_tau_device_token: str | None = Header(default=None),
    ) -> dict:
        """Revoke a device (Phase 16). Enforcement is in _device_id(), not here - this just
        flips the flag every future request from that device_id is checked against."""
        _require_admin(deps, x_tau_voice_token, x_tau_admin_service_token)
        _device_id(deps, x_tau_device_id, x_tau_device_token)
        device = devices.block(device_id)
        if device is None:
            raise HTTPException(status_code=404, detail=f"Unknown device '{device_id}'")
        return device.as_dict()

    @router.post("/api/admin/devices/{device_id}/unblock")
    async def unblock_device(
        device_id: str,
        x_tau_voice_token: str | None = Header(default=None),
        x_tau_admin_service_token: str | None = Header(default=None),
        x_tau_device_id: str | None = Header(default=None),
        x_tau_device_token: str | None = Header(default=None),
    ) -> dict:
        _require_admin(deps, x_tau_voice_token, x_tau_admin_service_token)
        _device_id(deps, x_tau_device_id, x_tau_device_token)
        device = devices.unblock(device_id)
        if device is None:
            raise HTTPException(status_code=404, detail=f"Unknown device '{device_id}'")
        return device.as_dict()

    @router.post("/api/admin/devices/{device_id}/approve")
    async def approve_device(
        device_id: str,
        x_tau_voice_token: str | None = Header(default=None),
        x_tau_admin_service_token: str | None = Header(default=None),
        x_tau_device_id: str | None = Header(default=None),
        x_tau_device_token: str | None = Header(default=None),
    ) -> dict:
        """Grant a pending device a bearer token (Phase 27.A). Admin-gated like block/unblock.

        The raw token is returned in THIS response only - devices.py never stores it, only its
        hash - so this is the one and only place it ever leaves the process. By design there is
        no automated hand-off to the device being approved: the admin copies it from here and
        enters it on that device (AdminDashboard.jsx's reveal-once box). An automated
        "device polls and claims its own token" path was considered and rejected - `/api/devices`
        already lists every pending device_id under the default LAN-trust setting, so anything
        that could read that list could also race the real device for its token the moment it's
        minted, defeating the point of requiring one at all.
        """
        _require_admin(deps, x_tau_voice_token, x_tau_admin_service_token)
        _device_id(deps, x_tau_device_id, x_tau_device_token)
        token = devices.approve(device_id)
        if token is None:
            raise HTTPException(status_code=404, detail=f"Unknown device '{device_id}'")
        device = devices.get(device_id)
        return {**device.as_dict(), "token": token}

    return router
