"""Phase 16 admin portal hardening: the voice token moved off the URL/JSON body onto the
X-Tau-Voice-Token header, three previously-ungated admin panels (system, people, activity) got
a real gate, and device revocation went from a bare field to something actually enforced.

Same fake-transport style as test_voice_identity.py: the CDG, approval queue, and bridge
orchestration all run for real; only the MCP transport is canned.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from httpx import ASGITransport
from mcp.types import CallToolResult, TextContent

from tau_core.config import TauCoreSettings
from tau_core.host import TauCoreHost
from tau_core.web.devices import DeviceRegistry
from tau_core.web.server import create_app

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"

EMBEDDING = [0.1, 0.2, 0.3, 0.4]


class FakeManager:
    """Canned JSON per (server, tool) - see test_voice_identity.py for the same pattern."""

    def __init__(self):
        self.calls = []
        self.responses = {
            ("voice-mcp-server", "transcribe"): "placeholder",
            ("voice-mcp-server", "identify_speaker"): {"embedding": EMBEDDING, "embedding_dim": 4},
            ("memory-mcp-server", "match_voice"): [{"person_id": "zion", "distance": 0.3}],
            ("memory-mcp-server", "get_person_profile"): {"person_id": "zion", "access_level": "admin"},
            ("utility-mcp-server", "get_system_status"): {
                "current_model": "qwen2.5:7b-instruct",
                "hardware": {"cpu_cores": 8, "gpu_vram_mb": 8192},
            },
            ("memory-mcp-server", "list_people"): {
                "people": [{"person_id": "zion", "access_level": "admin", "face_count": 1, "voice_count": 1}]
            },
        }

    def connected_servers(self):
        return ["voice-mcp-server", "memory-mcp-server", "utility-mcp-server"]

    async def read_resource(self, server, uri):
        # /api/state's ui-bridge read - a minimal stand-in, only used by the device-revocation
        # tests below, which care about _device_id()'s 403 firing before this is even reached.
        return SimpleNamespace(contents=[SimpleNamespace(text=json.dumps({"security": {}}))])

    async def call_tool(self, server, tool, arguments):
        self.calls.append((server, tool, arguments))
        payload = self.responses[(server, tool)]
        if isinstance(payload, list):
            content = [TextContent(type="text", text=json.dumps(item)) for item in payload]
        elif isinstance(payload, str):
            content = [TextContent(type="text", text=payload)]
        else:
            content = [TextContent(type="text", text=json.dumps(payload))]
        return CallToolResult(content=content)


def make_app(
    require_voice_approval=False, require_device_token=False, admin_service_token=None, settings=None
):
    """`settings` lets a test pre-build (and act on, e.g. approve a device against
    `settings.device_store_path`) the TauCoreSettings before the app reads it - otherwise one is
    built fresh from the two convenience flags, as before."""
    settings = settings or TauCoreSettings(
        cdg_rules_path=CONFIG_DIR / "cdg_rules.yaml",
        require_voice_approval=require_voice_approval,
        require_device_token=require_device_token,
        admin_service_token=admin_service_token,
    )
    manager = FakeManager()
    host = TauCoreHost(manager, settings=settings)
    app = create_app(settings=settings, host=host)
    return app, host, manager


def client_for(app):
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _admin_token(client, manager, access_level="admin"):
    manager.responses[("memory-mcp-server", "get_person_profile")] = {
        "person_id": "zion", "access_level": access_level
    }
    challenge = (await client.post("/api/voice/challenge")).json()
    manager.responses[("voice-mcp-server", "transcribe")] = challenge["phrase"]
    verify = (
        await client.post(
            f"/api/voice/challenge/{challenge['challenge_id']}/verify", json={"audio_b64": "QUJD"}
        )
    ).json()
    assert verify["verified"] is True
    return verify["voice_token"]


# --- token transport: header, not query/body ---------------------------------------------------


async def test_devices_admin_read_accepts_header_token_when_voice_on():
    app, _host, manager = make_app(require_voice_approval=True)
    async with client_for(app) as client:
        no_token = await client.get("/api/devices")
        assert no_token.status_code == 428

        token = await _admin_token(client, manager)
        ok = await client.get("/api/devices", headers={"X-Tau-Voice-Token": token})
        assert ok.status_code == 200

        # A query-string token is no longer honored - the whole point of the move.
        query_only = await client.get(f"/api/devices?voice_token={token}")
        assert query_only.status_code == 428


async def test_approve_action_accepts_header_not_body():
    app, host, manager = make_app(require_voice_approval=True)
    pending = host.approvals.submit(
        server="fabrication-mcp-server", tool="submit_print_job", arguments={},
        reason="test", requested_by="test",
    )
    async with client_for(app) as client:
        token = await _admin_token(client, manager, access_level="standard")
        # A token in the body is ignored now - must 428.
        body_only = await client.post(
            f"/api/approvals/{pending.id}/approve",
            json={"decided_by": "tablet", "voice_token": token},
        )
        assert body_only.status_code == 428


# --- newly-gated admin endpoints ----------------------------------------------------------------


async def test_admin_system_gated_and_shaped():
    app, _host, manager = make_app(require_voice_approval=True)
    async with client_for(app) as client:
        gated = await client.get("/api/admin/system")
        assert gated.status_code == 428

        token = await _admin_token(client, manager)
        ok = await client.get("/api/admin/system", headers={"X-Tau-Voice-Token": token})
        assert ok.status_code == 200
        assert ok.json()["current_model"] == "qwen2.5:7b-instruct"


async def test_admin_system_lan_trust_when_voice_off():
    app, _host, _manager = make_app()
    async with client_for(app) as client:
        r = await client.get("/api/admin/system")
    assert r.status_code == 200
    assert r.json()["hardware"]["cpu_cores"] == 8


async def test_admin_people_gated_and_shaped():
    app, _host, manager = make_app(require_voice_approval=True)
    async with client_for(app) as client:
        gated = await client.get("/api/admin/people")
        assert gated.status_code == 428

        token = await _admin_token(client, manager)
        ok = await client.get("/api/admin/people", headers={"X-Tau-Voice-Token": token})
        assert ok.status_code == 200
        assert ok.json()["people"][0]["person_id"] == "zion"


async def test_admin_security_gated_and_shaped():
    app, _host, manager = make_app(require_voice_approval=True)
    async with client_for(app) as client:
        gated = await client.get("/api/admin/security")
        assert gated.status_code == 428

        token = await _admin_token(client, manager)
        ok = await client.get("/api/admin/security", headers={"X-Tau-Voice-Token": token})
        assert ok.status_code == 200
        body = ok.json()
        assert body["require_voice_approval"] is True
        assert isinstance(body["voice_match_max_distance"], float)
        assert any(r["tool"] == "exit_lockdown" for r in body["tier_policy"])


async def test_admin_security_reports_lan_trust_posture_when_off():
    app, _host, _manager = make_app()  # require_voice_approval defaults False
    async with client_for(app) as client:
        r = await client.get("/api/admin/security")
    assert r.status_code == 200
    assert r.json()["require_voice_approval"] is False


async def test_activity_now_admin_gated():
    app, _host, manager = make_app(require_voice_approval=True)
    async with client_for(app) as client:
        gated = await client.get("/api/activity")
        assert gated.status_code == 428

        token = await _admin_token(client, manager)
        ok = await client.get("/api/activity", headers={"X-Tau-Voice-Token": token})
        assert ok.status_code == 200


async def test_activity_lan_trust_when_voice_off():
    app, _host, _manager = make_app()
    async with client_for(app) as client:
        r = await client.get("/api/activity")
    assert r.status_code == 200


# --- Phase 38: tau-admin-server's service-token credential ---------------------------------------


async def test_service_token_satisfies_admin_gate_even_when_voice_required():
    """The standalone admin backend authenticates its own humans with a password login, then
    calls this bridge with a shared secret instead of a voice token - either should work."""
    app, _host, manager = make_app(require_voice_approval=True, admin_service_token="shhh-secret")
    async with client_for(app) as client:
        no_creds = await client.get("/api/admin/system")
        assert no_creds.status_code == 428

        ok = await client.get(
            "/api/admin/system", headers={"X-Tau-Admin-Service-Token": "shhh-secret"}
        )
        assert ok.status_code == 200
        assert ok.json()["current_model"] == "qwen2.5:7b-instruct"
        # Identity is proved by the shared secret, not a voice/memory round trip - only the
        # actual data fetch (get_system_status) happened, no identify_speaker/match_voice calls.
        assert manager.calls == [("utility-mcp-server", "get_system_status", {})]


async def test_wrong_service_token_falls_through_to_voice_requirement():
    app, _host, manager = make_app(require_voice_approval=True, admin_service_token="shhh-secret")
    async with client_for(app) as client:
        wrong = await client.get(
            "/api/admin/system", headers={"X-Tau-Admin-Service-Token": "guessed"}
        )
        assert wrong.status_code == 428

        token = await _admin_token(client, manager)
        ok = await client.get("/api/admin/system", headers={"X-Tau-Voice-Token": token})
        assert ok.status_code == 200


async def test_service_token_works_even_with_voice_approval_off():
    """LAN-trust already lets anything through when require_voice_approval is off, so this is
    mostly confirming the service-token path doesn't error in that mode - not a meaningful
    security boundary either way."""
    app, _host, _manager = make_app(admin_service_token="shhh-secret")
    async with client_for(app) as client:
        ok = await client.get(
            "/api/admin/system", headers={"X-Tau-Admin-Service-Token": "shhh-secret"}
        )
    assert ok.status_code == 200


async def test_unset_service_token_setting_ignores_the_header_entirely():
    """TAU_ADMIN_SERVICE_TOKEN unset (the default) must not accept a blank/empty header as a
    match - guards against hmac.compare_digest("", "") or similar accidental True."""
    app, _host, manager = make_app(require_voice_approval=True)  # admin_service_token=None
    async with client_for(app) as client:
        empty_header = await client.get(
            "/api/admin/system", headers={"X-Tau-Admin-Service-Token": ""}
        )
        assert empty_header.status_code == 428

        token = await _admin_token(client, manager)
        ok = await client.get("/api/admin/system", headers={"X-Tau-Voice-Token": token})
        assert ok.status_code == 200


# --- device revocation, actually enforced --------------------------------------------------------


async def test_blocked_device_is_refused_at_chat_and_state():
    app, _host, manager = make_app()
    async with client_for(app) as client:
        # A brand-new device just talking - fine.
        first = await client.get("/api/state", headers={"X-Tau-Device-Id": "kids-tablet"})
        assert first.status_code == 200

        block = await client.post("/api/admin/devices/kids-tablet/block")
        assert block.status_code == 200
        assert block.json()["blocked"] is True

        blocked = await client.get("/api/state", headers={"X-Tau-Device-Id": "kids-tablet"})
        assert blocked.status_code == 403

        unblock = await client.post("/api/admin/devices/kids-tablet/unblock")
        assert unblock.status_code == 200
        assert unblock.json()["blocked"] is False

        restored = await client.get("/api/state", headers={"X-Tau-Device-Id": "kids-tablet"})
        assert restored.status_code == 200


async def test_anonymous_caller_unaffected_by_any_block():
    app, _host, _manager = make_app()
    async with client_for(app) as client:
        r = await client.get("/api/state")
    assert r.status_code == 200


async def test_block_unknown_device_is_404():
    app, _host, _manager = make_app()
    async with client_for(app) as client:
        r = await client.post("/api/admin/devices/never-seen/block")
    assert r.status_code == 404


async def test_block_endpoint_requires_admin_when_voice_on():
    app, _host, manager = make_app(require_voice_approval=True)
    async with client_for(app) as client:
        await client.get("/api/state", headers={"X-Tau-Device-Id": "devA"})
        gated = await client.post("/api/admin/devices/devA/block")
        assert gated.status_code == 428

        token = await _admin_token(client, manager)
        ok = await client.post(
            "/api/admin/devices/devA/block", headers={"X-Tau-Voice-Token": token}
        )
        assert ok.status_code == 200


# --- Phase 27.A step 5: the admin-approve endpoint ----------------------------------------------


async def test_approve_device_returns_raw_token_once():
    app, _host, _manager = make_app()
    async with client_for(app) as client:
        await client.get("/api/state", headers={"X-Tau-Device-Id": "kids-tablet"})
        r = await client.post("/api/admin/devices/kids-tablet/approve")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "approved"
        assert isinstance(body["token"], str) and len(body["token"]) > 20
        assert "token_hash" not in body  # as_dict() strips it - see devices.py


async def test_approve_unknown_device_is_404():
    app, _host, _manager = make_app()
    async with client_for(app) as client:
        r = await client.post("/api/admin/devices/never-seen/approve")
    assert r.status_code == 404


async def test_approve_endpoint_requires_admin_when_voice_on():
    app, _host, manager = make_app(require_voice_approval=True)
    async with client_for(app) as client:
        await client.get("/api/state", headers={"X-Tau-Device-Id": "devA"})
        gated = await client.post("/api/admin/devices/devA/approve")
        assert gated.status_code == 428

        token = await _admin_token(client, manager)
        ok = await client.post(
            "/api/admin/devices/devA/approve", headers={"X-Tau-Voice-Token": token}
        )
        assert ok.status_code == 200
        assert "token" in ok.json()


# --- Phase 27.A step 3: device-token enforcement wired into the routes _device_id() didn't -----
# previously reach (admin/*, activity, approvals list, and - spot-checked, since the wiring is
# identical everywhere - one representative /api/voice/* route). The chokepoint logic itself
# (pending/approved/wrong-token/blocked) is already fully covered in test_devices.py and
# test_web_devices.py's step-2 tests; these just confirm each route actually calls it.


def _approve_device(settings: TauCoreSettings, device_id: str) -> str:
    """Mint a token out of band, against the same store the app-under-test will load from - the
    admin-approve HTTP route doesn't exist yet (step 5)."""
    registry = DeviceRegistry(store_path=settings.device_store_path)
    registry.touch(device_id)
    return registry.approve(device_id)


def _settings_with_device_token(**overrides) -> TauCoreSettings:
    return TauCoreSettings(
        cdg_rules_path=CONFIG_DIR / "cdg_rules.yaml", require_device_token=True, **overrides
    )


async def test_admin_system_requires_device_token_when_enforcement_on():
    settings = _settings_with_device_token()
    token = _approve_device(settings, "devA")
    app, _host, _manager = make_app(settings=settings)
    async with client_for(app) as client:
        pending = await client.get(
            "/api/admin/system", headers={"X-Tau-Device-Id": "unapproved-device"}
        )
        assert pending.status_code == 403

        ok = await client.get(
            "/api/admin/system",
            headers={"X-Tau-Device-Id": "devA", "X-Tau-Device-Token": token},
        )
        assert ok.status_code == 200


async def test_activity_requires_device_token_when_enforcement_on():
    settings = _settings_with_device_token()
    token = _approve_device(settings, "devA")
    app, _host, _manager = make_app(settings=settings)
    async with client_for(app) as client:
        pending = await client.get("/api/activity", headers={"X-Tau-Device-Id": "unapproved"})
        assert pending.status_code == 403

        ok = await client.get(
            "/api/activity", headers={"X-Tau-Device-Id": "devA", "X-Tau-Device-Token": token}
        )
        assert ok.status_code == 200


async def test_approvals_list_requires_device_token_when_enforcement_on():
    """/api/approvals has no _require_admin gate at all (the kiosk's own card reads it) - device
    token is the only enforcement available to it."""
    settings = _settings_with_device_token()
    token = _approve_device(settings, "devA")
    app, _host, _manager = make_app(settings=settings)
    async with client_for(app) as client:
        pending = await client.get("/api/approvals", headers={"X-Tau-Device-Id": "unapproved"})
        assert pending.status_code == 403

        ok = await client.get(
            "/api/approvals", headers={"X-Tau-Device-Id": "devA", "X-Tau-Device-Token": token}
        )
        assert ok.status_code == 200


async def test_block_endpoint_also_requires_device_token_when_enforcement_on():
    settings = _settings_with_device_token()
    token = _approve_device(settings, "devA")
    app, _host, _manager = make_app(settings=settings)
    async with client_for(app) as client:
        await client.get(
            "/api/state", headers={"X-Tau-Device-Id": "targetDevice"}
        )  # give it something to block
        pending = await client.post(
            "/api/admin/devices/targetDevice/block",
            headers={"X-Tau-Device-Id": "unapproved"},
        )
        assert pending.status_code == 403

        ok = await client.post(
            "/api/admin/devices/targetDevice/block",
            headers={"X-Tau-Device-Id": "devA", "X-Tau-Device-Token": token},
        )
        assert ok.status_code == 200


async def test_voice_challenge_requires_device_token_when_enforcement_on():
    """Spot-check for the whole /api/voice/* family - the wiring is the same one-line
    `_device_id(x_tau_device_id, x_tau_device_token)` call in every one of them."""
    settings = _settings_with_device_token()
    token = _approve_device(settings, "devA")
    app, _host, _manager = make_app(settings=settings)
    async with client_for(app) as client:
        pending = await client.post(
            "/api/voice/challenge", headers={"X-Tau-Device-Id": "unapproved"}
        )
        assert pending.status_code == 403

        ok = await client.post(
            "/api/voice/challenge",
            headers={"X-Tau-Device-Id": "devA", "X-Tau-Device-Token": token},
        )
        assert ok.status_code == 200


async def test_device_token_enforcement_off_by_default_leaves_these_routes_open():
    """TAU_REQUIRE_DEVICE_TOKEN still defaults False - step 3 must not change behaviour for
    anyone who hasn't opted in."""
    app, _host, _manager = make_app()  # both flags default False
    async with client_for(app) as client:
        r = await client.get("/api/activity", headers={"X-Tau-Device-Id": "never-approved"})
        assert r.status_code == 200
        r = await client.get("/api/approvals", headers={"X-Tau-Device-Id": "never-approved"})
        assert r.status_code == 200
