"""Post-turn background learning: a second, independent trigger for draft_memory that doesn't
depend on the conversational model noticing mid-turn (see tau_core.llm.memory_learner's module
docstring for why that's needed). These tests drive TauAssistant._draft_from_turn/_learn_from_turn
directly against a stubbed learner and a recording host.call_tool, so they pin the wiring without
needing a live Ollama or a real memory-mcp-server subprocess.
"""

from pathlib import Path

from mcp.types import CallToolResult, TextContent
from pydantic_ai import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from tau_core.config import TauCoreSettings
from tau_core.host import TauCoreHost, ToolCallOutcome, ToolCallStatus
from tau_core.llm.agent import TauAssistant
from tau_core.llm.memory_learner import DraftCandidate, PostTurnMemoryLearner
from tau_core.mcp_client import MCPClientManager, ServerNotConnectedError, ServerRegistry

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


def _settings(**overrides) -> TauCoreSettings:
    return TauCoreSettings(
        cdg_rules_path=CONFIG_DIR / "cdg_rules.yaml",
        approval_store_path=None,
        session_store_path=None,
        device_session_store_path=None,
        **overrides,
    )


def _dummy_model() -> FunctionModel:
    """A model stub that's never actually invoked - these tests override memory_learner directly
    and only exercise TauAssistant's own bookkeeping around it."""

    def respond(messages, info):
        return ModelResponse(parts=[TextPart("unused")])

    return FunctionModel(respond)


class _FixedLearner:
    def __init__(self, candidate: DraftCandidate | None):
        self._candidate = candidate
        self.calls = 0

    async def extract(self, user_text: str, assistant_reply: str) -> DraftCandidate | None:
        self.calls += 1
        return self._candidate


class _RecordingCallTool:
    def __init__(self, *, raises: Exception | None = None):
        self.calls: list[tuple[str, str, dict, float | None]] = []
        self._raises = raises

    async def __call__(self, server, tool, arguments, *, confidence=1.0, **kwargs):
        self.calls.append((server, tool, dict(arguments), confidence))
        if self._raises is not None:
            raise self._raises
        return ToolCallOutcome(
            status=ToolCallStatus.EXECUTED,
            reason="ok",
            result=CallToolResult(content=[TextContent(type="text", text="done")]),
        )


def _build_assistant(settings: TauCoreSettings | None = None) -> TauAssistant:
    manager = MCPClientManager(ServerRegistry(servers=[]))
    host = TauCoreHost(manager, settings=settings or _settings())
    return TauAssistant(host, main_model=_dummy_model(), router_model=_dummy_model())


async def test_draft_from_turn_calls_draft_memory_with_stamped_owner():
    assistant = _build_assistant()
    assistant.memory_learner = _FixedLearner(
        DraftCandidate(should_draft=True, title="Kitchen lighting", content="Prefers dim lights after 22:00")
    )
    recorder = _RecordingCallTool()
    assistant.host.call_tool = recorder

    await assistant._draft_from_turn("I like the kitchen lights dim after 10pm", "Got it.", "zion")

    assert recorder.calls == [
        (
            "memory-mcp-server",
            "draft_memory",
            {"title": "Kitchen lighting", "content": "Prefers dim lights after 22:00", "owner": "zion"},
            None,
        )
    ]


async def test_draft_from_turn_defaults_title_when_learner_omits_it():
    assistant = _build_assistant()
    assistant.memory_learner = _FixedLearner(DraftCandidate(should_draft=True, title="", content="Some fact"))
    recorder = _RecordingCallTool()
    assistant.host.call_tool = recorder

    await assistant._draft_from_turn("text", "reply", "amara")

    assert recorder.calls[0][2]["title"] == "Observation"


async def test_draft_from_turn_does_nothing_when_learner_declines():
    assistant = _build_assistant()
    assistant.memory_learner = _FixedLearner(DraftCandidate(should_draft=False))
    recorder = _RecordingCallTool()
    assistant.host.call_tool = recorder

    await assistant._draft_from_turn("what's the weather", "It's sunny.", "zion")

    assert recorder.calls == []


async def test_draft_from_turn_does_nothing_when_learner_returns_none():
    """The learner itself already fails toward None on any error (memory_learner.py) - this just
    confirms the caller treats that the same as should_draft=False rather than crashing."""
    assistant = _build_assistant()
    assistant.memory_learner = _FixedLearner(None)
    recorder = _RecordingCallTool()
    assistant.host.call_tool = recorder

    await assistant._draft_from_turn("text", "reply", "zion")

    assert recorder.calls == []


async def test_draft_from_turn_swallows_a_disconnected_memory_server():
    assistant = _build_assistant()
    assistant.memory_learner = _FixedLearner(DraftCandidate(should_draft=True, title="t", content="c"))
    assistant.host.call_tool = _RecordingCallTool(raises=ServerNotConnectedError("memory-mcp-server"))

    # Must not raise - a background learner failing must never surface anywhere the caller notices.
    await assistant._draft_from_turn("text", "reply", "zion")


async def test_learn_from_turn_schedules_a_background_task_when_enabled(monkeypatch):
    assistant = _build_assistant(_settings(background_memory_learning_enabled=True))
    scheduled = []
    monkeypatch.setattr(
        "tau_core.llm.agent.asyncio.create_task", lambda coro: scheduled.append(coro) or coro.close()
    )

    assistant._learn_from_turn("text", "reply", "zion")

    assert len(scheduled) == 1


async def test_learn_from_turn_does_nothing_when_disabled(monkeypatch):
    assistant = _build_assistant(_settings(background_memory_learning_enabled=False))
    scheduled = []
    monkeypatch.setattr(
        "tau_core.llm.agent.asyncio.create_task", lambda coro: scheduled.append(coro) or coro.close()
    )

    assistant._learn_from_turn("text", "reply", "zion")

    assert scheduled == []


def _stub_learner_model(should_draft: bool, title: str = "", content: str = "") -> FunctionModel:
    def respond(messages, info):
        return ModelResponse(
            parts=[ToolCallPart(tool_name="final_result", args={"should_draft": should_draft, "title": title, "content": content})]
        )

    return FunctionModel(respond)


async def test_learner_extract_parses_structured_output():
    learner = PostTurnMemoryLearner(_stub_learner_model(True, "Kitchen lighting", "Dim after 22:00"))

    candidate = await learner.extract("I like it dim after 10pm", "Got it.")

    assert candidate == DraftCandidate(should_draft=True, title="Kitchen lighting", content="Dim after 22:00")


async def test_learner_extract_fails_safe_to_none_on_model_error():
    def broken(messages, info):
        raise RuntimeError("boom")

    learner = PostTurnMemoryLearner(FunctionModel(broken))

    assert await learner.extract("text", "reply") is None
