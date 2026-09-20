from __future__ import annotations

from typing import Protocol


class MemoryBackend(Protocol):
    """Interface Tau Core's session manager uses for long-term recall.

    Phase 2.4 (memory-mcp-server) will provide a real implementation backed by a vector DB.
    Until that server exists, NullMemoryBackend keeps the session manager fully usable.
    """

    async def remember(self, session_id: str, text: str) -> None: ...

    async def recall(
        self, session_id: str, query: str, limit: int = 5, owner: str = ""
    ) -> list[str]:
        # `owner` scopes recall to the voice-identified speaker (Phase 13.5): their own memories
        # plus shared ones, never another person's. "" = unknown speaker (shared only).
        ...


class NullMemoryBackend:
    """No-op backend: nothing is persisted, recall always returns empty."""

    async def remember(self, session_id: str, text: str) -> None:
        return None

    async def recall(
        self, session_id: str, query: str, limit: int = 5, owner: str = ""
    ) -> list[str]:
        return []
