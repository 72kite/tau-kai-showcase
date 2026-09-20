from tau_core.session import MemoryBackend, SessionManager


class FakeMemoryBackend:
    def __init__(self):
        self.remembered: list[tuple[str, str]] = []

    async def remember(self, session_id: str, text: str) -> None:
        self.remembered.append((session_id, text))

    async def recall(
        self, session_id: str, query: str, limit: int = 5, owner: str = ""
    ) -> list[str]:
        return [text for sid, text in self.remembered if sid == session_id][:limit]


def test_add_message_and_history_order():
    session = SessionManager()
    session.add_message("user", "hello")
    session.add_message("assistant", "hi there")
    history = session.history()
    assert [m.role for m in history] == ["user", "assistant"]
    assert history[1].content == "hi there"


def test_rolling_window_trims_oldest_but_keeps_system_message():
    session = SessionManager(max_messages=3)
    session.add_message("system", "you are Tau")
    for i in range(5):
        session.add_message("user", f"message {i}")

    history = session.history()
    assert history[0].role == "system"
    assert history[0].content == "you are Tau"
    assert len(history) == 3
    assert [m.content for m in history[1:]] == ["message 3", "message 4"]


def test_clear_empties_history():
    session = SessionManager()
    session.add_message("user", "hello")
    session.clear()
    assert session.history() == []


async def test_recall_defaults_to_empty_without_backend():
    session = SessionManager()
    assert await session.recall("anything") == []


async def test_remember_and_recall_roundtrip_with_backend():
    backend = FakeMemoryBackend()
    session = SessionManager(memory_backend=backend)
    await session.remember("Zion prefers terse responses")
    results = await session.recall("preferences")
    assert results == ["Zion prefers terse responses"]


def test_memory_backend_protocol_is_satisfied_by_fake():
    backend: MemoryBackend = FakeMemoryBackend()
    assert hasattr(backend, "remember") and hasattr(backend, "recall")
