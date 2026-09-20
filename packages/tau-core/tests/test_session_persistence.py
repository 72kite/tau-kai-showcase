"""Session persistence (Phase 10.3 #11, project-tau-plan.md §10.3 item 11).

Before this, SessionManager held `_messages` in a plain Python list with no disk I/O at all -
SessionRegistry's own docstring said "sessions reset on bridge restart" outright. These tests
prove a restart (simulated by constructing a fresh TauCoreHost/TauAssistant against the SAME
settings - conftest.isolate_durable_state points TAU_SESSION_STORE_PATH/
TAU_DEVICE_SESSION_STORE_PATH at the same tmp_path for every host built in one test) actually
restores conversation history, for both the default/anonymous session and per-device sessions.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from tau_core.config import TauCoreSettings
from tau_core.host import TauCoreHost
from tau_core.llm.agent import TauAssistant
from tau_core.session import SessionManager
from tau_core.session import store as session_store

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


def _unused_model() -> FunctionModel:
    """A model that's never actually invoked - these tests exercise persistence wiring in
    TauAssistant.__init__, not chat orchestration, so main_model/router_model just need to be
    valid Model instances to satisfy Agent(...)'s constructor."""

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise AssertionError("this model should never be called in a persistence-wiring test")

    return FunctionModel(respond)


class _FakeManager:
    """An MCP manager with no servers - these tests are about persistence wiring, not tool
    calls, so nothing here needs to actually connect anywhere (matches the pattern in
    test_turn_request_limit.py)."""

    def registered_servers(self) -> list[str]:
        return []

    def connected_servers(self) -> list[str]:
        return []


def _settings() -> TauCoreSettings:
    # Deliberately does NOT override session_store_path/device_session_store_path/
    # approval_store_path - conftest's isolate_durable_state fixture already points every one of
    # them at the same tmp_path for the whole test via env vars, which is what makes "construct a
    # second host against the same settings" a valid restart simulation.
    return TauCoreSettings(cdg_rules_path=CONFIG_DIR / "cdg_rules.yaml")


def test_default_session_survives_a_simulated_restart():
    first_host = TauCoreHost(_FakeManager(), settings=_settings())
    first_host.session.add_message("user", "what's the wifi password")
    first_host.session.add_message("assistant", "it's on the fridge")

    second_host = TauCoreHost(_FakeManager(), settings=_settings())

    restored = second_host.session.history()
    assert [(m.role, m.content) for m in restored] == [
        ("user", "what's the wifi password"),
        ("assistant", "it's on the fridge"),
    ]


def test_default_session_clear_persists_the_empty_state():
    first_host = TauCoreHost(_FakeManager(), settings=_settings())
    first_host.session.add_message("user", "hello")
    first_host.session.clear()

    second_host = TauCoreHost(_FakeManager(), settings=_settings())

    assert second_host.session.history() == []


def test_per_device_session_survives_a_simulated_restart():
    settings = _settings()
    first_host = TauCoreHost(_FakeManager(), settings=settings)
    first_assistant = TauAssistant(
        first_host,
        main_model=_unused_model(),
        router_model=_unused_model(),
    )
    device_session = first_assistant._sessions.get_or_create("kitchen-tablet")
    device_session.add_message("user", "dim the lights")
    device_session.add_message("assistant", "done")

    second_host = TauCoreHost(_FakeManager(), settings=settings)
    second_assistant = TauAssistant(second_host, main_model=_unused_model(), router_model=_unused_model())
    restored = second_assistant._sessions.get_or_create("kitchen-tablet")

    assert [(m.role, m.content) for m in restored.history()] == [
        ("user", "dim the lights"),
        ("assistant", "done"),
    ]


def test_per_device_sessions_do_not_cross_contaminate_on_restore():
    settings = _settings()
    first_host = TauCoreHost(_FakeManager(), settings=settings)
    first_assistant = TauAssistant(first_host, main_model=_unused_model(), router_model=_unused_model())
    first_assistant._sessions.get_or_create("kitchen-tablet").add_message("user", "kitchen turn")
    first_assistant._sessions.get_or_create("bedroom-tablet").add_message("user", "bedroom turn")

    second_host = TauCoreHost(_FakeManager(), settings=settings)
    second_assistant = TauAssistant(second_host, main_model=_unused_model(), router_model=_unused_model())

    kitchen = second_assistant._sessions.get_or_create("kitchen-tablet")
    bedroom = second_assistant._sessions.get_or_create("bedroom-tablet")
    assert [m.content for m in kitchen.history()] == ["kitchen turn"]
    assert [m.content for m in bedroom.history()] == ["bedroom turn"]


def test_a_device_never_seen_before_restarts_empty():
    settings = _settings()
    first_host = TauCoreHost(_FakeManager(), settings=settings)
    TauAssistant(first_host, main_model=_unused_model(), router_model=_unused_model())  # writes nothing

    second_host = TauCoreHost(_FakeManager(), settings=settings)
    second_assistant = TauAssistant(second_host, main_model=_unused_model(), router_model=_unused_model())

    assert second_assistant._sessions.get_or_create("never-seen").history() == []


def test_session_manager_on_change_fires_on_add_and_clear():
    calls: list[str] = []
    session = SessionManager(on_change=lambda mgr: calls.append(mgr.session_id))

    session.add_message("user", "hi")
    session.clear()

    assert len(calls) == 2
    assert all(c == session.session_id for c in calls)


def test_session_manager_on_change_failure_does_not_break_add_message():
    """A durability failure must not break the chat path itself - see SessionManager._notify."""

    def boom(_mgr):
        raise RuntimeError("disk is on fire")

    session = SessionManager(on_change=boom)
    session.add_message("user", "still works")

    assert [m.content for m in session.history()] == ["still works"]


def test_session_manager_initial_messages_are_trimmed_to_max():
    from tau_core.session.manager import Message

    seeded = [Message(role="user", content=f"msg {i}") for i in range(5)]
    session = SessionManager(max_messages=3, initial_messages=seeded)

    assert [m.content for m in session.history()] == ["msg 2", "msg 3", "msg 4"]


def test_corrupt_session_store_falls_back_to_empty_history(tmp_path):
    path = tmp_path / "sessions.json"
    path.write_text("not valid json at all {{{", encoding="utf-8")

    assert session_store.load_session_history(path, None) == []


def test_corrupt_session_map_falls_back_to_empty_registry(tmp_path):
    path = tmp_path / "device_sessions.json"
    path.write_text("not valid json at all {{{", encoding="utf-8")

    assert session_store.load_session_map(path, None) == {}
