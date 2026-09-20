"""Phase 6.D end-to-end: per-device chat isolation and the admin-only unified log, exercised
against a real ui-bridge-mcp-server over stdio (not mocked), so the whole path - HTTP -> CDG ->
MCP transport -> ui-bridge state - is proven, the same way test_web_server.py does it.
"""

import sys
from pathlib import Path

import httpx
import pytest
from httpx import ASGITransport

from tau_core.config import TauCoreSettings
from tau_core.host import TauCoreHost
from tau_core.mcp_client import MCPClientManager, ServerConfig, ServerRegistry
from tau_core.web.devices import DeviceRegistry
from tau_core.web.server import create_app

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


def build_registry() -> ServerRegistry:
    return ServerRegistry(
        servers=[
            ServerConfig(
                name="ui-bridge-mcp-server",
                transport="stdio",
                command=sys.executable,
                args=["-m", "ui_bridge_mcp_server.server"],
            )
        ]
    )


def make_settings(**overrides) -> TauCoreSettings:
    return TauCoreSettings(cdg_rules_path=CONFIG_DIR / "cdg_rules.yaml", **overrides)


async def _seed(host: TauCoreHost, text: str, device_id: str) -> None:
    # update_transcription is now excluded from the generic /api/tools passthrough (Phase 41 -
    # tau_core.llm.toolset.MODEL_EXCLUDED_TOOLS), matching how it's actually populated in
    # production: tau-core's own web/server.py calls host.call_tool directly, never through the
    # passthrough. Seed the same way here rather than through HTTP.
    await host.call_tool(
        "ui-bridge-mcp-server",
        "update_transcription",
        {"text": text, "speaker": "user", "device_id": device_id},
        requested_by="test-seed",
    )


async def test_per_device_state_isolation():
    settings = make_settings(require_device_token=False)  # isolation, not enforcement, under test
    async with MCPClientManager(build_registry()) as manager:
        await manager.connect_all()
        host = TauCoreHost(manager, settings=settings)
        app = create_app(settings=settings, host=host)
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await _seed(host, "hi from A", "devA")
            await _seed(host, "hi from B", "devB")

            ra = await client.get("/api/state", headers={"X-Tau-Device-Id": "devA"})
            assert ra.status_code == 200
            texts_a = [e["text"] for e in ra.json()["transcription"]["history"]]
            assert texts_a == ["hi from A"]  # NOT "hi from B"

            rb = await client.get("/api/state", headers={"X-Tau-Device-Id": "devB"})
            texts_b = [e["text"] for e in rb.json()["transcription"]["history"]]
            assert texts_b == ["hi from B"]

            # No device id -> empty history (fail toward not leaking), other state still present.
            rn = await client.get("/api/state")
            assert rn.json()["transcription"]["history"] == []
            assert "security" in rn.json()


async def test_unified_transcript_lan_trust_when_voice_off():
    settings = make_settings()  # require_voice_approval defaults False
    async with MCPClientManager(build_registry()) as manager:
        await manager.connect_all()
        host = TauCoreHost(manager, settings=settings)
        app = create_app(settings=settings, host=host)
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await _seed(host, "hi from A", "devA")
            await _seed(host, "hi from B", "devB")
            r = await client.get("/api/transcript/unified")
            assert r.status_code == 200
            texts = {e["text"] for e in r.json()["history"]}
            assert {"hi from A", "hi from B"} <= texts


async def test_unified_transcript_requires_token_when_voice_on():
    settings = make_settings(require_voice_approval=True)
    async with MCPClientManager(build_registry()) as manager:
        await manager.connect_all()
        host = TauCoreHost(manager, settings=settings)
        app = create_app(settings=settings, host=host)
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/transcript/unified")
            assert r.status_code == 428  # no admin voice token


async def test_unified_transcript_blocked_from_generic_passthrough():
    settings = make_settings()
    async with MCPClientManager(build_registry()) as manager:
        await manager.connect_all()
        host = TauCoreHost(manager, settings=settings)
        app = create_app(settings=settings, host=host)
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # Even in LAN-trust mode the sensitive read is not reachable via /api/tools - it has
            # its own tier-gated endpoint, so the admin gate can't be sidestepped here.
            r = await client.post(
                "/api/tools/ui-bridge-mcp-server/get_unified_transcript", json={"arguments": {}}
            )
            assert r.status_code == 403


async def test_device_register_open_and_list_returns_it():
    settings = make_settings()  # voice off -> /api/devices is lan-trust readable
    async with MCPClientManager(build_registry()) as manager:
        await manager.connect_all()
        host = TauCoreHost(manager, settings=settings)
        app = create_app(settings=settings, host=host)
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            reg = await client.post("/api/devices/register", json={"device_id": "devA", "name": "Kitchen iPad"})
            assert reg.status_code == 200
            assert reg.json()["name"] == "Kitchen iPad"

            lst = await client.get("/api/devices")
            assert lst.status_code == 200
            assert any(d["device_id"] == "devA" and d["name"] == "Kitchen iPad" for d in lst.json())


async def test_admin_system_status_reachable_via_passthrough():
    """The admin dashboard's SYSTEM block reads utility-mcp-server.get_system_status through the
    generic /api/tools passthrough (it's read-only, not admin-gated). Prove that path returns
    parseable JSON with the model + hardware fields the dashboard renders."""
    import json as _json

    settings = make_settings()
    registry = ServerRegistry(
        servers=[
            ServerConfig(
                name="utility-mcp-server",
                transport="stdio",
                command=sys.executable,
                args=["-m", "utility_mcp_server.server"],
            )
        ]
    )
    async with MCPClientManager(registry) as manager:
        await manager.connect_all()
        host = TauCoreHost(manager, settings=settings)
        app = create_app(settings=settings, host=host)
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post(
                "/api/tools/utility-mcp-server/get_system_status", json={"arguments": {}}
            )
            assert r.status_code == 200
            payload = _json.loads(r.json()["result"])
            assert "hardware" in payload and "cpu_cores" in payload["hardware"]
            assert "current_model" in payload


async def test_device_list_requires_admin_when_voice_on():
    settings = make_settings(require_voice_approval=True)
    async with MCPClientManager(build_registry()) as manager:
        await manager.connect_all()
        host = TauCoreHost(manager, settings=settings)
        app = create_app(settings=settings, host=host)
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/devices")
            assert r.status_code == 428


# --- Phase 27.A step 2: TAU_REQUIRE_DEVICE_TOKEN enforcement in _device_id() -------------------


def test_device_token_enforcement_defaults_on():
    """Phase 48 regression guard: the bare default (no override, no env var) must stay True.
    §8.28 step 6 was the whole reason this flip waited from 2026-08-19 to 2026-09-16 - a silent
    revert back to False here would be exactly the kind of thing worth catching in CI, not in
    the household's actual assistant."""
    assert make_settings().require_device_token is True


async def test_device_token_enforcement_explicitly_off_pending_device_still_allowed():
    """Phase 48 flipped the default to True; this covers the still-supported opt-out
    (TAU_REQUIRE_DEVICE_TOKEN=false) for anyone not ready to approve every device yet."""
    settings = make_settings(require_device_token=False)
    async with MCPClientManager(build_registry()) as manager:
        await manager.connect_all()
        host = TauCoreHost(manager, settings=settings)
        app = create_app(settings=settings, host=host)
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/state", headers={"X-Tau-Device-Id": "devA"})
            assert r.status_code == 200


async def test_device_token_required_when_enforcement_on_and_device_pending():
    settings = make_settings(require_device_token=True)
    async with MCPClientManager(build_registry()) as manager:
        await manager.connect_all()
        host = TauCoreHost(manager, settings=settings)
        app = create_app(settings=settings, host=host)
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/state", headers={"X-Tau-Device-Id": "devA"})
            assert r.status_code == 403


async def test_device_token_accepted_when_approved_and_enforcement_on():
    settings = make_settings(require_device_token=True)
    # Approve out of band (the admin-approve HTTP route doesn't exist yet - step 3/5) by driving
    # a DeviceRegistry pointed at the same persisted store create_app will load from.
    registry = DeviceRegistry(store_path=settings.device_store_path)
    registry.touch("devA")
    token = registry.approve("devA")
    async with MCPClientManager(build_registry()) as manager:
        await manager.connect_all()
        host = TauCoreHost(manager, settings=settings)
        app = create_app(settings=settings, host=host)
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get(
                "/api/state", headers={"X-Tau-Device-Id": "devA", "X-Tau-Device-Token": token}
            )
            assert r.status_code == 200


async def test_device_token_rejected_when_wrong_and_enforcement_on():
    settings = make_settings(require_device_token=True)
    registry = DeviceRegistry(store_path=settings.device_store_path)
    registry.touch("devA")
    registry.approve("devA")
    async with MCPClientManager(build_registry()) as manager:
        await manager.connect_all()
        host = TauCoreHost(manager, settings=settings)
        app = create_app(settings=settings, host=host)
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get(
                "/api/state",
                headers={"X-Tau-Device-Id": "devA", "X-Tau-Device-Token": "wrong-token"},
            )
            assert r.status_code == 403


async def test_anonymous_caller_unaffected_by_device_token_enforcement():
    settings = make_settings(require_device_token=True)
    async with MCPClientManager(build_registry()) as manager:
        await manager.connect_all()
        host = TauCoreHost(manager, settings=settings)
        app = create_app(settings=settings, host=host)
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/state")  # no X-Tau-Device-Id at all
            assert r.status_code == 200


async def test_chat_endpoint_also_enforces_device_token():
    settings = make_settings(require_device_token=True)
    async with MCPClientManager(build_registry()) as manager:
        await manager.connect_all()
        host = TauCoreHost(manager, settings=settings)
        app = create_app(settings=settings, host=host)
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post(
                "/api/chat", json={"text": "hi"}, headers={"X-Tau-Device-Id": "devA"}
            )
            assert r.status_code == 403


async def test_blocked_device_refused_even_with_valid_token_and_enforcement_on():
    settings = make_settings(require_device_token=True)
    registry = DeviceRegistry(store_path=settings.device_store_path)
    registry.touch("devA")
    token = registry.approve("devA")
    registry.block("devA")  # in-memory only (block() doesn't persist) - see devices.py
    async with MCPClientManager(build_registry()) as manager:
        await manager.connect_all()
        host = TauCoreHost(manager, settings=settings)
        app = create_app(settings=settings, host=host)
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # The app's own DeviceRegistry loaded the approved+token state from disk but never
            # saw the in-memory-only block() above, so devA loads as approved. Block it again
            # through this app's own registry via the existing admin endpoint instead.
            block_resp = await client.post("/api/admin/devices/devA/block")
            assert block_resp.status_code == 200
            r = await client.get(
                "/api/state", headers={"X-Tau-Device-Id": "devA", "X-Tau-Device-Token": token}
            )
            assert r.status_code == 403
            assert "revoked" in r.json()["detail"].lower()


# --- Phase 27.A step 3: device-token enforcement on the routes that only real MCP servers can
# exercise end-to-end (unified transcript, generic tool passthrough) -----------------------------


async def test_unified_transcript_requires_device_token_when_enforcement_on():
    settings = make_settings(require_device_token=True)
    registry = DeviceRegistry(store_path=settings.device_store_path)
    registry.touch("devA")
    token = registry.approve("devA")
    async with MCPClientManager(build_registry()) as manager:
        await manager.connect_all()
        host = TauCoreHost(manager, settings=settings)
        app = create_app(settings=settings, host=host)
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            pending = await client.get(
                "/api/transcript/unified", headers={"X-Tau-Device-Id": "unapproved"}
            )
            assert pending.status_code == 403

            ok = await client.get(
                "/api/transcript/unified",
                headers={"X-Tau-Device-Id": "devA", "X-Tau-Device-Token": token},
            )
            assert ok.status_code == 200


async def test_tools_passthrough_requires_device_token_when_enforcement_on():
    settings = make_settings(require_device_token=True)
    registry = DeviceRegistry(store_path=settings.device_store_path)
    registry.touch("devA")
    token = registry.approve("devA")
    async with MCPClientManager(build_registry()) as manager:
        await manager.connect_all()
        host = TauCoreHost(manager, settings=settings)
        app = create_app(settings=settings, host=host)
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # update_transcription itself is no longer reachable via this passthrough at all
            # (Phase 41 - excluded from the model's toolset AND the generic passthrough it shares
            # a block-list with), so this uses get_device_transcript - still passthrough-reachable
            # - purely as a vehicle to exercise the device-token gate on this route.
            pending = await client.post(
                "/api/tools/ui-bridge-mcp-server/get_device_transcript",
                json={"arguments": {"device_id": "unapproved"}},
                headers={"X-Tau-Device-Id": "unapproved"},
            )
            assert pending.status_code == 403

            ok = await client.post(
                "/api/tools/ui-bridge-mcp-server/get_device_transcript",
                json={"arguments": {"device_id": "devA"}},
                headers={"X-Tau-Device-Id": "devA", "X-Tau-Device-Token": token},
            )
            assert ok.status_code == 200
