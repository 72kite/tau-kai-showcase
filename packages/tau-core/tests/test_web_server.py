"""Tests for the Phase 3 HTTP bridge (tau_core.web.server) that the tablet frontend talks to.

Follows the same pattern as test_host_integration.py: connect a real MCPClientManager to the
echo/dangerous example servers, then exercise the bridge's endpoints against a real (not
mocked) TauCoreHost so these tests prove the whole path - HTTP request -> CDG -> MCP transport
- actually works, not just that the FastAPI routing is wired correctly.
"""

import sys
from pathlib import Path

import httpx
import pytest
from httpx import ASGITransport
from pydantic_ai import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.messages import ModelRequest, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel

from tau_core.config import TauCoreSettings
from tau_core.host import TauCoreHost
from tau_core.llm.agent import TauAssistant
from tau_core.llm.toolset import qualified_tool_name
from tau_core.mcp_client import MCPClientManager, ServerConfig, ServerRegistry
from tau_core.web.server import create_app

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"
CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


def build_registry() -> ServerRegistry:
    return ServerRegistry(
        servers=[
            ServerConfig(
                name="echo",
                transport="stdio",
                command=sys.executable,
                args=[str(EXAMPLES_DIR / "echo_mcp_server.py")],
            ),
            ServerConfig(
                name="dangerous",
                transport="stdio",
                command=sys.executable,
                args=[str(EXAMPLES_DIR / "dangerous_mcp_server.py")],
            ),
        ]
    )


@pytest.fixture
def settings() -> TauCoreSettings:
    return TauCoreSettings(cdg_rules_path=CONFIG_DIR / "cdg_rules.yaml")


async def test_health_lists_connected_servers(settings):
    registry = build_registry()
    async with MCPClientManager(registry) as manager:
        await manager.connect_all()
        host = TauCoreHost(manager, settings=settings)
        app = create_app(settings=settings, host=host)

        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/health")

        assert resp.status_code == 200
        assert "echo" in resp.json()["connected_servers"]


async def test_health_reports_version_and_build(settings, monkeypatch):
    """The kiosk footer compares these against its own baked-in stamp to spot a stale cached
    bundle, so /api/health must actually carry them (see StatusFooter.jsx / useVersion.js)."""
    monkeypatch.setenv("TAU_BUILD_SHA", "a3f9c21")
    registry = build_registry()
    async with MCPClientManager(registry) as manager:
        await manager.connect_all()
        host = TauCoreHost(manager, settings=settings)
        app = create_app(settings=settings, host=host)

        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            body = (await client.get("/api/health")).json()

        assert body["build"] == "a3f9c21"
        # Running from a source checkout, so the value itself is the unknown sentinel - what
        # matters is that the field is present and is a string the footer can compare.
        assert isinstance(body["version"], str) and body["version"]


async def test_health_reports_device_token_enforcement_state():
    """Phase 27.A: the open-by-default state used to be silent - /api/health now says so
    directly, which is what drives StatusFooter.jsx's UNAUTHENTICATED tag."""
    registry = build_registry()

    off_settings = TauCoreSettings(
        cdg_rules_path=CONFIG_DIR / "cdg_rules.yaml", require_device_token=False
    )
    async with MCPClientManager(registry) as manager:
        await manager.connect_all()
        host = TauCoreHost(manager, settings=off_settings)
        app = create_app(settings=off_settings, host=host)
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            body = (await client.get("/api/health")).json()
        assert body["device_token_enforced"] is False

    on_settings = TauCoreSettings(
        cdg_rules_path=CONFIG_DIR / "cdg_rules.yaml", require_device_token=True
    )
    async with MCPClientManager(build_registry()) as manager:
        await manager.connect_all()
        host = TauCoreHost(manager, settings=on_settings)
        app = create_app(settings=on_settings, host=host)
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            body = (await client.get("/api/health")).json()
        assert body["device_token_enforced"] is True


async def test_get_resource_returns_resource_body(settings):
    registry = build_registry()
    async with MCPClientManager(registry) as manager:
        await manager.connect_all()
        host = TauCoreHost(manager, settings=settings)
        app = create_app(settings=settings, host=host)

        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/resources/echo/echo://status")

        assert resp.status_code == 200
        assert resp.json() == {"text": "ok"}


async def test_call_tool_executes_through_bridge(settings):
    registry = build_registry()
    async with MCPClientManager(registry) as manager:
        await manager.connect_all()
        host = TauCoreHost(manager, settings=settings)
        app = create_app(settings=settings, host=host)

        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/tools/echo/echo", json={"arguments": {"text": "hi tablet"}})

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "executed"
        assert body["result"] == "hi tablet"


async def test_activity_feed_reflects_tool_calls(settings):
    """/api/activity serves the audit ring buffer: a call made through the bridge must show up
    as an event with its server/tool/outcome, newest first."""
    registry = build_registry()
    async with MCPClientManager(registry) as manager:
        await manager.connect_all()
        host = TauCoreHost(manager, settings=settings)
        app = create_app(settings=settings, host=host)

        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/tools/echo/echo", json={"arguments": {"text": "audit me"}})
            resp = await client.get("/api/activity")

        assert resp.status_code == 200
        events = resp.json()
        match = next(
            e for e in events
            if e.get("server") == "echo" and e.get("arguments", {}).get("text") == "audit me"
        )
        assert match["outcome"] == "executed"
        assert match["cdg_effect"] == "allow"


class StubMemoryBackend:
    async def remember(self, session_id, text):
        return None

    async def recall(self, session_id, query, limit=5, owner=""):
        return ["Kitchen lighting: Dim to 40% after 22:00."]


async def test_chat_response_includes_recalled_memories(settings):
    """Recall transparency: whatever the Memory Tree surfaced into the prompt must come back
    in the /api/chat response so the UI can show why Tau knew it."""
    from tau_core.session import SessionManager

    registry = build_registry()
    async with MCPClientManager(registry) as manager:
        await manager.connect_all()
        host = TauCoreHost(manager, settings=settings)
        assistant = TauAssistant(
            host,
            main_model=FunctionModel(lambda m, i: ModelResponse(parts=[TextPart("noted")])),
            router_model=_stub_router_model(0.9),
            session=SessionManager(memory_backend=StubMemoryBackend()),
        )
        app = create_app(settings=settings, host=host, assistant=assistant)

        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/chat", json={"text": "kitchen lights?"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["recalled_memories"] == ["Kitchen lighting: Dim to 40% after 22:00."]


async def test_chat_with_text_attachment_injects_content(settings):
    """A dropped text file's content must reach the model prompt (decoded server-side), and the
    turn must work even with an empty user message."""
    import base64

    seen_prompts: list[str] = []

    def main_fn(messages, info):
        seen_prompts.append(str(messages))
        return ModelResponse(parts=[TextPart("read it")])

    registry = build_registry()
    async with MCPClientManager(registry) as manager:
        await manager.connect_all()
        host = TauCoreHost(manager, settings=settings)
        assistant = TauAssistant(
            host, main_model=FunctionModel(main_fn), router_model=_stub_router_model(0.9)
        )
        app = create_app(settings=settings, host=host, assistant=assistant)

        payload = {
            "text": "",
            "attachments": [
                {
                    "name": "notes.txt",
                    "content_type": "text/plain",
                    "data_b64": base64.b64encode(b"the printer nozzle is 0.4mm").decode(),
                }
            ],
        }
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/chat", json=payload)

        assert resp.status_code == 200
        assert resp.json()["reply"] == "read it"
        assert "the printer nozzle is 0.4mm" in seen_prompts[0]
        assert "notes.txt" in seen_prompts[0]


async def test_chat_rejects_oversized_attachment(settings):
    registry = build_registry()
    async with MCPClientManager(registry) as manager:
        await manager.connect_all()
        host = TauCoreHost(manager, settings=settings)
        assistant = TauAssistant(
            host,
            main_model=FunctionModel(lambda m, i: ModelResponse(parts=[TextPart("x")])),
            router_model=_stub_router_model(0.9),
        )
        app = create_app(settings=settings, host=host, assistant=assistant)

        payload = {
            "text": "look",
            "attachments": [{"name": "big.bin", "content_type": "", "data_b64": "A" * 14_000_001}],
        }
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/chat", json=payload)

        assert resp.status_code == 413


async def test_call_tool_denied_returns_403(settings):
    """CDG's '*cdg*' glob is a hard deny - the bridge must surface that as 403, not a 500 or
    a silently-executed call, when a tablet-initiated action hits the guard.
    """
    registry = build_registry()
    async with MCPClientManager(registry) as manager:
        await manager.connect_all()
        host = TauCoreHost(manager, settings=settings)
        app = create_app(settings=settings, host=host)

        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/tools/dangerous/edit_cdg_rules", json={"arguments": {}})

        assert resp.status_code == 403


async def test_call_tool_pending_approval_then_approve_via_bridge(settings):
    registry = build_registry()
    async with MCPClientManager(registry) as manager:
        await manager.connect_all()
        host = TauCoreHost(manager, settings=settings)
        app = create_app(settings=settings, host=host)

        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            call_resp = await client.post(
                "/api/tools/dangerous/shutdown_host", json={"arguments": {"vmid": 101}}
            )
            assert call_resp.status_code == 200
            body = call_resp.json()
            assert body["status"] == "pending_approval"
            request_id = body["approval_request_id"]
            assert request_id is not None

            list_resp = await client.get("/api/approvals")
            assert any(a["id"] == request_id for a in list_resp.json())

            approve_resp = await client.post(
                f"/api/approvals/{request_id}/approve", json={"decided_by": "zion"}
            )

        assert approve_resp.status_code == 200
        assert approve_resp.json()["status"] == "approved"


async def test_approve_unknown_request_id_returns_404(settings):
    registry = build_registry()
    async with MCPClientManager(registry) as manager:
        await manager.connect_all()
        host = TauCoreHost(manager, settings=settings)
        app = create_app(settings=settings, host=host)

        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/approvals/does-not-exist/approve", json={})

        # The endpoint looks the request up first (needed for the access-tier check), so an
        # unknown id is a 404 Not Found rather than the old 409 Conflict.
        assert resp.status_code == 404


def _stub_router_model(confidence: float) -> FunctionModel:
    def respond(messages, info):
        return ModelResponse(
            parts=[ToolCallPart(tool_name="final_result", args={"confidence": confidence, "reasoning": "stub"})]
        )

    return FunctionModel(respond)


def _tool_return_text(messages) -> str:
    for message in messages:
        if isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, ToolReturnPart):
                    return str(part.content)
    raise AssertionError("no ToolReturnPart found in message history")


async def test_chat_returns_reply_for_a_plain_text_turn(settings):
    """The atom-click / wake-word flow in the frontend both land here: one utterance in, one
    reply out. No tool call in this turn, so the model just replies directly."""

    def main_fn(messages, info):
        return ModelResponse(parts=[TextPart("Hello, I'm Tau.")])

    registry = build_registry()
    async with MCPClientManager(registry) as manager:
        await manager.connect_all()
        host = TauCoreHost(manager, settings=settings)
        assistant = TauAssistant(host, main_model=FunctionModel(main_fn), router_model=_stub_router_model(0.99))
        app = create_app(settings=settings, host=host, assistant=assistant)

        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/chat", json={"text": "hi tau"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["reply"] == "Hello, I'm Tau."
        assert body["pending_approval_ids"] == []
        assert body["image"] is None


async def test_chat_response_includes_an_image_from_an_image_producing_tool_call(settings, monkeypatch):
    """Phase 40 "visual answer cards": AssistantTurn.image reaches the HTTP response body, not
    just the Python-level chat() return value. Same echo-server-as-stand-in technique as
    test_llm_agent.py's own version of this test - see that file for why a live SearxNG isn't
    needed to prove the wiring."""
    from research_mcp_server.search import ImageResult
    from research_mcp_server.server import _frame, _neutralise_markers
    from tau_core.llm import toolset as toolset_module
    import json as _json

    monkeypatch.setattr(toolset_module, "IMAGE_RESULT_TOOLS", frozenset({("echo", "echo")}))

    body_json = _json.dumps(
        [ImageResult(
            title="Eiffel Tower", image_url="https://example.com/eiffel.jpg",
            source_url="https://example.com/paris", source="example.com",
        ).to_dict()],
        indent=2,
    )
    framed = _frame("image search for 'eiffel tower' via searxng", _neutralise_markers(body_json))

    def main_fn(messages, info):
        if len(messages) == 1:
            return ModelResponse(
                parts=[ToolCallPart(tool_name=qualified_tool_name("echo", "echo"), args={"text": framed})]
            )
        return ModelResponse(parts=[TextPart("here's the Eiffel Tower")])

    registry = build_registry()
    async with MCPClientManager(registry) as manager:
        await manager.connect_all()
        host = TauCoreHost(manager, settings=settings)
        assistant = TauAssistant(host, main_model=FunctionModel(main_fn), router_model=_stub_router_model(0.99))
        app = create_app(settings=settings, host=host, assistant=assistant)

        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/chat", json={"text": "what does the eiffel tower look like?"})

    assert resp.status_code == 200
    assert resp.json()["image"] == {
        "url": "https://example.com/eiffel.jpg",
        "title": "Eiffel Tower",
        "source_url": "https://example.com/paris",
        "source": "example.com",
    }


async def test_chat_surfaces_pending_approval_from_a_tool_call(settings):
    """If the model's turn triggers a require_approval tool, that must show up in the chat
    response exactly as it does for a direct TauCoreHost.call_tool() caller - /api/chat is a
    thin wrapper, not a new privileged path around the CDG."""

    def main_fn(messages, info):
        if len(messages) == 1:
            return ModelResponse(
                parts=[ToolCallPart(tool_name=qualified_tool_name("dangerous", "shutdown_host"), args={"vmid": 101})]
            )
        return ModelResponse(parts=[TextPart(_tool_return_text(messages))])

    registry = build_registry()
    async with MCPClientManager(registry) as manager:
        await manager.connect_all()
        host = TauCoreHost(manager, settings=settings)
        assistant = TauAssistant(host, main_model=FunctionModel(main_fn), router_model=_stub_router_model(0.99))
        app = create_app(settings=settings, host=host, assistant=assistant)

        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/chat", json={"text": "shut down vm 101"})

        assert resp.status_code == 200
        body = resp.json()
        assert len(body["pending_approval_ids"]) == 1
        assert "PENDING_APPROVAL" in body["reply"]


async def test_chat_rejects_empty_text(settings):
    registry = build_registry()
    async with MCPClientManager(registry) as manager:
        await manager.connect_all()
        host = TauCoreHost(manager, settings=settings)
        assistant = TauAssistant(
            host, main_model=FunctionModel(lambda m, i: ModelResponse(parts=[TextPart("x")])),
            router_model=_stub_router_model(0.99),
        )
        app = create_app(settings=settings, host=host, assistant=assistant)

        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/chat", json={"text": "   "})

        assert resp.status_code == 400


async def test_chat_rate_limit_returns_429_with_retry_after(settings):
    """Phase 7 Tier 1 #9: /api/chat has no authentication at all, so a per-client budget is the
    only thing standing between it and one client pegging the box with real LLM turns."""
    limited_settings = settings.model_copy(update={"chat_rate_limit_max": 1})
    registry = build_registry()
    async with MCPClientManager(registry) as manager:
        await manager.connect_all()
        host = TauCoreHost(manager, settings=limited_settings)
        assistant = TauAssistant(
            host,
            main_model=FunctionModel(lambda m, i: ModelResponse(parts=[TextPart("hi")])),
            router_model=_stub_router_model(0.99),
        )
        app = create_app(settings=limited_settings, host=host, assistant=assistant)

        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            first = await client.post("/api/chat", json={"text": "hello"})
            second = await client.post("/api/chat", json={"text": "hello again"})

        assert first.status_code == 200
        assert second.status_code == 429
        assert "Retry-After" in second.headers


async def test_chat_rate_limit_is_scoped_per_device(settings):
    """Two different X-Tau-Device-Id headers must not share one budget - device A being
    throttled must not block device B."""
    limited_settings = settings.model_copy(
        update={"chat_rate_limit_max": 1, "require_device_token": False}
    )
    registry = build_registry()
    async with MCPClientManager(registry) as manager:
        await manager.connect_all()
        host = TauCoreHost(manager, settings=limited_settings)
        assistant = TauAssistant(
            host,
            main_model=FunctionModel(lambda m, i: ModelResponse(parts=[TextPart("hi")])),
            router_model=_stub_router_model(0.99),
        )
        app = create_app(settings=limited_settings, host=host, assistant=assistant)

        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            device_a = await client.post(
                "/api/chat", json={"text": "hi"}, headers={"X-Tau-Device-Id": "tablet-a"}
            )
            device_b = await client.post(
                "/api/chat", json={"text": "hi"}, headers={"X-Tau-Device-Id": "tablet-b"}
            )

        assert device_a.status_code == 200
        assert device_b.status_code == 200


async def test_chat_context_is_isolated_per_device_header(settings):
    """Phase 12 end-to-end: the X-Tau-Device-Id header must scope the model's CONVERSATION
    CONTEXT, not just the rendered transcript. Device A shares a secret over the bridge; device
    B's turn (different header) must not have it in the prompt the model receives. This is the
    §10.1 #3 / Tier 1 #8 leak, asserted at the HTTP boundary the kiosks actually use."""
    seen_prompts: list[str] = []

    def main_fn(messages, info):
        for message in messages:
            if isinstance(message, ModelRequest):
                for part in message.parts:
                    if isinstance(part, UserPromptPart):
                        seen_prompts.append(str(part.content))
        return ModelResponse(parts=[TextPart("ok")])

    settings = settings.model_copy(update={"require_device_token": False})
    registry = build_registry()
    async with MCPClientManager(registry) as manager:
        await manager.connect_all()
        host = TauCoreHost(manager, settings=settings)
        assistant = TauAssistant(host, main_model=FunctionModel(main_fn), router_model=_stub_router_model(0.99))
        app = create_app(settings=settings, host=host, assistant=assistant)

        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post(
                "/api/chat", json={"text": "my code is SPARROW"}, headers={"X-Tau-Device-Id": "tablet-a"}
            )
            await client.post(
                "/api/chat", json={"text": "what is my code?"}, headers={"X-Tau-Device-Id": "tablet-b"}
            )

    assert "SPARROW" not in seen_prompts[-1]


async def test_voice_challenge_rate_limit_returns_429(settings):
    """/api/voice/challenge takes no MCP call at all (just mints a phrase), so it's a clean way
    to exercise the voice-endpoint limiter without a real voice-mcp-server."""
    limited_settings = settings.model_copy(update={"voice_rate_limit_max": 2})
    registry = build_registry()
    async with MCPClientManager(registry) as manager:
        await manager.connect_all()
        host = TauCoreHost(manager, settings=limited_settings)
        app = create_app(settings=limited_settings, host=host)

        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            responses = [await client.post("/api/voice/challenge") for _ in range(3)]

        assert [r.status_code for r in responses] == [200, 200, 429]
        assert "Retry-After" in responses[-1].headers


async def test_get_resource_error_does_not_leak_raw_exception_text(settings):
    """Phase 7 Tier 1 #9: this endpoint has no auth, so a broken/unknown resource must not hand
    back the underlying exception's text (which named the resolution logic that rejected it) -
    only a generic, server/uri-scoped message that the caller already had the inputs for."""
    registry = build_registry()
    async with MCPClientManager(registry) as manager:
        await manager.connect_all()
        host = TauCoreHost(manager, settings=settings)
        app = create_app(settings=settings, host=host)

        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/resources/no-such-server/foo://bar")

        assert resp.status_code == 502
        detail = resp.json()["detail"]
        assert detail == "Resource 'foo://bar' on 'no-such-server' is unavailable."
        # No ValueError/exception-class name or the manager's own "unknown server" wording
        # (its actual message text) leaked into the response.
        assert "ValueError" not in detail
        assert "unknown server" not in detail.lower()


async def test_chat_without_ollama_configured_returns_503(settings, monkeypatch, tmp_path):
    """No assistant injected and no OLLAMA_HOST set - _get_assistant()'s lazy
    TauAssistant.from_settings() must fail informatively, not crash the endpoint."""
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    # EnvFileSecretsProvider also reads ./.env from disk, so a developer's real tau-core/.env
    # (present whenever live Ollama testing has happened on this machine) would satisfy the
    # lazy from_settings() and turn this into a real chat turn. chdir somewhere .env-less;
    # every path this test uses (registry args, config dir) is already absolute.
    monkeypatch.chdir(tmp_path)

    registry = build_registry()
    async with MCPClientManager(registry) as manager:
        await manager.connect_all()
        host = TauCoreHost(manager, settings=settings)
        app = create_app(settings=settings, host=host)  # no assistant injected

        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/chat", json={"text": "hi"})

        assert resp.status_code == 503
