"""Every proxied route: requires a session, forwards to TauCoreProxy, and turns a TauCoreError
into the matching HTTP status. TauCoreProxy itself is faked here (no real tau-core) - its own
header-construction/error-mapping logic is exercised in test_proxy.py instead.
"""

import httpx
import pytest
from httpx import ASGITransport

from tau_admin_server.proxy import TauCoreError
from tau_admin_server.server import create_app
from tau_admin_server.settings import AdminServerSettings


class FakeProxy:
    def __init__(self):
        self.calls = []

    async def list_devices(self):
        self.calls.append(("list_devices",))
        return [{"device_id": "kids-tablet"}]

    async def approve_device(self, device_id):
        self.calls.append(("approve_device", device_id))
        return {"status": "approved", "token": "raw-token"}

    async def block_device(self, device_id):
        self.calls.append(("block_device", device_id))
        return {"blocked": True}

    async def unblock_device(self, device_id):
        self.calls.append(("unblock_device", device_id))
        return {"blocked": False}

    async def remove_device(self, device_id):
        self.calls.append(("remove_device", device_id))
        return {"device_id": device_id, "removed": True}

    async def system_snapshot(self):
        self.calls.append(("system_snapshot",))
        return {"current_model": "qwen2.5:7b-instruct"}

    async def people_snapshot(self):
        self.calls.append(("people_snapshot",))
        return {"people": [{"person_id": "zion"}]}

    async def security_snapshot(self):
        self.calls.append(("security_snapshot",))
        return {"require_voice_approval": True}

    async def unified_transcript(self):
        self.calls.append(("unified_transcript",))
        return {"history": []}

    async def list_drafts(self):
        self.calls.append(("list_drafts",))
        # A bare list, matching tau-core's real /api/drafts shape (list[dict], not {"drafts": []}).
        return [{"id": "node1", "title": "likes tea", "content": "prefers green tea", "owner": ""}]

    async def promote_draft(self, node_id, approval_request_id=None):
        self.calls.append(("promote_draft", node_id, approval_request_id))
        if approval_request_id is None:
            return {"status": "pending_approval", "approval_request_id": "abc123"}
        return {"status": "ok"}

    async def discard_draft(self, node_id):
        self.calls.append(("discard_draft", node_id))
        return {"status": "ok"}

    async def list_wake_words(self):
        self.calls.append(("list_wake_words",))
        return [{"id": "hey_tau", "current": True}]

    async def set_wake_word(self, wake_id, approval_request_id=None):
        self.calls.append(("set_wake_word", wake_id, approval_request_id))
        return {"status": "ok", "wake_id": wake_id}

    async def list_voices(self):
        self.calls.append(("list_voices",))
        return [{"id": "en_US-lessac-medium", "current": True, "available": True}]

    async def set_voice(self, voice_id, approval_request_id=None):
        self.calls.append(("set_voice", voice_id, approval_request_id))
        return {"status": "ok", "voice_id": voice_id}

    async def activity_feed(self, limit=50):
        self.calls.append(("activity_feed", limit))
        return [{"server": "home-assistant-mcp-server", "tool": "call_service", "outcome": "executed"}]

    async def set_access_level(self, person_id, access_level, approval_request_id=None):
        self.calls.append(("set_access_level", person_id, access_level, approval_request_id))
        if approval_request_id is None:
            return {"status": "pending_approval", "approval_request_id": "xyz789"}
        return {"status": "ok"}

    async def list_approvals(self):
        self.calls.append(("list_approvals",))
        return [{"id": "req1", "server": "home-assistant-mcp-server", "tool": "call_service"}]

    async def approve_action(self, request_id, decided_by, note=None):
        self.calls.append(("approve_action", request_id, decided_by, note))
        return {"id": request_id, "status": "approved", "decided_by": decided_by}

    async def deny_action(self, request_id, decided_by, note=None):
        self.calls.append(("deny_action", request_id, decided_by, note))
        return {"id": request_id, "status": "denied", "decided_by": decided_by}


class FailingProxy(FakeProxy):
    async def system_snapshot(self):
        raise TauCoreError(428, "Admin voice verification required")


def make_app(tmp_path, proxy=None):
    settings = AdminServerSettings(
        credential_store_path=tmp_path / "credential.json",
        session_store_path=tmp_path / "sessions.json",
        bootstrap_username="zion",
        bootstrap_password="correct horse battery staple",
    )
    return create_app(settings=settings, proxy=proxy or FakeProxy())


def client_for(app):
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _login(client):
    r = await client.post(
        "/login", json={"username": "zion", "password": "correct horse battery staple"}
    )
    return {"Authorization": f"Bearer {r.json()['token']}"}


async def test_every_proxied_route_requires_a_session(tmp_path):
    app = make_app(tmp_path)
    async with client_for(app) as client:
        for method, path in [
            ("GET", "/api/devices"),
            ("POST", "/api/admin/devices/x/approve"),
            ("POST", "/api/admin/devices/x/block"),
            ("POST", "/api/admin/devices/x/unblock"),
            ("GET", "/api/admin/system"),
            ("GET", "/api/admin/people"),
            ("GET", "/api/admin/security"),
            ("GET", "/api/transcript/unified"),
            ("GET", "/api/drafts"),
            ("POST", "/api/drafts/x/promote"),
            ("POST", "/api/drafts/x/discard"),
            ("GET", "/api/voice/wake-words"),
            ("POST", "/api/voice/wake-words"),
            ("GET", "/api/voice/voices"),
            ("POST", "/api/voice/voices"),
            ("GET", "/api/activity"),
            ("POST", "/api/people/x/access-level"),
            ("GET", "/api/approvals"),
            ("POST", "/api/approvals/x/approve"),
            ("POST", "/api/approvals/x/deny"),
        ]:
            r = await client.request(method, path, json={} if method == "POST" else None)
            assert r.status_code == 401, f"{method} {path} should require auth, got {r.status_code}"


async def test_devices_roundtrip(tmp_path):
    proxy = FakeProxy()
    app = make_app(tmp_path, proxy=proxy)
    async with client_for(app) as client:
        headers = await _login(client)
        listed = await client.get("/api/devices", headers=headers)
        assert listed.status_code == 200
        assert listed.json() == [{"device_id": "kids-tablet"}]

        approved = await client.post("/api/admin/devices/kids-tablet/approve", headers=headers)
        assert approved.status_code == 200
        assert approved.json()["token"] == "raw-token"

        blocked = await client.post("/api/admin/devices/kids-tablet/block", headers=headers)
        assert blocked.json()["blocked"] is True

        unblocked = await client.post("/api/admin/devices/kids-tablet/unblock", headers=headers)
        assert unblocked.json()["blocked"] is False

        removed = await client.delete("/api/admin/devices/kids-tablet", headers=headers)
        assert removed.status_code == 200
        assert removed.json() == {"device_id": "kids-tablet", "removed": True}

    assert ("approve_device", "kids-tablet") in proxy.calls
    assert ("block_device", "kids-tablet") in proxy.calls
    assert ("unblock_device", "kids-tablet") in proxy.calls
    assert ("remove_device", "kids-tablet") in proxy.calls


async def test_snapshots_roundtrip(tmp_path):
    proxy = FakeProxy()
    app = make_app(tmp_path, proxy=proxy)
    async with client_for(app) as client:
        headers = await _login(client)
        assert (await client.get("/api/admin/system", headers=headers)).json()["current_model"] == "qwen2.5:7b-instruct"
        assert (await client.get("/api/admin/people", headers=headers)).json()["people"][0]["person_id"] == "zion"
        assert (await client.get("/api/admin/security", headers=headers)).json()["require_voice_approval"] is True
        assert (await client.get("/api/transcript/unified", headers=headers)).json() == {"history": []}


async def test_list_drafts_returns_a_bare_array(tmp_path):
    """Regression guard: tau-core's /api/drafts returns list[dict], not {"drafts": [...]} - a
    server.py return-type annotation mismatch here would 500 instead of serializing at all."""
    app = make_app(tmp_path)
    async with client_for(app) as client:
        headers = await _login(client)
        r = await client.get("/api/drafts", headers=headers)
    assert r.status_code == 200
    assert isinstance(r.json(), list)
    assert r.json()[0]["id"] == "node1"


async def test_draft_promote_two_stage_continuation(tmp_path):
    """Mirrors the wake-word continuation shape: the first call comes back pending_approval, the
    retry carries approval_request_id through to the proxy."""
    proxy = FakeProxy()
    app = make_app(tmp_path, proxy=proxy)
    async with client_for(app) as client:
        headers = await _login(client)
        first = await client.post("/api/drafts/node1/promote", json={}, headers=headers)
        assert first.json()["status"] == "pending_approval"

        second = await client.post(
            "/api/drafts/node1/promote", json={"approval_request_id": "abc123"}, headers=headers
        )
        assert second.json()["status"] == "ok"

    assert ("promote_draft", "node1", None) in proxy.calls
    assert ("promote_draft", "node1", "abc123") in proxy.calls


async def test_discard_draft(tmp_path):
    proxy = FakeProxy()
    app = make_app(tmp_path, proxy=proxy)
    async with client_for(app) as client:
        headers = await _login(client)
        r = await client.post("/api/drafts/node1/discard", headers=headers)
    assert r.status_code == 200
    assert ("discard_draft", "node1") in proxy.calls


async def test_wake_word_roundtrip(tmp_path):
    proxy = FakeProxy()
    app = make_app(tmp_path, proxy=proxy)
    async with client_for(app) as client:
        headers = await _login(client)
        listed = await client.get("/api/voice/wake-words", headers=headers)
        assert listed.json() == [{"id": "hey_tau", "current": True}]

        r = await client.post("/api/voice/wake-words", json={"wake_id": "hey_jarvis"}, headers=headers)
        assert r.json() == {"status": "ok", "wake_id": "hey_jarvis"}
    assert ("set_wake_word", "hey_jarvis", None) in proxy.calls


async def test_voice_roundtrip(tmp_path):
    """Same shape as the wake-word roundtrip above - both are household-wide voice settings behind
    the same session auth and the same CDG continuation."""
    proxy = FakeProxy()
    app = make_app(tmp_path, proxy=proxy)
    async with client_for(app) as client:
        headers = await _login(client)
        listed = await client.get("/api/voice/voices", headers=headers)
        assert listed.json() == [
            {"id": "en_US-lessac-medium", "current": True, "available": True}
        ]

        r = await client.post(
            "/api/voice/voices", json={"voice_id": "en_US-amy-medium"}, headers=headers
        )
        assert r.json() == {"status": "ok", "voice_id": "en_US-amy-medium"}
    assert ("set_voice", "en_US-amy-medium", None) in proxy.calls


async def test_activity_feed_roundtrip(tmp_path):
    proxy = FakeProxy()
    app = make_app(tmp_path, proxy=proxy)
    async with client_for(app) as client:
        headers = await _login(client)
        r = await client.get("/api/activity", headers=headers)
        assert r.status_code == 200
        assert r.json()[0]["tool"] == "call_service"
    assert ("activity_feed", 50) in proxy.calls


async def test_activity_feed_limit_is_forwarded(tmp_path):
    proxy = FakeProxy()
    app = make_app(tmp_path, proxy=proxy)
    async with client_for(app) as client:
        headers = await _login(client)
        await client.get("/api/activity?limit=10", headers=headers)
    assert ("activity_feed", 10) in proxy.calls


async def test_set_access_level_two_stage_continuation(tmp_path):
    """Same continuation shape as wake-word/drafts: the first call comes back pending_approval,
    the retry carries approval_request_id through to the proxy."""
    proxy = FakeProxy()
    app = make_app(tmp_path, proxy=proxy)
    async with client_for(app) as client:
        headers = await _login(client)
        first = await client.post(
            "/api/people/zion/access-level", json={"access_level": "admin"}, headers=headers
        )
        assert first.json()["status"] == "pending_approval"

        second = await client.post(
            "/api/people/zion/access-level",
            json={"access_level": "admin", "approval_request_id": "xyz789"},
            headers=headers,
        )
        assert second.json()["status"] == "ok"

    assert ("set_access_level", "zion", "admin", None) in proxy.calls
    assert ("set_access_level", "zion", "admin", "xyz789") in proxy.calls


async def test_approvals_roundtrip(tmp_path):
    proxy = FakeProxy()
    app = make_app(tmp_path, proxy=proxy)
    async with client_for(app) as client:
        headers = await _login(client)
        listed = await client.get("/api/approvals", headers=headers)
        assert listed.status_code == 200
        assert listed.json()[0]["id"] == "req1"

        approved = await client.post("/api/approvals/req1/approve", json={}, headers=headers)
        assert approved.status_code == 200
        assert approved.json()["status"] == "approved"

        denied = await client.post(
            "/api/approvals/req2/deny", json={"note": "looked wrong"}, headers=headers
        )
        assert denied.status_code == 200
        assert denied.json()["status"] == "denied"

    assert ("list_approvals",) in proxy.calls
    # decided_by is stamped from the logged-in session's own username, never client-supplied.
    assert ("approve_action", "req1", "admin:zion", None) in proxy.calls
    assert ("deny_action", "req2", "admin:zion", "looked wrong") in proxy.calls


async def test_approval_decision_ignores_a_client_supplied_decided_by(tmp_path):
    """The request body has no decided_by field at all - proving a client can't spoof one even
    if it tried (extra JSON fields are just ignored by the pydantic model)."""
    proxy = FakeProxy()
    app = make_app(tmp_path, proxy=proxy)
    async with client_for(app) as client:
        headers = await _login(client)
        await client.post(
            "/api/approvals/req1/approve", json={"decided_by": "someone-else"}, headers=headers
        )
    assert ("approve_action", "req1", "admin:zion", None) in proxy.calls


async def test_tau_core_error_maps_to_same_status_code(tmp_path):
    """tau-core itself might 428 (voice verification required, if the service token was wrong or
    unset there) - that should surface to the admin frontend as the same 428, not a generic 502."""
    app = make_app(tmp_path, proxy=FailingProxy())
    async with client_for(app) as client:
        headers = await _login(client)
        r = await client.get("/api/admin/system", headers=headers)
    assert r.status_code == 428
    assert r.json()["detail"] == "Admin voice verification required"
