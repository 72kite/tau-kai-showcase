"""HTTP bridge between the tablet/browser frontend (Phase 3) and TauCoreHost.

Before this module, tau-core was a pure Python library with no network-facing surface - the
frontend's resource-subscription hook assumed a WebSocket endpoint that nothing actually
served. This is that missing piece, built as plain HTTP + polling rather than WebSocket:
older iPads (the deployment target - see project-tau-plan.md Phase 3) hold flaky Wi-Fi
connections far more reliably as a sequence of short GETs with their own retry/backoff than as
one long-lived socket, and polling means a dropped connection self-heals on the next tick
instead of needing explicit reconnect logic on both ends.

Every read here is an MCP Resource fetch (informational, no CDG). Every write - a tool call or
an approval decision - still goes through TauCoreHost.call_tool or the approval queue exactly
as it would from the LLM path, so serving the browser instead of the model does not create a
side door around the Core Directive Guard.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from tau_core import crypto_store
from tau_core.config import TauCoreSettings
from tau_core.host import TauCoreHost
from tau_core.llm.agent import TauAssistant
from tau_core.scheduler import ProposingScheduler, cve_job
from tau_core.web.deps import SharedDeps
from tau_core.web.devices import DeviceRegistry
from tau_core.web.identity import ChallengeStore
from tau_core.web.ratelimit import RateLimiter
from tau_core.web.routers.admin import build_admin_router
from tau_core.web.routers.approvals import build_approvals_router
from tau_core.web.routers.chat import build_chat_router
from tau_core.web.routers.devices import build_devices_router
from tau_core.web.routers.system import build_system_router
from tau_core.web.routers.tools import _TOOLS_BLOCKED_FROM_PASSTHROUGH, build_tools_router  # noqa: F401 - re-exported, test_toolset.py imports this constant from here
from tau_core.web.routers.voice import build_voice_router

logger = logging.getLogger(__name__)


def create_app(
    settings: TauCoreSettings | None = None,
    host: TauCoreHost | None = None,
    assistant: TauAssistant | None = None,
) -> FastAPI:
    """Builds the FastAPI app. Pass `host` directly in tests to inject a pre-wired
    TauCoreHost (e.g. pointed at mock MCP servers) instead of connecting the real registry.
    Pass `assistant` to skip /api/chat's lazy from_settings() construction - e.g. a
    FunctionModel-backed TauAssistant in tests, so /api/chat can be exercised without a real
    Ollama instance (same pattern test_llm_agent.py uses for TauAssistant itself).
    """
    settings = settings or TauCoreSettings()

    if host is None:
        host = TauCoreHost.from_settings(settings)
        manager = host.mcp
        owns_manager = True
    else:
        manager = host.mcp
        owns_manager = False

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if owns_manager:
            async with manager:
                # Partial-degradation boot (Phase 7 Tier 0): connect_all reports which servers
                # did not come up rather than raising. A host that refuses to start because one
                # domain server is broken is a worse failure than one that starts without that
                # server's tools - and the tools come back on their own, since the call path
                # reconnects on demand.
                failed = await manager.connect_all()
                if failed:
                    logger.warning(
                        "Bridge starting with %d MCP server(s) unavailable: %s",
                        len(failed),
                        ", ".join(f"{name} ({exc!r})" for name, exc in sorted(failed.items())),
                    )
                scheduler = None
                if settings.scheduler_enabled:
                    # Phase 10.3 #10 - see tau_core.scheduler for what "proposing" means and why
                    # it's safe to default on. Only started when this app owns the MCP manager's
                    # lifecycle (real deployments); tests that inject a pre-wired host manage
                    # their own lifecycle and don't want a background task outliving the test.
                    scheduler = ProposingScheduler(host, [cve_job()])
                    scheduler.start()
                mdns = None
                if settings.tls_enabled:
                    # Phase 27.D Milestone 3: advertise the TLS listener (uvicorn.run in
                    # web/__main__.py starts it separately, alongside this plain-HTTP one) so the
                    # desktop client can find it via mDNS instead of a human typing an IP. mDNS
                    # start failing (e.g. no multicast route on this network) must not take the
                    # whole bridge down over a discovery nicety - caught broadly (OSError plus
                    # zeroconf's own exception base) since a live test showed the synchronous
                    # zeroconf API alone can raise EventLoopBlocked from inside a running loop;
                    # discovery.py now uses AsyncZeroconf specifically to avoid that, but this
                    # stays defensive against whatever else a real network can throw.
                    try:
                        from tau_core import discovery

                        mdns = await discovery.start_advertising(settings.tls_port)
                    except Exception:
                        logger.exception("mDNS advertisement failed to start; discovery disabled")
                try:
                    yield
                finally:
                    if scheduler is not None:
                        await scheduler.stop()
                    if mdns is not None:
                        await mdns.async_close()
        else:
            # Caller (e.g. a test) manages the manager's lifecycle itself.
            yield

    app = FastAPI(title="Tau Core Bridge", lifespan=lifespan)
    app.state.host = host

    # Lazily constructed on first /api/chat call, not at app startup: TauAssistant.from_settings
    # requires OLLAMA_HOST/OLLAMA_MODEL to be configured, and a bridge that otherwise works fine
    # (resources, approvals, direct tool calls) shouldn't refuse to start just because Ollama
    # isn't reachable yet. Cached after the first successful build so we're not reconnecting a
    # fresh PydanticAI agent on every message.
    assistant_holder: dict[str, TauAssistant] = {}
    if assistant is not None:
        assistant_holder["assistant"] = assistant

    challenges = ChallengeStore()
    # Volume-backed by default (TAU_DEVICE_STORE_PATH), so an admin-approved device's token
    # survives a bridge restart instead of forcing re-approval on every deploy - Phase 27.A.
    # Encrypted at rest when TAU_MASTER_KEY is set, same as the approval queue in host.py.
    device_encryption_key = (
        crypto_store.resolve_key(settings.device_store_path)
        if settings.device_store_path is not None
        else None
    )
    devices = DeviceRegistry(
        store_path=settings.device_store_path,
        encryption_key=device_encryption_key,
    )
    # Phase 7 Tier 1 #9: neither endpoint group requires a credential, and each call is
    # expensive, so both get a per-client budget. See TauCoreSettings for why voice's default is
    # much higher than chat's (continuous wake-word polling vs. occasional human turns).
    chat_limiter = RateLimiter(settings.chat_rate_limit_max, settings.chat_rate_limit_window_seconds)
    voice_limiter = RateLimiter(settings.voice_rate_limit_max, settings.voice_rate_limit_window_seconds)

    # Phase 49: everything create_app() used to close over for _client_key/_device_id/
    # _require_admin/_tool_json, now threaded explicitly - see web/deps.py.
    deps = SharedDeps(
        settings=settings,
        host=host,
        devices=devices,
        challenges=challenges,
        chat_limiter=chat_limiter,
        voice_limiter=voice_limiter,
        assistant_holder=assistant_holder,
    )

    # Kiosk tablets on the home LAN hit this from whatever origin the frontend is served on, so
    # the default admits any private/LAN origin by regex rather than naming them (see
    # TauCoreSettings.allowed_origins and LAN_ORIGIN_REGEX).
    #
    # This used to be `allow_origins=["*"]`, justified as "behind the core-agent VLAN, not
    # internet-facing". The VLAN argument holds for packets and not for browsers: most endpoints
    # here have no auth, so `*` let any public page a household browser visited script that
    # browser into reading /api/approvals and /api/activity and POSTing to /api/chat and
    # /api/tools. The browser is inside the network even when the attacker is not.
    #
    # Note what this is not: a CORS policy binds browsers only. Anything that can reach the port
    # directly - a script on the LAN, a compromised device - is unaffected, and network
    # segmentation stays the actual perimeter.
    allow_origins, allow_origin_regex = settings.cors_origin_config()
    if allow_origins == ["*"]:
        logger.warning(
            "TAU_ALLOWED_ORIGINS is '*': any website a browser on this network visits can call "
            "this bridge, and most endpoints here are unauthenticated. Set it to your kiosk "
            "origins, or leave it unset for the LAN-only default."
        )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins,
        allow_origin_regex=allow_origin_regex,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Phase 27.A: the open-by-default state used to be silent - nothing told an admin that any
    # LAN client (or, once 27.C ships, anything that reaches port 8000 from outside it) could use
    # every device-aware route with no approval at all. Loud now, on both channels an operator
    # might actually be watching: container logs at boot, and /api/health's new field for the
    # dashboard banner below.
    if not settings.require_device_token:
        logger.warning(
            "TAU_REQUIRE_DEVICE_TOKEN is off: any identified device is accepted with no admin "
            "approval on every device-aware route. Fine for a fresh install with nothing to "
            "protect yet; approve your devices and set this once real devices are in use."
        )

    app.include_router(build_system_router(deps))

    app.include_router(build_devices_router(deps))

    app.include_router(build_admin_router(deps))

    app.include_router(build_approvals_router(deps))
    app.include_router(build_tools_router(deps))

    app.include_router(build_voice_router(deps))
    app.include_router(build_chat_router(deps))

    return app
