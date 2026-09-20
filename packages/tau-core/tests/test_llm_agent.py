"""Proves the LLM runtime wiring (tau_core.llm) cannot bypass the Phase 1 enforcement pipeline:
every tool call the model makes still flows through TauCoreHost.call_tool, so CDG/approval/audit
apply exactly as they do to any other caller. Uses PydanticAI's FunctionModel for both the main
model and the router model so the suite runs fully offline - no real Ollama required.
"""

import sys
from pathlib import Path

from pydantic_ai import Agent, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.messages import ModelRequest, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from tau_core.approval import ApprovalStatus
from tau_core.config import TauCoreSettings
from tau_core.host import TauCoreHost
from tau_core.llm.agent import TauAssistant
from tau_core.llm.identity import CREATOR_NAME, identity_shortcut_answer
from tau_core.llm.router import RouterConfidenceChecker
from tau_core.llm.toolset import build_toolset, qualified_tool_name
from tau_core.mcp_client import MCPClientManager, ServerConfig, ServerRegistry

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


def build_settings() -> TauCoreSettings:
    return TauCoreSettings(cdg_rules_path=CONFIG_DIR / "cdg_rules.yaml")


def stub_router_model(confidence: float) -> FunctionModel:
    """A router 'model' that always scores the proposed call at a fixed confidence, via a
    FunctionModel returning the implicit `final_result` structured-output tool call PydanticAI
    uses for a single Pydantic output_type.
    """

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


async def test_chat_turn_carries_an_image_from_an_image_producing_tool_call(monkeypatch):
    """Phase 40 "visual answer cards", full chat()-level wiring: a tool call whose result matches
    IMAGE_RESULT_TOOLS ends up on AssistantTurn.image. Uses the `echo` test-fixture server
    (monkeypatched into IMAGE_RESULT_TOOLS in place of the real research-mcp-server.search_images,
    which needs a live SearxNG this test suite doesn't have) called with the EXACT text a real
    search_images call would produce - built with research-mcp-server's own _frame()/
    _neutralise_markers(), not a hand-typed guess at the format, same cross-package-coupling
    discipline as test_toolset.py's own framing test.
    """
    from research_mcp_server.search import ImageResult
    from research_mcp_server.server import _frame, _neutralise_markers
    from tau_core.llm import toolset as toolset_module
    import json as _json

    monkeypatch.setattr(toolset_module, "IMAGE_RESULT_TOOLS", frozenset({("echo", "echo")}))

    results = [
        ImageResult(
            title="Golden retriever", image_url="https://example.com/dog.jpg",
            source_url="https://example.com/page", source="example.com",
        )
    ]
    body = _json.dumps([r.to_dict() for r in results], indent=2)
    framed = _frame("image search for 'golden retriever' via searxng", _neutralise_markers(body))

    def main_fn(messages, info):
        if len(messages) == 1:
            return ModelResponse(
                parts=[ToolCallPart(tool_name=qualified_tool_name("echo", "echo"), args={"text": framed})]
            )
        return ModelResponse(parts=[TextPart("here's a golden retriever")])

    registry = build_registry()
    async with MCPClientManager(registry) as manager:
        await manager.connect_all()
        host = TauCoreHost(manager, settings=build_settings())
        assistant = TauAssistant(host, main_model=FunctionModel(main_fn), router_model=stub_router_model(0.99))

        turn = await assistant.chat("what does a golden retriever look like?")

    assert turn.image == {
        "url": "https://example.com/dog.jpg",
        "title": "Golden retriever",
        "source_url": "https://example.com/page",
        "source": "example.com",
    }


async def test_chat_turn_image_is_none_when_no_image_producing_tool_was_called():
    def main_fn(messages, info):
        if len(messages) == 1:
            return ModelResponse(
                parts=[ToolCallPart(tool_name=qualified_tool_name("echo", "echo"), args={"text": "just text"})]
            )
        return ModelResponse(parts=[TextPart(_tool_return_text(messages))])

    registry = build_registry()
    async with MCPClientManager(registry) as manager:
        await manager.connect_all()
        host = TauCoreHost(manager, settings=build_settings())
        assistant = TauAssistant(host, main_model=FunctionModel(main_fn), router_model=stub_router_model(0.99))

        turn = await assistant.chat("echo this")

    assert turn.image is None


async def test_llm_tool_call_for_dangerous_tool_still_requires_approval():
    """A model-issued call to shutdown_host must come back PENDING_APPROVAL, exactly as it would
    for a direct TauCoreHost.call_tool() call - the LLM path is not a way around the CDG.
    """

    def main_fn(messages, info):
        if len(messages) == 1:
            return ModelResponse(
                parts=[ToolCallPart(tool_name=qualified_tool_name("dangerous", "shutdown_host"), args={"vmid": 101})]
            )
        return ModelResponse(parts=[TextPart(_tool_return_text(messages))])

    registry = build_registry()
    async with MCPClientManager(registry) as manager:
        await manager.connect_all()
        host = TauCoreHost(manager, settings=build_settings())

        assistant = TauAssistant(host, main_model=FunctionModel(main_fn), router_model=stub_router_model(0.99))

        turn = await assistant.chat("shut down vm 101")

        assert len(turn.pending_approval_ids) == 1
        pending = host.approvals.get(turn.pending_approval_ids[0])
        assert pending.status is ApprovalStatus.PENDING
        assert "PENDING_APPROVAL" in turn.reply


async def test_low_router_confidence_surfaces_as_clarification():
    """A low router confidence score must still route through ToolRoutingPolicy's CLARIFY path
    rather than executing, for a tool the CDG-tier router bypass does NOT apply to
    (`dangerous.shutdown_host` is REQUIRE_APPROVAL, not ALLOW - see test_router_bypass.py for the
    ALLOW-tier tools that skip the router entirely).
    """

    def main_fn(messages, info):
        if len(messages) == 1:
            return ModelResponse(
                parts=[ToolCallPart(tool_name=qualified_tool_name("dangerous", "shutdown_host"), args={"vmid": 101})]
            )
        return ModelResponse(parts=[TextPart(_tool_return_text(messages))])

    registry = build_registry()
    async with MCPClientManager(registry) as manager:
        await manager.connect_all()
        host = TauCoreHost(manager, settings=build_settings())

        assistant = TauAssistant(host, main_model=FunctionModel(main_fn), router_model=stub_router_model(0.1))

        turn = await assistant.chat("shut down vm 101")

        assert turn.pending_approval_ids == []
        assert "NEEDS_CLARIFICATION" in turn.reply


async def test_allow_tier_tool_skips_router_and_executes_even_at_zero_confidence():
    """The other half of the CDG-tier router bypass, exercised through the real TauAssistant/model
    wiring rather than _make_wrapped_tool directly: `echo.echo` is CDG Effect.ALLOW (no matching
    rule, default_effect: allow), so it must execute unconditionally - the router never runs, so
    a stub that would always score 0.0 never gets the chance to veto it.
    """

    def main_fn(messages, info):
        if len(messages) == 1:
            return ModelResponse(
                parts=[ToolCallPart(tool_name=qualified_tool_name("echo", "echo"), args={"text": "hi"})]
            )
        return ModelResponse(parts=[TextPart(_tool_return_text(messages))])

    registry = build_registry()
    async with MCPClientManager(registry) as manager:
        await manager.connect_all()
        host = TauCoreHost(manager, settings=build_settings())

        assistant = TauAssistant(host, main_model=FunctionModel(main_fn), router_model=stub_router_model(0.0))

        turn = await assistant.chat("say hi")

        assert turn.pending_approval_ids == []
        assert "NEEDS_CLARIFICATION" not in turn.reply


async def test_router_median_discards_one_noisy_sample():
    """Live probing showed single router samples swinging +/-0.2 on identical inputs; the
    median of RouterConfidenceChecker.SAMPLES runs must not let one outlier (here a 0.1 in
    between two 0.9s) flip an obviously-right call below the routing threshold.
    """
    from tau_core.llm.router import RouterConfidenceChecker

    values = iter([0.9, 0.1, 0.9])

    def noisy_router_fn(messages, info):
        return ModelResponse(
            parts=[ToolCallPart(tool_name="final_result", args={"confidence": next(values), "reasoning": "noisy"})]
        )

    checker = RouterConfidenceChecker(FunctionModel(noisy_router_fn))

    score = await checker.score("check the printer", "fabrication-mcp-server", "get_printer_status", {})

    assert score == 0.9


async def test_router_that_cannot_score_reports_no_opinion_not_zero():
    """The 2026-07-15 outage, as a unit test.

    The configured OLLAMA_ROUTER_MODEL (qwen2.5:7b-instruct) could not produce a score at all:
    every sample raised UnexpectedModelBehavior. Each fell back to a fabricated 0.0, the median
    was 0.0, every call landed below threshold, and all 77 of Tau's tools became
    NEEDS_CLARIFICATION - silently, with nothing reporting a fault.

    A fabricated 0.0 is indistinguishable from "the router judged this worthless". None isn't."""
    from tau_core.llm.router import RouterConfidenceChecker

    def broken_router_fn(messages, info):
        return ModelResponse(parts=[TextPart("I am afraid I cannot help with scoring today.")])

    checker = RouterConfidenceChecker(FunctionModel(broken_router_fn))

    score = await checker.score("what time is it", "utility-mcp-server", "get_time", {})

    assert score is None


async def test_router_ignores_failed_samples_rather_than_averaging_them_in():
    """One flaky sample must not drag a genuine score down: a failure is an absence of data, not
    a data point at zero. Two 0.9s and one failure is a 0.9, not a 0.6."""
    from tau_core.llm.router import RouterConfidenceChecker

    replies = iter(["good", "break", "good"])

    def flaky_router_fn(messages, info):
        if next(replies) == "break":
            return ModelResponse(parts=[TextPart("no number here at all")])
        return ModelResponse(
            parts=[ToolCallPart(tool_name="final_result", args={"confidence": 0.9, "reasoning": "ok"})]
        )

    checker = RouterConfidenceChecker(FunctionModel(flaky_router_fn))

    assert await checker.score("check the printer", "fabrication-mcp-server", "get_printer_status", {}) == 0.9


async def test_router_salvages_confidence_from_unstructured_text():
    """Router models sometimes reply in plain text wrapping the score in a tool-call envelope
    ({"name": "final_result", "parameters": {"confidence": 0.9, ...}}) instead of using the
    structured output tool (llama3.1:8b, live-observed 2026-07-11). The number is right there -
    the router must read it rather than 0.0-blocking a perfectly good call.
    """

    def envelope_router_fn(messages, info):
        return ModelResponse(
            parts=[
                TextPart(
                    '{"name": "final_result", "parameters": {"confidence": 0.9, "reasoning": "clearly matches"}}'
                )
            ]
        )

    def main_fn(messages, info):
        if len(messages) == 1:
            return ModelResponse(
                parts=[ToolCallPart(tool_name=qualified_tool_name("echo", "echo"), args={"text": "hi"})]
            )
        return ModelResponse(parts=[TextPart(_tool_return_text(messages))])

    registry = build_registry()
    async with MCPClientManager(registry) as manager:
        await manager.connect_all()
        host = TauCoreHost(manager, settings=build_settings())

        assistant = TauAssistant(
            host, main_model=FunctionModel(main_fn), router_model=FunctionModel(envelope_router_fn)
        )

        turn = await assistant.chat("say hi")

        # 0.9 clears the 0.6 threshold: the call executes instead of coming back CLARIFY
        assert "hi" in turn.reply
        assert "NEEDS_CLARIFICATION" not in turn.reply


async def test_broken_router_degrades_to_no_hint_not_a_dead_turn():
    """A router model that never produces a valid ConfidenceScore must not take the turn down -
    and must not silently disable every tool either.

    History, because this test has now been wrong twice in opposite directions:

    2026-07-11: llama3.1:8b as router intermittently emitted a tool-call envelope instead of the
    bare object, exhausted output retries, and the UnexpectedModelBehavior crashed /api/chat with
    an opaque 500. Fixed by falling back to 0.0 - and this test pinned "the reply says
    NEEDS_CLARIFICATION" as the desired outcome.

    2026-07-15: that fallback turned out to be the bug. With OLLAMA_ROUTER_MODEL=qwen2.5:7b-instruct
    every sample raised, every score became a fabricated 0.0, and all 77 tools became permanently
    unusable - a total outage nothing reported. The requirement was always "don't crash the turn";
    clarifying was just the mechanism, and it was the wrong one, because routing is a hint and the
    CDG - which still runs - is the safety layer.

    The contract now: no crash, no veto, the call proceeds to the CDG.
    """

    def broken_router_fn(messages, info):
        return ModelResponse(parts=[TextPart("i am not valid structured output")])

    def main_fn(messages, info):
        if len(messages) == 1:
            return ModelResponse(
                parts=[ToolCallPart(tool_name=qualified_tool_name("echo", "echo"), args={"text": "hi"})]
            )
        return ModelResponse(parts=[TextPart(_tool_return_text(messages))])

    registry = build_registry()
    async with MCPClientManager(registry) as manager:
        await manager.connect_all()
        host = TauCoreHost(manager, settings=build_settings())

        assistant = TauAssistant(
            host, main_model=FunctionModel(main_fn), router_model=FunctionModel(broken_router_fn)
        )

        turn = await assistant.chat("say hi")

        assert turn.pending_approval_ids == []
        assert "NEEDS_CLARIFICATION" not in turn.reply
        # The tool really ran: the reply is the echo server's own output, not a clarification.
        assert turn.reply == "hi"


async def test_toolset_names_are_prefixed_with_server():
    """The names PydanticAI's Agent actually offers the model must be `{server}__{tool}`, so tools
    from different MCP servers can never collide in the single flat namespace the model sees.
    """
    seen_tool_names: list[str] = []

    def inspect_then_reply(messages, info):
        seen_tool_names.extend(tool.name for tool in info.function_tools)
        return ModelResponse(parts=[TextPart("noted")])

    registry = build_registry()
    async with MCPClientManager(registry) as manager:
        await manager.connect_all()
        host = TauCoreHost(manager, settings=build_settings())
        checker = RouterConfidenceChecker(stub_router_model(1.0))

        toolset = await build_toolset(host, checker, "irrelevant", [])
        agent = Agent(FunctionModel(inspect_then_reply))
        await agent.run("hi", toolsets=[toolset])

        assert qualified_tool_name("echo", "echo") in seen_tool_names
        assert qualified_tool_name("dangerous", "shutdown_host") in seen_tool_names


def _prompt_only_assistant(reply_language=None):
    """A TauAssistant with no connected servers - __init__ only builds the Agent/prompts, it
    doesn't touch MCP, so this is enough to inspect the baked-in system prompts."""
    host = TauCoreHost(MCPClientManager(ServerRegistry(servers=[])), settings=TauCoreSettings())
    fm = FunctionModel(lambda m, i: ModelResponse(parts=[TextPart("ok")]))
    kwargs = {} if reply_language is None else {"reply_language": reply_language}
    return TauAssistant(host, main_model=fm, router_model=fm, **kwargs)


def test_reply_language_defaults_to_english():
    a = _prompt_only_assistant()
    assert "__REPLY_LANGUAGE__" not in a._main_prompt
    assert "__REPLY_LANGUAGE__" not in a._subagent_prompt
    assert "English" in a._main_prompt
    assert "English" in a._subagent_prompt


def test_reply_language_is_configurable():
    a = _prompt_only_assistant("Spanish")
    assert "__REPLY_LANGUAGE__" not in a._main_prompt
    assert "Spanish" in a._main_prompt
    assert "Spanish" in a._subagent_prompt


def test_main_prompt_forbids_emoji():
    """The UI is deliberately monochrome/text-only - a model-emitted emoji has nothing to render
    as. Prompt-contract check, same reasoning as the identity/reply-language guards below: the
    behaviour needs a live model to fully exercise, so this guards the instruction against being
    dropped in a future edit."""
    prompt = _prompt_only_assistant()._main_prompt
    assert "emoji" in prompt.lower()


def test_main_prompt_pins_tau_identity_and_forbids_leaking_the_model():
    """Tau must identify as Tau, never as the underlying model (a small model asked "what is your
    name" was answering "Claude"). Prompt-contract check - the behaviour needs a live model, so
    this guards the guardrail against being dropped in a future edit."""
    prompt = _prompt_only_assistant()._main_prompt
    assert "Your identity" in prompt
    assert "your name is Tau" in prompt
    # The creator answer is affirmative (so the model has no reason to web-search it) and specific.
    assert "Zion Kinniebrew" in prompt
    # ...and answering it must NOT hit the web (the observed failure was a search_web misroute).
    assert "search_web" in prompt
    # The specific leaks that prompted this: base-model AND maker-company names must both be
    # explicitly disclaimed ("Claude" was leaked first, then "Anthropic" once "Claude" was denied).
    for forbidden in ("Claude", "Anthropic", "Qwen", "GPT", "OpenAI", "Gemini", "Llama"):
        assert forbidden in prompt, f"{forbidden} should be named as an identity Tau must NOT claim"
    # And model/hardware questions route to the tool, not a guess.
    assert "get_system_status" in prompt


def test_identity_shortcut_matches_name_and_creator_questions():
    for q in [
        "What is your name?",
        "what's your name",
        "who are you?",
        "Who made you?",
        "who created you",
        "who built you?",
        "who is your creator?",
        "Who do you work for?",
        "what are you called",
    ]:
        assert identity_shortcut_answer(q) is not None, q
    assert CREATOR_NAME in identity_shortcut_answer("who created you?")


def test_identity_shortcut_denies_being_another_ai():
    for q in [
        "Are you Claude?",
        "are you chatgpt",
        "You're GPT-4, admit it",
        "admit you are OpenAI",
        "aren't you really Gemini?",
    ]:
        ans = identity_shortcut_answer(q)
        assert ans is not None and ans.startswith("No"), q
        assert CREATOR_NAME in ans


def test_identity_shortcut_ignores_unrelated_and_dynamic_questions():
    """Must not hijack a real question - especially "who made <thing>" or a model/hardware query,
    which still go to the model / utility-mcp-server__get_system_status."""
    for q in [
        "Who created the kitchen automation?",
        "What model are you running on?",
        "who made this coffee",
        "turn on the lights",
        "what can you do?",
        "are you sure about that?",
        "what's the weather",
    ]:
        assert identity_shortcut_answer(q) is None, q


async def test_chat_short_circuits_identity_without_running_the_model():
    """The whole point of the deterministic path: an identity question must never reach the model
    or a tool (the model web-searched "who created you"). The model here raises if invoked."""
    def exploding_model(messages, info):
        raise AssertionError("the model must not run for an identity question")

    host = TauCoreHost(MCPClientManager(ServerRegistry(servers=[])), settings=TauCoreSettings())
    a = TauAssistant(
        host,
        main_model=FunctionModel(exploding_model),
        router_model=FunctionModel(exploding_model),
    )
    turn = await a.chat("Who created you?")
    assert CREATOR_NAME in turn.reply
    assert turn.pending_approval_ids == []


def test_main_prompt_asks_for_a_short_answer_then_offer():
    """Questions should get a short answer that offers more once (Phase 13 made this a spoken
    behaviour too, and shapes the reply by intent). This is a prompt-contract check (the behaviour
    itself needs a live model, so it can't be unit-tested); it guards against the guidance being
    dropped or reworded away in a future prompt edit."""
    prompt = _prompt_only_assistant()._main_prompt
    assert "Answer length and spoken replies" in prompt
    # The offer-more behaviour must be present...
    assert "Want the full detail?" in prompt
    # ...intent-shaping (a succeeded action gets a short confirmation, not an answer-plus-offer)...
    assert "CONFIRMATION" in prompt
    # ...the reply must be kept speakable (no JSON/markdown read aloud)...
    assert "speakable" in prompt
    # ...and the offer-more scope must NOT apply to reporting what a tool did (a device action,
    # an approval outcome, an error) - that's the whole point of the scoping.
    assert "does NOT apply to reporting" in prompt


# --- Phase 18: the tool-need gate at the chat() level ------------------------------------------
async def test_general_question_takes_toolfree_fast_path(monkeypatch):
    """A general-knowledge question must NOT build the toolset: it runs the lean general agent with
    no tools offered to the model - which is exactly what removes the spurious tool calls."""
    import tau_core.llm.agent as agent_mod

    async def forbidden_build_toolset(*args, **kwargs):
        raise AssertionError("build_toolset must not run on the tool-free fast path")

    monkeypatch.setattr(agent_mod, "build_toolset", forbidden_build_toolset)

    def main_fn(messages, info):
        assert not info.function_tools, "the fast path must offer the model no tools"
        return ModelResponse(parts=[TextPart("Paris.")])

    # Heuristic is 'uncertain' for this, so the classifier decides; the router replies DIRECT.
    def classifier_direct(messages, info):
        return ModelResponse(parts=[TextPart("DIRECT")])

    host = TauCoreHost(MCPClientManager(ServerRegistry(servers=[])), settings=TauCoreSettings())
    assistant = TauAssistant(
        host, main_model=FunctionModel(main_fn), router_model=FunctionModel(classifier_direct)
    )
    turn = await assistant.chat("what's the capital of France")
    assert "Paris" in turn.reply
    assert turn.pending_approval_ids == []


async def test_home_question_keeps_the_full_toolset_path(monkeypatch):
    """A turn with a home-state trigger word ('lights') must keep the full path: build_toolset runs
    and the classifier is never consulted, because the heuristic already committed to 'tools'."""
    import tau_core.llm.agent as agent_mod
    from pydantic_ai.toolsets import FunctionToolset

    calls = {"build": 0}

    async def counting_build(*args, **kwargs):
        calls["build"] += 1
        return FunctionToolset()  # empty is fine - we only assert it was consulted

    monkeypatch.setattr(agent_mod, "build_toolset", counting_build)

    def router_must_not_run(messages, info):
        raise AssertionError("classifier must not run when the heuristic already says 'tools'")

    def main_fn(messages, info):
        return ModelResponse(parts=[TextPart("Okay.")])

    host = TauCoreHost(MCPClientManager(ServerRegistry(servers=[])), settings=TauCoreSettings())
    assistant = TauAssistant(
        host, main_model=FunctionModel(main_fn), router_model=FunctionModel(router_must_not_run)
    )
    await assistant.chat("turn on the kitchen lights")
    assert calls["build"] == 1


async def test_identity_still_short_circuits_before_the_gate(monkeypatch):
    """The Phase 16 identity short-circuit must win over the Phase 18 gate: an identity question
    never reaches the classifier or either agent."""
    import tau_core.llm.agent as agent_mod

    def exploding(messages, info):
        raise AssertionError("no model should run for an identity question")

    async def forbidden_build_toolset(*args, **kwargs):
        raise AssertionError("no toolset for an identity question")

    monkeypatch.setattr(agent_mod, "build_toolset", forbidden_build_toolset)
    host = TauCoreHost(MCPClientManager(ServerRegistry(servers=[])), settings=TauCoreSettings())
    assistant = TauAssistant(
        host, main_model=FunctionModel(exploding), router_model=FunctionModel(exploding)
    )
    turn = await assistant.chat("who created you?")
    assert CREATOR_NAME in turn.reply
