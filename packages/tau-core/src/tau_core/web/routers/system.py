"""System-tier routes: liveness, TLS pairing fingerprint, device-scoped UI state.

Split out of web/server.py's create_app() in Phase 49. Grouped separately from `admin.py`
because none of these three require `_require_admin` - `/api/health` and `/api/tls/fingerprint`
have no auth at all (by design - see their docstrings below), and `/api/state` is gated only
by `_device_id`, not admin tier. Folding them into `admin.py` would misstate that.
"""

from __future__ import annotations

import json
import logging
import time

from fastapi import APIRouter, Header

from tau_core import tls
from tau_core.version import build_sha, tau_core_version
from tau_core.web.deps import SharedDeps, _device_id, _sanitized_error, _tool_json

logger = logging.getLogger(__name__)

# Process start, for /api/health's uptime. Module import time rather than a real start timestamp,
# the same approximation utility-mcp-server.server makes and for the same reason: this module is
# imported during app construction, so the difference is milliseconds, and nothing here needs
# better than that. Monotonic, so a clock adjustment (NTP step, DST) cannot make uptime jump or
# go backwards on a kiosk that has been showing it for a week.
_STARTED_MONOTONIC = time.monotonic()


def build_system_router(deps: SharedDeps) -> APIRouter:
    router = APIRouter()
    settings = deps.settings
    host = deps.host

    @router.get("/api/health")
    async def health() -> dict:
        """Liveness + an honest picture of what is actually up (Phase 7 Tier 0 item 7).

        The version/build are what the kiosk footer compares itself against to notice it is
        serving a stale cached bundle (StatusFooter.jsx) - a service-worker PWA can otherwise run
        an old frontend against a new bridge with no visible symptom.

        **`status` used to be the string "ok", unconditionally.** It reported what the bridge
        *was*, not whether it worked, so wiring a healthcheck to it would have passed while the
        host was wholly broken. It now derives from real state: `connected_servers()` checks
        actual session liveness (Phase 7), so a wedged manager or a dead session shows up here
        rather than being papered over.

        **Deliberately always HTTP 200 while the process can serve, including when `degraded`.**
        This endpoint is a *liveness* probe, and the compose healthcheck treats it as one. It is
        tempting to fail it when domain servers are missing - don't: restarting tau-core cannot
        fix a domain server being down, and Phase 7 went to some trouble to make the host survive
        exactly that (per-server sessions, on-demand reconnect, partial-degradation boot). A
        healthcheck that killed the host because a neighbour died would re-introduce the coupling
        that work removed, and would restart-loop the one process holding the approval queue. The
        real signal a liveness probe carries is "did this answer at all" - a wedged event loop
        times out, and *that* is worth a restart.

        `degraded` is therefore information for humans and the admin dashboard, not an instruction
        to the orchestrator.

        **`device_token_enforced` (Phase 27.A)** reports whether `TAU_REQUIRE_DEVICE_TOKEN` is
        on - the open-by-default state used to be silent, so the frontend shows a persistent
        banner when this is `False` rather than an admin having to know to check.
        """
        registered = host.mcp.registered_servers()
        connected = host.mcp.connected_servers()
        unavailable = sorted(set(registered) - set(connected))
        return {
            # ok = everything registered is connected; degraded = some domain servers are down but
            # the host is serving fine and their tools will reconnect on demand.
            "status": "degraded" if unavailable else "ok",
            "version": tau_core_version(),
            "build": build_sha(),
            "connected_servers": connected,
            "registered_servers": registered,
            "unavailable_servers": unavailable,
            "device_token_enforced": settings.require_device_token,
            # How long THIS bridge process has been up. The toolbar shows it next to the clock so
            # a glance at a kiosk says whether tau-core restarted overnight - previously only
            # utility-mcp-server knew this, and only the model could ask it (get_system_status),
            # which is no use to a person standing in front of the screen. The frontend polls this
            # slowly and ticks the seconds locally; see useUptime.js.
            "uptime_seconds": round(time.monotonic() - _STARTED_MONOTONIC, 1),
        }

    @router.get("/api/tls/fingerprint")
    async def tls_fingerprint() -> dict:
        """Phase 27.D Milestone 3: the pairing "code" a desktop client compares against what it
        actually received over the wire during its TLS handshake - trust-on-first-use, the same
        pattern as an SSH host-key fingerprint or a Signal safety number. Deliberately served on
        *both* the plain-HTTP and TLS listeners (this value isn't a secret; showing it openly is
        the whole point) and generates the cert on first call if it doesn't exist yet, so this
        works regardless of whether the TLS listener (a separate `uvicorn.Server` in
        `web/__main__.py`) has started yet.
        """
        tls.get_or_create_cert(settings.tls_cert_path, settings.tls_key_path)
        return {"fingerprint": tls.fingerprint_sha256(settings.tls_cert_path)}

    @router.get("/api/state")
    async def get_state(
        x_tau_device_id: str | None = Header(default=None),
        x_tau_device_token: str | None = Header(default=None),
    ) -> dict:
        """Device-scoped UI state (Phase 6.D). Same shape as ui-bridge's ui://state, but the
        transcript is ONLY the calling device's own turns - never another device's, never the
        unified log. Non-transcript sections (security/vision/devices/design/recognition) are
        genuinely shared and pass through unchanged. This replaces the frontend's direct read of
        ui://state for the conversation view, which leaked every device's chat to every client.
        """
        device_id = _device_id(deps, x_tau_device_id, x_tau_device_token)
        try:
            result = await host.mcp.read_resource("ui-bridge-mcp-server", "ui://state")
            state = json.loads(result.contents[0].text) if result.contents else {}
        except Exception as exc:  # noqa: BLE001 - sanitized (Phase 7 Tier 1 #9); logged below
            raise _sanitized_error(
                exc, 502, "ui-bridge-mcp-server state is unavailable.", "get_state"
            ) from exc

        history: list = []
        if device_id:
            try:
                dev = await _tool_json(
                    deps, "ui-bridge-mcp-server", "get_device_transcript", {"device_id": device_id},
                    requested_by="tau-core-web-state",
                )
                if isinstance(dev, dict):
                    history = dev.get("history", []) or []
            except Exception:  # noqa: BLE001 - no history rather than another device's, on failure
                logger.warning("Could not fetch device transcript for %s", device_id, exc_info=True)
        state.setdefault("transcription", {})
        state["transcription"]["history"] = history
        return state

    return router
