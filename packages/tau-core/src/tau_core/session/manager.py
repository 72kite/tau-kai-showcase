from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Literal

from tau_core.session.backend import MemoryBackend, NullMemoryBackend

logger = logging.getLogger(__name__)

Role = Literal["system", "user", "assistant", "tool"]


@dataclass(frozen=True)
class Message:
    role: Role
    content: str
    name: str | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class SessionManager:
    """Holds rolling conversation state for one Tau session and bridges to long-term memory.

    The rolling window keeps the most recent `max_messages`, always preserving a leading
    system message (if any) so persona/instructions never get evicted by chat volume.
    """

    def __init__(
        self,
        session_id: str | None = None,
        memory_backend: MemoryBackend | None = None,
        max_messages: int = 50,
        initial_messages: list[Message] | None = None,
        on_change: Callable[["SessionManager"], None] | None = None,
    ):
        self.session_id = session_id or str(uuid.uuid4())
        self._memory = memory_backend or NullMemoryBackend()
        self._max_messages = max_messages
        self._messages: list[Message] = list(initial_messages) if initial_messages else []
        self._trim()
        # Fires after every mutation (Phase 10.3 #11, session persistence) - deliberately a
        # caller-supplied hook rather than a store_path/encryption_key pair on this class
        # directly, because the two callers need different shapes on disk: TauCoreHost.session
        # is the only occupant of its own file, but SessionRegistry's per-device sessions all
        # share ONE file keyed by device id (tau_core.session.store has both shapes). Whoever
        # owns the file decides how to write it; this class only reports that it changed.
        self._on_change = on_change

    def add_message(self, role: Role, content: str, name: str | None = None) -> Message:
        message = Message(role=role, content=content, name=name)
        self._messages.append(message)
        self._trim()
        self._notify()
        return message

    def history(self) -> list[Message]:
        return list(self._messages)

    def _trim(self) -> None:
        if len(self._messages) <= self._max_messages:
            return
        pinned = self._messages[:1] if self._messages[0].role == "system" else []
        rest = self._messages[len(pinned):]
        overflow = len(self._messages) - self._max_messages
        self._messages = pinned + rest[overflow:]

    def _notify(self) -> None:
        if self._on_change is None:
            return
        try:
            self._on_change(self)
        except Exception:
            # A durability failure must not break the chat path itself - same reasoning as
            # PendingActionQueue._save_locked's own try/except.
            logger.exception("session on_change callback failed for session %s", self.session_id)

    async def remember(self, text: str) -> None:
        await self._memory.remember(self.session_id, text)

    async def recall(self, query: str, limit: int = 5, owner: str = "") -> list[str]:
        # `owner` = the identified speaker this turn (Phase 13.5); passed through to the backend so
        # recall is scoped per-speaker, not global. The session itself is per-device (Phase 12);
        # the speaker is per-turn, which is why it rides the call rather than the session id.
        return await self._memory.recall(self.session_id, query, limit, owner)

    def clear(self) -> None:
        self._messages.clear()
        self._notify()
