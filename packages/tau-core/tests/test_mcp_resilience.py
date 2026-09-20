"""Phase 7 Tier 0: the host survives a domain server dying, hanging, or never starting.

These drive real stdio subprocesses (examples/flaky_mcp_server.py), not mocks, because every
bug in this area was a lifecycle bug - a mocked session cannot reproduce a session that dies
underneath you, which is precisely what happened live on 2026-07-15.
"""

import asyncio
import sys
from pathlib import Path

import pytest
from pydantic_ai import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from tau_core.config import TauCoreSettings
from tau_core.host import TauCoreHost
from tau_core.llm.router import RouterConfidenceChecker
from tau_core.llm.toolset import build_toolset, qualified_tool_name
from tau_core.mcp_client import (
    MCPClientManager,
    ServerCallError,
    ServerConfig,
    ServerNotConnectedError,
    ServerRegistry,
)

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"
CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


def flaky_server(name: str = "flaky") -> ServerConfig:
    return ServerConfig(
        name=name,
        transport="stdio",
        command=sys.executable,
        args=[str(EXAMPLES_DIR / "flaky_mcp_server.py")],
    )


def echo_server() -> ServerConfig:
    return ServerConfig(
        name="echo",
        transport="stdio",
        command=sys.executable,
        args=[str(EXAMPLES_DIR / "echo_mcp_server.py")],
    )


def unstartable_server() -> ServerConfig:
    """A registered server whose process cannot start - the config-drift/broken-image case."""
    return ServerConfig(
        name="broken",
        transport="stdio",
        command=sys.executable,
        args=["-c", "import sys; sys.exit(1)"],
    )


async def test_connect_all_boots_with_a_broken_server_and_reports_it():
    """Partial-degradation boot. The old connect_all was a sequential loop that raised on the
    first failure, so one broken domain server meant no host at all - the whole home AI down
    because the 3D printer's server won't start."""
    registry = ServerRegistry(servers=[echo_server(), unstartable_server()])
    async with MCPClientManager(registry) as manager:
        failures = await manager.connect_all()

        assert "broken" in failures
        assert "echo" not in failures
        # The healthy server is fully usable despite its neighbour being dead on arrival.
        assert manager.connected_servers() == ["echo"]
        result = await manager.call_tool("echo", "echo", {"text": "still here"})
        assert result.content[0].text == "still here"


async def test_connected_servers_does_not_report_a_dead_session():
    """`connected_servers()` used to return dict keys, so it reported every server ever
    connected as connected - forever. It feeds build_toolset, so it lying is what turned one
    dead server into a failed turn."""
    registry = ServerRegistry(servers=[flaky_server()])
    async with MCPClientManager(registry) as manager:
        await manager.connect_all()
        assert manager.connected_servers() == ["flaky"]

        with pytest.raises(ServerCallError):
            await manager.call_tool("flaky", "crash", {})

        assert manager.connected_servers() == []


async def test_server_that_died_reconnects_on_the_next_call():
    """The live 2026-07-15 bug, as a test: recreating one domain server's container left the
    bridge returning 502 {"detail":"Session terminated"} for it permanently, until tau-core was
    manually restarted. The next call must transparently bring it back."""
    registry = ServerRegistry(servers=[flaky_server()])
    async with MCPClientManager(registry) as manager:
        await manager.connect_all()
        assert (await manager.call_tool("flaky", "ping", {"text": "before"})).content[0].text == "before"

        with pytest.raises(ServerCallError):
            await manager.call_tool("flaky", "crash", {})

        # No restart, no manual intervention: the very next call reconnects.
        result = await manager.call_tool("flaky", "ping", {"text": "after"})
        assert result.content[0].text == "after"
        assert manager.connected_servers() == ["flaky"]


async def test_reads_transparently_survive_a_dead_session():
    """A session's death is only discoverable by using it, so the first request after a
    redeploy always finds a corpse. For reads that is safe to swallow: reconnect and retry.
    This is the path that matters most, because build_toolset lists tools on every server at
    the start of every turn - so a redeployed server is back before the model picks a tool."""
    registry = ServerRegistry(servers=[flaky_server()])
    async with MCPClientManager(registry) as manager:
        await manager.connect_all()
        with pytest.raises(ServerCallError):
            await manager.call_tool("flaky", "crash", {})

        # No error, no reconnect dance in the caller: the read just works.
        tools = await manager.list_tools("flaky")
        assert {t.name for t in tools} == {"ping", "crash", "hang"}


async def test_a_dead_tool_call_is_reported_not_silently_retried():
    """Deliberate governance decision, not an oversight: `call_tool` does NOT auto-retry.

    When a call dies in flight we cannot distinguish "never sent" from "executed, then the
    reply was lost". This manager fronts door locks, a patrol drone and a 3D printer, so
    silently re-issuing a possibly-completed physical action to save one error message is the
    wrong trade. The caller gets a clear failure; the session is already dead, so their next
    call reconnects.
    """
    calls: list[str] = []

    registry = ServerRegistry(servers=[flaky_server()])
    async with MCPClientManager(registry) as manager:
        await manager.connect_all()
        real_attempt = manager._attempt

        async def counting_attempt(name, what, factory):
            calls.append(what)
            return await real_attempt(name, what, factory)

        manager._attempt = counting_attempt  # type: ignore[method-assign]

        with pytest.raises(ServerCallError):
            await manager.call_tool("flaky", "crash", {})

        # Exactly one attempt - crash() is not re-issued against a fresh process.
        assert calls == ["call_tool(crash)"]


async def test_a_wedged_server_times_out_and_does_not_hang_forever():
    """Before Phase 7 there was no timeout anywhere in tau_core/src outside hardware.py: a
    server that stops replying meant a call that never returns, holding the request forever."""
    registry = ServerRegistry(servers=[flaky_server()])
    async with MCPClientManager(registry, call_timeout=1.0) as manager:
        await manager.connect_all()

        with pytest.raises(ServerCallError, match="timed out"):
            await manager.call_tool("flaky", "hang", {"seconds": 30.0})

        # A timed-out session is not trusted afterwards - it's torn down, and the next call
        # gets a fresh process rather than queueing behind the wedged one.
        assert manager.connected_servers() == []
        assert (await manager.call_tool("flaky", "ping", {"text": "fresh"})).content[0].text == "fresh"


async def test_unreachable_server_backs_off_instead_of_retrying_every_call():
    """Reconnect-on-demand must not become "pay the connect timeout on every turn" for a server
    that is genuinely gone. After a failed connect, calls fast-fail until the backoff expires."""
    registry = ServerRegistry(servers=[unstartable_server()])
    async with MCPClientManager(registry) as manager:
        await manager.connect_all()  # records the first failure + starts the backoff

        started = asyncio.get_running_loop().time()
        with pytest.raises(ServerNotConnectedError, match="down"):
            await manager.call_tool("broken", "anything", {})
        # Fast-fail, not a fresh subprocess spawn + handshake attempt.
        assert asyncio.get_running_loop().time() - started < 0.5


async def test_unregistered_server_is_a_clear_error_not_a_reconnect_attempt():
    registry = ServerRegistry(servers=[echo_server()])
    async with MCPClientManager(registry) as manager:
        await manager.connect_all()
        with pytest.raises(ServerNotConnectedError, match="not registered"):
            await manager.call_tool("nope", "anything", {})


async def test_concurrent_calls_to_a_dead_server_produce_one_reconnect():
    """Without the per-server lock, N concurrent callers finding a dead session would each
    spawn their own replacement subprocess and N-1 would be orphaned."""
    registry = ServerRegistry(servers=[flaky_server()])
    async with MCPClientManager(registry) as manager:
        await manager.connect_all()
        with pytest.raises(ServerCallError):
            await manager.call_tool("flaky", "crash", {})

        results = await asyncio.gather(
            *(manager.call_tool("flaky", "ping", {"text": f"c{i}"}) for i in range(4))
        )
        assert sorted(r.content[0].text for r in results) == ["c0", "c1", "c2", "c3"]
        assert manager.connected_servers() == ["flaky"]


# --- The turn-level consequence: one dead server must not cost the whole turn ---------------


def stub_router_model(confidence: float = 0.9) -> FunctionModel:
    def respond(messages, info):
        return ModelResponse(
            parts=[ToolCallPart(tool_name="final_result", args={"confidence": confidence, "reasoning": "stub"})]
        )

    return FunctionModel(respond)


def _settings(**overrides) -> TauCoreSettings:
    return TauCoreSettings(cdg_rules_path=CONFIG_DIR / "cdg_rules.yaml", **overrides)


def _host_for(
    *servers: ServerConfig, settings: TauCoreSettings | None = None
) -> tuple[MCPClientManager, TauCoreHost]:
    manager = MCPClientManager(ServerRegistry(servers=list(servers)))
    return manager, TauCoreHost(manager, settings=settings or _settings())


async def test_build_toolset_degrades_to_missing_tools_not_a_dead_turn():
    """`llm/toolset.py` calls list_tools on every server on every turn. One zombie raising there
    used to take down the entire turn - including questions that needed no tools at all. Now the
    healthy servers' tools still reach the model; the dead one's simply aren't offered."""
    manager, host = _host_for(echo_server(), unstartable_server())
    async with manager:
        await manager.connect_all()
        checker = RouterConfidenceChecker(stub_router_model())

        toolset = await build_toolset(host, checker, "hello", [])

        names = set(toolset.tools)
        assert qualified_tool_name("echo", "echo") in names
        assert not any(name.startswith("broken__") for name in names)


async def test_build_toolset_rejects_a_typod_server_but_not_a_down_one():
    """The unknown-server ValueError means "you typo'd the name". A registered server that
    happens to be down is a transient, and telling a sub-agent it typo'd a real server name
    would be a lie - it just gets no tools from it this turn."""
    manager, host = _host_for(echo_server(), unstartable_server())
    async with manager:
        await manager.connect_all()
        checker = RouterConfidenceChecker(stub_router_model())

        with pytest.raises(ValueError, match="Unknown MCP server"):
            await build_toolset(host, checker, "hi", [], servers=["ecko"])

        # "broken" is registered but down: allowed through, contributes no tools.
        toolset = await build_toolset(host, checker, "hi", [], servers=["broken"])
        assert not toolset.tools


async def test_a_server_dying_mid_turn_does_not_kill_the_turn():
    """Isolating build_toolset is not enough on its own. A server can die (or a call can exceed
    the MCP call timeout) *between* the toolset build and the invocation, and an exception
    raised inside a wrapped tool propagates out through PydanticAI and 502s the whole turn -
    the exact failure Tier 0 exists to prevent. The model must get a reportable tool outcome.
    """
    from pydantic_ai.messages import ModelRequest, ToolReturnPart

    from tau_core.llm.agent import TauAssistant

    seen: list[str] = []

    def main_fn(messages, info):
        for message in messages:
            if isinstance(message, ModelRequest):
                for part in message.parts:
                    if isinstance(part, ToolReturnPart):
                        seen.append(str(part.content))
        if seen:
            return ModelResponse(parts=[TextPart("the flaky server is unavailable")])
        return ModelResponse(
            parts=[ToolCallPart(tool_name=qualified_tool_name("flaky", "crash"), args={})]
        )

    manager, host = _host_for(flaky_server())
    async with manager:
        await manager.connect_all()
        assistant = TauAssistant(
            host, main_model=FunctionModel(main_fn), router_model=stub_router_model()
        )

        # crash() kills the server mid-turn. The turn must still complete.
        turn = await assistant.chat("check the flaky server")

        assert turn.reply == "the flaky server is unavailable"
        assert any(text.startswith("UNAVAILABLE:") for text in seen), seen


async def test_health_reports_degraded_but_still_answers_200():
    """The compose healthcheck treats /api/health as a LIVENESS probe, and this is the test that
    keeps it honest.

    It is tempting to fail the check when domain servers are missing. That would be wrong twice
    over: restarting tau-core cannot fix a domain server being down, and Phase 7 deliberately
    made the host survive that (per-server sessions, on-demand reconnect, partial-degradation
    boot). Failing here would re-introduce the coupling that work removed AND restart-loop the
    one process holding the approval queue. `degraded` is information for humans; only "did not
    answer at all" is an instruction to the orchestrator.
    """
    import httpx
    from httpx import ASGITransport

    from tau_core.web.server import create_app

    manager, host = _host_for(echo_server(), unstartable_server())
    async with manager:
        await manager.connect_all()
        app = create_app(settings=_settings(), host=host)

        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/health")

    assert resp.status_code == 200  # still alive, still serving - do NOT restart me
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["connected_servers"] == ["echo"]
    assert body["unavailable_servers"] == ["broken"]
    assert sorted(body["registered_servers"]) == ["broken", "echo"]


async def test_health_reports_ok_when_everything_registered_is_connected():
    """`status` was the literal string "ok" regardless of reality, so it would have passed a
    healthcheck while wholly broken. It has to be able to say both words to mean either."""
    import httpx
    from httpx import ASGITransport

    from tau_core.web.server import create_app

    manager, host = _host_for(echo_server())
    async with manager:
        await manager.connect_all()
        app = create_app(settings=_settings(), host=host)

        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            body = (await client.get("/api/health")).json()

    assert body["status"] == "ok"
    assert body["unavailable_servers"] == []


async def test_tool_passthrough_reports_a_down_server_as_503_not_a_bare_500():
    """Found by killing a domain server under the running bridge: /api/tools only caught
    CoreDirectiveViolation, so a transport failure escaped as "Internal Server Error" - which
    tells a caller nothing about whether the action happened or whether to retry."""
    import httpx
    from httpx import ASGITransport

    from tau_core.web.server import create_app

    manager, host = _host_for(echo_server(), unstartable_server())
    async with manager:
        await manager.connect_all()  # 'broken' fails -> backoff, so this is a fast 503
        app = create_app(settings=_settings(), host=host)

        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/tools/broken/anything", json={"arguments": {}})

        assert resp.status_code == 503
        assert "broken" in resp.json()["detail"]


# --- A hung model must not hang the request forever ------------------------------------------


async def test_chat_returns_504_instead_of_hanging_on_a_wedged_model():
    """A hung Ollama used to mean a POST /api/chat that never returned - no timeout existed
    anywhere on the path, so the kiosk tab span forever and the worker never came back. The
    FunctionModel here never replies, which is what a wedged backend looks like from up here.
    """
    import httpx
    from httpx import ASGITransport

    from tau_core.llm.agent import TauAssistant
    from tau_core.web.server import create_app

    async def never_replies(messages, info):
        await asyncio.sleep(30)
        return ModelResponse(parts=[])

    settings = _settings(llm_turn_timeout_seconds=1.0)
    manager, host = _host_for(echo_server(), settings=settings)
    async with manager:
        await manager.connect_all()
        assistant = TauAssistant(
            host, main_model=FunctionModel(never_replies), router_model=stub_router_model()
        )
        app = create_app(settings=settings, host=host, assistant=assistant)

        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/chat", json={"text": "are you there?"})

        assert resp.status_code == 504
        assert "timed out" in resp.json()["detail"]
