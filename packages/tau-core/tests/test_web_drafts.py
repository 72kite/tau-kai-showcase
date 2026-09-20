"""Phase 8.C: the draft review surface, and the approval re-issue path it needs.

Driven against the real dangerous/echo example servers over stdio with the real CDG, same as
test_web_server.py - so the approval round trip these exercise is the actual one.
"""

import sys
from pathlib import Path

import httpx
import pytest
from httpx import ASGITransport

from tau_core.config import TauCoreSettings
from tau_core.host import TauCoreHost
from tau_core.mcp_client import MCPClientManager, ServerConfig, ServerRegistry
from tau_core.web.server import create_app

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"
CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


def build_registry() -> ServerRegistry:
    return ServerRegistry(
        servers=[
            ServerConfig(
                name="echo", transport="stdio", command=sys.executable,
                args=[str(EXAMPLES_DIR / "echo_mcp_server.py")],
            ),
            ServerConfig(
                name="dangerous", transport="stdio", command=sys.executable,
                args=[str(EXAMPLES_DIR / "dangerous_mcp_server.py")],
            ),
        ]
    )


@pytest.fixture
def settings() -> TauCoreSettings:
    return TauCoreSettings(cdg_rules_path=CONFIG_DIR / "cdg_rules.yaml")


# --- the approval re-issue path (general, not draft-specific) ---------------------------------


async def test_an_approved_action_can_be_re_issued_through_the_tool_passthrough(settings):
    """Approving RECORDS consent; it does not execute anything. Until Phase 8.C the generic
    passthrough had no approval_request_id parameter, so an approval-gated tool invoked from the
    UI could never complete - you got `pending_approval` and there was no way to come back. That
    is why voice enrolment needed bespoke two-stage endpoints."""
    async with MCPClientManager(build_registry()) as manager:
        await manager.connect_all()
        host = TauCoreHost(manager, settings=settings)
        app = create_app(settings=settings, host=host)

        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            first = await client.post(
                "/api/tools/dangerous/shutdown_host", json={"arguments": {"vmid": 101}}
            )
            assert first.status_code == 200
            assert first.json()["status"] == "pending_approval"
            request_id = first.json()["approval_request_id"]

            host.approvals.approve(request_id, approved_by="zion")

            second = await client.post(
                "/api/tools/dangerous/shutdown_host",
                json={"arguments": {"vmid": 101}, "approval_request_id": request_id},
            )

        assert second.status_code == 200
        assert second.json()["status"] == "executed"


async def test_an_approval_cannot_authorise_a_different_call(settings):
    """The reason accepting an arbitrary approval id here is safe: the CDG binds an approval to
    the exact server/tool/arguments hash it was granted for. Approving a shutdown of VM 101 must
    not shut down VM 999."""
    async with MCPClientManager(build_registry()) as manager:
        await manager.connect_all()
        host = TauCoreHost(manager, settings=settings)
        app = create_app(settings=settings, host=host)

        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            first = await client.post(
                "/api/tools/dangerous/shutdown_host", json={"arguments": {"vmid": 101}}
            )
            request_id = first.json()["approval_request_id"]
            host.approvals.approve(request_id, approved_by="zion")

            smuggled = await client.post(
                "/api/tools/dangerous/shutdown_host",
                json={"arguments": {"vmid": 999}, "approval_request_id": request_id},
            )

        # The approval doesn't match these arguments, so it's as if none was given: back to the
        # queue, not executed.
        assert smuggled.json()["status"] == "pending_approval"
        assert smuggled.json()["approval_request_id"] != request_id


async def test_an_unknown_approval_id_is_a_409_not_a_500(settings):
    async with MCPClientManager(build_registry()) as manager:
        await manager.connect_all()
        host = TauCoreHost(manager, settings=settings)
        app = create_app(settings=settings, host=host)

        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/tools/dangerous/shutdown_host",
                json={"arguments": {"vmid": 1}, "approval_request_id": "no-such-approval"},
            )

    assert resp.status_code == 409


# --- the drafts endpoint ----------------------------------------------------------------------


async def test_drafts_endpoint_degrades_to_empty_when_memory_server_is_absent(settings):
    """An empty review queue is honest ("nothing to review"); a dashboard panel should not 502
    because one domain server is down. memory-mcp-server isn't in this registry at all."""
    async with MCPClientManager(build_registry()) as manager:
        await manager.connect_all()
        host = TauCoreHost(manager, settings=settings)
        app = create_app(settings=settings, host=host)

        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/drafts")

    assert resp.status_code == 200
    assert resp.json() == []


async def test_drafts_endpoint_requires_admin_when_voice_approval_is_on():
    """Drafts are a dump of what Tau has inferred about the household, including kids - gated
    like the unified transcript, for the same reason."""
    settings = TauCoreSettings(
        cdg_rules_path=CONFIG_DIR / "cdg_rules.yaml", require_voice_approval=True
    )
    async with MCPClientManager(build_registry()) as manager:
        await manager.connect_all()
        host = TauCoreHost(manager, settings=settings)
        app = create_app(settings=settings, host=host)

        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/drafts")

    assert resp.status_code == 428


async def test_list_drafts_is_blocked_from_the_generic_passthrough(settings):
    """Otherwise any LAN client could read the whole profile dump straight around the admin gate
    on /api/drafts - the same hole 6.D closed for get_unified_transcript."""
    async with MCPClientManager(build_registry()) as manager:
        await manager.connect_all()
        host = TauCoreHost(manager, settings=settings)
        app = create_app(settings=settings, host=host)

        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/tools/memory-mcp-server/list_drafts", json={"arguments": {}}
            )

    assert resp.status_code == 403
