"""A turn whose final output never validates must degrade honestly, not crash.

Found live 2026-09-08 during Phase 36's model-sweep measurement: a small/mid model asked to use
the real 15-server toolset can call a tool with malformed arguments on every attempt, exhausting
pydantic-ai's retries and raising UnexpectedModelBehavior straight out of TauAssistant.chat() -
previously uncaught there (only UsageLimitExceeded was), so it would have surfaced as a raw
exception (a 502 out of /api/chat on a live turn) instead of the honest "I couldn't do that"
reply every other failure mode in chat() already returns.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from tau_core.config import TauCoreSettings
from tau_core.host import TauCoreHost
from tau_core.llm.agent import TauAssistant

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


class _FakeManager:
    """Same empty-registry stand-in as test_turn_request_limit.py - only spawn_subagent's
    schema matters here, so no real domain server needs to be connected."""

    def registered_servers(self) -> list[str]:
        return []

    def connected_servers(self) -> list[str]:
        return []


def _settings() -> TauCoreSettings:
    return TauCoreSettings(
        cdg_rules_path=CONFIG_DIR / "cdg_rules.yaml",
        approval_store_path=None,
        scope_servers_per_turn=False,
    )


def _router_ok() -> FunctionModel:
    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        name = info.output_tools[0].name
        return ModelResponse(parts=[ToolCallPart(name, {"confidence": 0.99, "reasoning": "ok"})])

    return FunctionModel(respond)


def test_a_turn_with_unvalidatable_output_degrades_instead_of_raising():
    """`servers` must be a list[str]; handing spawn_subagent an int on every attempt fails
    pydantic-ai's argument validation until retries are exhausted, raising
    UnexpectedModelBehavior - exactly the failure mode a small model hit live on real cases
    during the sweep (three ERROR verdicts, all this exception, all on qwen2.5:7b-instruct)."""

    def always_malformed(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[ToolCallPart("spawn_subagent", {"task": "x", "servers": 12345})])

    host = TauCoreHost(_FakeManager(), settings=_settings())
    assistant = TauAssistant(
        host, main_model=FunctionModel(always_malformed), router_model=_router_ok()
    )

    import asyncio

    turn = asyncio.run(assistant.chat("do something malformed"))

    # Must not raise - the whole point of the fix. Must also tell the truth: it didn't succeed,
    # and anything it already did before failing might be real (same honesty bar as the
    # UsageLimitExceeded path this mirrors).
    assert "couldn't put together a clear answer" in turn.reply
    assert "may have gone through" in turn.reply
