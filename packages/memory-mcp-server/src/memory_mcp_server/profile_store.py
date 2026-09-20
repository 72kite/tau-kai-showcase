from __future__ import annotations

from pathlib import Path
from typing import Any

from memory_mcp_server import crypto_store

DEFAULT_ACCESS_LEVEL = "unknown"


class ProfileStore:
    """JSON-file-backed store for person_id -> {access_level, ...} profiles. Separate from
    EmbeddingStore since a person's access level applies once, not once per face/voice sample
    stored for them.
    """

    def __init__(self, path: Path):
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Encrypted at rest (Phase 10) when TAU_MASTER_KEY is set; plaintext, unchanged, if unset.
        # Cached in crypto_store.resolve_key, so re-deriving on every ProfileStore(...) call
        # (server.py's _profiles() constructs a fresh one per tool invocation) is cheap.
        self._key = crypto_store.resolve_key(self._path)
        self._data: dict[str, dict[str, Any]] = crypto_store.read_json(path, self._key) if path.exists() else {}

    def get(self, person_id: str) -> dict[str, Any] | None:
        return self._data.get(person_id)

    def list_all(self) -> dict[str, dict[str, Any]]:
        return dict(self._data)

    def upsert(self, person_id: str, **fields: Any) -> dict[str, Any]:
        profile = self._data.setdefault(person_id, {"access_level": DEFAULT_ACCESS_LEVEL})
        profile.update(fields)
        self._save()
        return profile

    def set_access_level(self, person_id: str, access_level: str) -> dict[str, Any]:
        return self.upsert(person_id, access_level=access_level)

    def _save(self) -> None:
        self._path.write_bytes(crypto_store.write_json_bytes(self._data, self._key))
