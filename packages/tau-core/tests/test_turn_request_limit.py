"""A runaway turn must stop, and must stop honestly.

Sub-agents have been bounded since they were written (SUBAGENT_REQUEST_LIMIT); the agent that
spawns them had no ceiling at all. The 2026-07-28 eval showed a single turn issuing `call_service`
eight to twelve times - and `call_service` is a WRITE tool, so twelve retries is twelve commands
sent to the house, not one slow success.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from tau_core.config import TauCoreSettings
from tau_core.host import TauCoreHost
from tau_core.llm.agent import MAIN_TURN_REQUEST_LIMIT, TauAssistant

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


class _FakeManager:
    """An MCP manager with no servers at all.

    Deliberately empty: this test is about the request ceiling, so the only tool on the toolset
    should be `spawn_subagent` (which TauAssistant always adds). `connected_servers` is needed
    because MemoryTreeBackend.recall consults it on every turn and degrades to no-recall when the
    memory server is absent - which is exactly what we want here.
    """

    def registered_servers(self) -> list[str]:
        return []

    def connected_servers(self) -> list[str]:
        return []


def _settings() -> TauCoreSettings:
    return TauCoreSettings(
        cdg_rules_path=CONFIG_DIR / "cdg_rules.yaml",
        approval_store_path=None,
        # Off so the toolset is built from the whole (stubbed, empty) roster and the test is about
        # the ceiling rather than about scoping.
        scope_servers_per_turn=False,
    )


def _router_ok() -> FunctionModel:
    """A router that always returns high confidence, so nothing is clarified away."""

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        name = info.output_tools[0].name
        return ModelResponse(
            parts=[ToolCallPart(name, {"confidence": 0.99, "reasoning": "ok"})]
        )

    return FunctionModel(respond)


def _subagent_that_returns_immediately() -> FunctionModel:
    """Sub-agents default to the SAME model as the main agent, so a main model that always calls
    spawn_subagent would otherwise recurse into itself and fail on tool-retry limits before the
    request ceiling could bind. Giving the sub-agent its own terminating model isolates the thing
    under test."""

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart("sub-agent done")])

    return FunctionModel(respond)


def test_a_runaway_turn_stops_at_the_ceiling_and_says_so():
    """The model here never stops calling a tool - exactly the loop the eval recorded. Without a
    ceiling this would spin until the turn timeout; with one it must return a reply that admits
    what happened rather than raising or claiming success."""
    calls = {"n": 0}

    def never_finishes(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        calls["n"] += 1
        # spawn_subagent is always present on the main toolset, so it is a tool we can always call.
        return ModelResponse(parts=[ToolCallPart("spawn_subagent", {"task": "x", "servers": []})])

    host = TauCoreHost(_FakeManager(), settings=_settings())
    assistant = TauAssistant(
        host,
        main_model=FunctionModel(never_finishes),
        router_model=_router_ok(),
        subagent_model=_subagent_that_returns_immediately(),
    )

    import asyncio

    turn = asyncio.run(assistant.chat("do something that loops forever"))

    assert calls["n"] <= MAIN_TURN_REQUEST_LIMIT + 1, (
        f"the ceiling did not bind: {calls['n']} model requests for one turn"
    )
    assert "stuck repeating" in turn.reply
    # The honest part: it must not claim the work succeeded, and must warn that side effects may
    # already have landed - the tool calls it made before stopping really happened.
    assert "may have gone through" in turn.reply


def test_a_normal_turn_is_unaffected():
    """The ceiling must not touch turns that behave. A single tool call then an answer is the
    common case and must complete exactly as before."""
    state = {"called": False}

    def one_call_then_answer(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if not state["called"]:
            state["called"] = True
            return ModelResponse(parts=[ToolCallPart("spawn_subagent", {"task": "x", "servers": []})])
        return ModelResponse(parts=[TextPart("Done - all clear.")])

    host = TauCoreHost(_FakeManager(), settings=_settings())
    assistant = TauAssistant(
        host,
        main_model=FunctionModel(one_call_then_answer),
        router_model=_router_ok(),
        subagent_model=_subagent_that_returns_immediately(),
    )

    import asyncio

    turn = asyncio.run(assistant.chat("do one thing"))
    assert turn.reply == "Done - all clear."


def test_the_main_ceiling_is_higher_than_the_subagent_one():
    """A main turn legitimately fans out further than a sub-agent, which is by definition one
    focused task. If these ever invert, a normal multi-step request starts failing."""
    from tau_core.llm.agent import SUBAGENT_REQUEST_LIMIT

    assert MAIN_TURN_REQUEST_LIMIT > SUBAGENT_REQUEST_LIMIT



def test_a_runaway_turn_does_not_write_its_own_failure_into_memory():
    """Phase 35 guard. chat() used to have four copies of the "finish a successful turn" tail;
    collapsing them into _finish_turn made it possible to accidentally start calling the post-turn
    memory learner on the two ERROR paths, which return a canned apology Tau composed about its own
    malfunction. Drafting a memory from "I got stuck repeating myself on that one" would file the
    assistant's failure mode as something learned about the household.

    Nothing caught this before: _learn_from_turn is fire-and-forget and returns None, so the bug
    would have passed every existing test silently. That is why this asserts the negative.
    """
    seen: list[tuple[str, str]] = []

    def never_finishes(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[ToolCallPart("spawn_subagent", {"task": "x", "servers": []})])

    settings = _settings()
    settings.background_memory_learning_enabled = True
    host = TauCoreHost(_FakeManager(), settings=settings)
    assistant = TauAssistant(
        host,
        main_model=FunctionModel(never_finishes),
        router_model=_router_ok(),
        subagent_model=_subagent_that_returns_immediately(),
    )
    assistant._learn_from_turn = lambda user_text, reply, speaker: seen.append((user_text, reply))

    import asyncio

    turn = asyncio.run(assistant.chat("do something that loops forever"))

    assert "stuck repeating" in turn.reply
    assert seen == [], f"the learner ran on an error turn and would have drafted: {seen}"


def test_a_normal_turn_still_reaches_the_memory_learner():
    """The other half of the pair: learn=True is the default for a reason, and a passing error-path
    test would be worthless if the learner had simply stopped running everywhere."""
    seen: list[tuple[str, str]] = []

    def answer(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart("Done - all clear.")])

    settings = _settings()
    settings.background_memory_learning_enabled = True
    host = TauCoreHost(_FakeManager(), settings=settings)
    assistant = TauAssistant(
        host,
        main_model=FunctionModel(answer),
        router_model=_router_ok(),
        subagent_model=_subagent_that_returns_immediately(),
    )
    assistant._learn_from_turn = lambda user_text, reply, speaker: seen.append((user_text, reply))

    import asyncio

    turn = asyncio.run(assistant.chat("do one thing"))
    assert turn.reply == "Done - all clear."
    assert seen == [("do one thing", "Done - all clear.")]
