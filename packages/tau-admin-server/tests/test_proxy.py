"""TauCoreProxy's own HTTP behavior: headers it attaches, and error-status mapping. A real httpx
MockTransport stands in for tau-core - no server.py/auth involved here."""

import httpx
import pytest

from tau_admin_server.proxy import TauCoreError, TauCoreProxy


def make_proxy(handler, service_token="shhh-secret", device_token=None):
    transport = httpx.MockTransport(handler)
    return TauCoreProxy(
        base_url="http://tau-core:8000",
        service_token=service_token,
        device_token=device_token,
        transport=transport,
    )


async def test_service_token_and_device_id_headers_are_attached():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["service_token"] = request.headers.get("x-tau-admin-service-token")
        seen["device_id"] = request.headers.get("x-tau-device-id")
        return httpx.Response(200, json=[])

    proxy = make_proxy(handler)
    await proxy.list_devices()
    assert seen["service_token"] == "shhh-secret"
    assert seen["device_id"] == "tau-admin-server"


async def test_no_service_token_header_when_unset():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["has_header"] = "x-tau-admin-service-token" in request.headers
        return httpx.Response(200, json={})

    proxy = make_proxy(handler, service_token=None)
    await proxy.system_snapshot()
    assert seen["has_header"] is False


async def test_no_device_token_header_when_unset():
    """Phase 48: TauCoreProxy must not send an empty/None X-Tau-Device-Token - tau-core's own
    _device_id() treats a present-but-blank header the same as an invalid one, 403ing rather
    than falling back to anonymous."""
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["has_header"] = "x-tau-device-token" in request.headers
        return httpx.Response(200, json={})

    proxy = make_proxy(handler)
    await proxy.system_snapshot()
    assert seen["has_header"] is False


async def test_device_token_header_is_attached_when_configured():
    """Phase 48: once TAU_ADMIN_TAU_CORE_DEVICE_TOKEN is set, it rides alongside the existing
    service-token/device-id pair so the drafts promote/discard passthrough (gated by tau-core's
    _device_id(), not _require_admin) keeps working once TAU_REQUIRE_DEVICE_TOKEN is on."""
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["device_token"] = request.headers.get("x-tau-device-token")
        return httpx.Response(200, json={})

    proxy = make_proxy(handler, device_token="the-minted-token")
    await proxy.system_snapshot()
    assert seen["device_token"] == "the-minted-token"


async def test_error_response_raises_tau_core_error_with_status_and_detail():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(428, json={"detail": "Admin voice verification required"})

    proxy = make_proxy(handler)
    with pytest.raises(TauCoreError) as exc_info:
        await proxy.system_snapshot()
    assert exc_info.value.status_code == 428
    assert exc_info.value.detail == "Admin voice verification required"


async def test_promote_draft_first_call_omits_approval_id():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = request.content
        return httpx.Response(200, json={"status": "pending_approval"})

    proxy = make_proxy(handler)
    await proxy.promote_draft("node1")
    assert seen["path"] == "/api/tools/memory-mcp-server/promote_memory"
    assert b"approval_request_id" not in seen["body"]


async def test_promote_draft_retry_includes_approval_id():
    def handler(request: httpx.Request) -> httpx.Response:
        assert b"abc123" in request.content
        return httpx.Response(200, json={"status": "ok"})

    proxy = make_proxy(handler)
    result = await proxy.promote_draft("node1", approval_request_id="abc123")
    assert result == {"status": "ok"}


async def test_list_approvals_hits_the_real_tau_core_endpoint_not_a_passthrough():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        return httpx.Response(200, json=[{"id": "req1"}])

    proxy = make_proxy(handler)
    result = await proxy.list_approvals()
    assert seen["path"] == "/api/approvals"
    assert result == [{"id": "req1"}]


async def test_approve_action_sends_decided_by_and_omits_note_when_absent():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = request.content
        return httpx.Response(200, json={"status": "approved"})

    proxy = make_proxy(handler)
    await proxy.approve_action("req1", "admin:zion")
    assert seen["path"] == "/api/approvals/req1/approve"
    assert b"admin:zion" in seen["body"]
    assert b"note" not in seen["body"]


async def test_deny_action_includes_note_when_given():
    def handler(request: httpx.Request) -> httpx.Response:
        assert b"admin:zion" in request.content
        assert b"looked wrong" in request.content
        return httpx.Response(200, json={"status": "denied"})

    proxy = make_proxy(handler)
    result = await proxy.deny_action("req1", "admin:zion", note="looked wrong")
    assert result == {"status": "denied"}
