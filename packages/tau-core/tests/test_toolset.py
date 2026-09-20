"""Phase 11 audit: MODEL_EXCLUDED_TOOLS. get_unified_transcript (Phase 6.D) and list_drafts
(Phase 8.C) each already had an admin-only HTTP endpoint and were blocked from the generic
/api/tools passthrough - but nothing stopped the model itself from calling either one straight
through host.call_tool during a normal chat turn, which defeated the whole point of gating them.
The CDG can't fix this (it has no concept of caller identity, so a deny/require_approval rule
would break the legitimate admin HTTP path too, which goes through the exact same CDG). The fix
is at the toolset layer, which does know "this is being offered to the model" - exercised here
against the real servers over stdio, not mocks.
"""

import sys
from pathlib import Path

from pydantic_ai import ModelResponse, TextPart

from tau_core.config import TauCoreSettings
from tau_core.host import TauCoreHost
from tau_core.llm.router import RouterConfidenceChecker
from tau_core.llm import toolset as toolset_module
from tau_core.llm.toolset import (
    IMAGE_RESULT_TOOLS,
    MODEL_EXCLUDED_TOOLS,
    PASSTHROUGH_BLOCKED_MEMORY_WRITES,
    SUBAGENT_ONLY_SERVERS,
    _extract_first_image,
    build_toolset,
    qualified_tool_name,
)
from tau_core.mcp_client import MCPClientManager, ServerConfig, ServerRegistry

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


def _stub_router() -> RouterConfidenceChecker:
    def respond(messages, info):
        from pydantic_ai import ToolCallPart

        return ModelResponse(
            parts=[ToolCallPart(tool_name="final_result", args={"confidence": 0.9, "reasoning": "stub"})]
        )

    from pydantic_ai.models.function import FunctionModel

    return RouterConfidenceChecker(FunctionModel(respond))


def _settings() -> TauCoreSettings:
    return TauCoreSettings(cdg_rules_path=CONFIG_DIR / "cdg_rules.yaml")


async def test_get_unified_transcript_never_reaches_the_models_toolset():
    registry = ServerRegistry(
        servers=[
            ServerConfig(
                name="ui-bridge-mcp-server",
                transport="stdio",
                command=sys.executable,
                args=["-m", "ui_bridge_mcp_server.server"],
            )
        ]
    )
    async with MCPClientManager(registry) as manager:
        await manager.connect_all()
        host = TauCoreHost(manager, settings=_settings())
        toolset = await build_toolset(host, _stub_router(), "what did the study ipad ask?", [])

        names = set(toolset.tools)
        assert qualified_tool_name("ui-bridge-mcp-server", "get_unified_transcript") not in names
        # The exclusion must be targeted, not "the whole server disappeared". update_transcription
        # itself is ALSO excluded now (Phase 41 - see MODEL_EXCLUDED_TOOLS's docstring), so this
        # uses a different, still-model-visible ui-bridge-mcp-server tool as the control.
        assert qualified_tool_name("ui-bridge-mcp-server", "get_device_transcript") in names


async def test_list_drafts_never_reaches_the_models_toolset():
    registry = ServerRegistry(
        servers=[
            ServerConfig(
                name="memory-mcp-server",
                transport="stdio",
                command=sys.executable,
                args=["-m", "memory_mcp_server.server"],
            )
        ]
    )
    async with MCPClientManager(registry) as manager:
        await manager.connect_all()
        host = TauCoreHost(manager, settings=_settings())
        toolset = await build_toolset(host, _stub_router(), "what drafts exist about the kids?", [])

        names = set(toolset.tools)
        assert qualified_tool_name("memory-mcp-server", "list_drafts") not in names
        assert qualified_tool_name("memory-mcp-server", "search_memory") in names


# --- Phase 38: SUBAGENT_ONLY_SERVERS -------------------------------------------------------------
# stealth-browser-mcp itself isn't installed in this test venv (it's a separate, third-party
# package - see servers.yaml), so these exercise the generic exclusion mechanism against an
# already-installed server, monkeypatched into SUBAGENT_ONLY_SERVERS for the duration of one test.
# The mechanism is what's under test, not which specific server ends up in that set.


def _utility_server_registry() -> ServerRegistry:
    return ServerRegistry(
        servers=[
            ServerConfig(
                name="utility-mcp-server",
                transport="stdio",
                command=sys.executable,
                args=["-m", "utility_mcp_server.server"],
            )
        ]
    )


async def test_subagent_only_server_absent_from_an_unscoped_main_turn(monkeypatch):
    monkeypatch.setattr(toolset_module, "SUBAGENT_ONLY_SERVERS", frozenset({"utility-mcp-server"}))
    async with MCPClientManager(_utility_server_registry()) as manager:
        await manager.connect_all()
        host = TauCoreHost(manager, settings=_settings())
        # servers=None, allow_subagent_only_servers=False (both defaults) - the main turn's own
        # call shape, whether or not scope_servers_per_turn narrowed anything.
        toolset = await build_toolset(host, _stub_router(), "what time is it?", [])
    assert qualified_tool_name("utility-mcp-server", "get_time") not in set(toolset.tools)


async def test_subagent_only_server_absent_even_when_explicitly_scoped_in(monkeypatch):
    """A heuristic match (or an explicit servers=[...]) must not be able to leak a subagent-only
    server into the MAIN turn's toolset - only allow_subagent_only_servers=True (the sub-agent
    call site) may."""
    monkeypatch.setattr(toolset_module, "SUBAGENT_ONLY_SERVERS", frozenset({"utility-mcp-server"}))
    async with MCPClientManager(_utility_server_registry()) as manager:
        await manager.connect_all()
        host = TauCoreHost(manager, settings=_settings())
        toolset = await build_toolset(
            host, _stub_router(), "what time is it?", [], servers=["utility-mcp-server"]
        )
    assert qualified_tool_name("utility-mcp-server", "get_time") not in set(toolset.tools)


async def test_subagent_only_server_reachable_with_the_subagent_flag(monkeypatch):
    monkeypatch.setattr(toolset_module, "SUBAGENT_ONLY_SERVERS", frozenset({"utility-mcp-server"}))
    async with MCPClientManager(_utility_server_registry()) as manager:
        await manager.connect_all()
        host = TauCoreHost(manager, settings=_settings())
        toolset = await build_toolset(
            host, _stub_router(), "what time is it?", [],
            servers=["utility-mcp-server"], allow_subagent_only_servers=True,
        )
    assert qualified_tool_name("utility-mcp-server", "get_time") in set(toolset.tools)


def test_stealth_browser_mcp_is_registered_subagent_only():
    assert "stealth-browser-mcp" in SUBAGENT_ONLY_SERVERS


def test_passthrough_block_list_is_model_excluded_plus_memory_writes():
    """The passthrough block is derived from MODEL_EXCLUDED_TOOLS, not a hand-copied second list -
    the tripwire against the two drifting apart the way the original gap happened. Phase 15 #1
    widens it by exactly the per-speaker memory writes: those stay in the model's toolset (that
    path stamps `owner`) but must not be reachable through the identity-less passthrough."""
    from tau_core.web.server import _TOOLS_BLOCKED_FROM_PASSTHROUGH

    assert _TOOLS_BLOCKED_FROM_PASSTHROUGH == MODEL_EXCLUDED_TOOLS | PASSTHROUGH_BLOCKED_MEMORY_WRITES
    # The widening is only the writes; the model-excluded admin tools are still blocked here too.
    assert MODEL_EXCLUDED_TOOLS <= _TOOLS_BLOCKED_FROM_PASSTHROUGH


# --- Phase 40: IMAGE_RESULT_TOOLS / _extract_first_image -----------------------------------------


def test_search_images_is_registered_as_image_producing():
    assert ("research-mcp-server", "search_images") in IMAGE_RESULT_TOOLS


def test_extract_first_image_reads_the_real_research_mcp_server_framing():
    """The single most important thing to get right here: toolset.py's parser and
    research-mcp-server's actual _frame()/_neutralise_markers() output must agree on the format,
    or every real search_images call silently produces no image. Builds the exact text a real
    call would return (using research-mcp-server's own functions, not a hand-typed guess at its
    format) and feeds it through the real parser - a genuine cross-package coupling test, not two
    independently-maintained assumptions about a shared string format."""
    from research_mcp_server.search import ImageResult
    from research_mcp_server.server import _frame, _neutralise_markers
    import json as _json

    results = [
        ImageResult(
            title="Golden retriever",
            image_url="https://example.com/dog.jpg",
            source_url="https://example.com/page",
            source="example.com",
        ),
        ImageResult(
            title="Another dog", image_url="https://example.com/dog2.jpg", source_url="", source=""
        ),
    ]
    body = _json.dumps([r.to_dict() for r in results], indent=2)
    text = _frame("image search for 'golden retriever' via searxng (http://searxng:8080)", _neutralise_markers(body))

    image = _extract_first_image(text)
    assert image == {
        "url": "https://example.com/dog.jpg",
        "title": "Golden retriever",
        "source_url": "https://example.com/page",
        "source": "example.com",
    }


def test_extract_first_image_handles_no_results():
    from research_mcp_server.server import _frame, _neutralise_markers

    text = _frame("image search for 'xyzzy nonsense' via searxng (http://searxng:8080)", _neutralise_markers("[]"))
    assert _extract_first_image(text) is None


def test_extract_first_image_ignores_unrelated_text():
    """The failure-message shape (SEARCH_FAILED:/no results) other tools return, and arbitrary
    unrelated text - both must degrade to "no image", never raise."""
    assert _extract_first_image("SEARCH_FAILED: No search backend configured") is None
    assert _extract_first_image("") is None
    assert _extract_first_image("just some plain reply text with no framing at all") is None


def test_extract_first_image_handles_truncated_content():
    """_content_to_text truncates results over 8000 chars with a trailing "...[truncated, N
    chars total]" marker, which breaks the clean JSON-between-markers assumption - must degrade
    gracefully, not raise, same as every other malformed-input case."""
    text = (
        "<<<UNTRUSTED_WEB_CONTENT>>>\nsource: image search for 'x' via searxng\n...\n---\n"
        '[{"title": "cut off mid'
        "...[truncated, 9001 chars total]"
    )
    assert _extract_first_image(text) is None
