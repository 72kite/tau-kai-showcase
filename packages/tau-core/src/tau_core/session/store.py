"""Durable conversation history (Phase 10.3 #11, session persistence).

Before this, `SessionManager` held its `_messages` list in a plain Python attribute with no
disk I/O anywhere - `SessionRegistry`'s own docstring said so outright: "sessions reset on
bridge restart". Mirrors the atomic-write/encrypted-at-rest idiom `PendingActionQueue` already
uses (see `tau_core.approval.queue`): a temp file + `os.replace` so a half-written store is never
worse than a stale one, and `crypto_store` so history is encrypted at rest under TAU_MASTER_KEY
exactly like the approval queue and device registry already are.

Two shapes, because there are two kinds of session file:
- `load_session_history`/`save_session_history` - ONE session's messages in a file
  (`{"messages": [...]}`), for `TauCoreHost.session`, the single default/anonymous session.
- `load_session_map`/`save_session_map` - MANY sessions keyed by id in one file
  (`{"sessions": {key: [...]}}`), for `SessionRegistry`'s per-device sessions (Phase 12).
"""

from __future__ import annotations

import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path

from tau_core import crypto_store
from tau_core.session.manager import Message

logger = logging.getLogger(__name__)


def _atomic_write(path: Path, encryption_key: bytes | None, payload: dict) -> None:
    """Failures are logged, never raised - losing durability must not take down the chat path
    itself, the same reasoning PendingActionQueue._save_locked documents."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "wb", dir=path.parent, prefix=path.name, suffix=".tmp", delete=False
        ) as handle:
            handle.write(crypto_store.write_json_bytes(payload, encryption_key))
            temp_name = handle.name
        os.replace(temp_name, path)
    except OSError:
        logger.exception("Could not persist session store to %s", path)


def _messages_to_dicts(messages: list[Message]) -> list[dict]:
    return [
        {"role": m.role, "content": m.content, "name": m.name, "timestamp": m.timestamp.isoformat()}
        for m in messages
    ]


def _dicts_to_messages(items: list[dict]) -> list[Message]:
    out: list[Message] = []
    for item in items:
        try:
            out.append(
                Message(
                    role=item["role"],
                    content=item["content"],
                    name=item.get("name"),
                    timestamp=datetime.fromisoformat(item["timestamp"]),
                )
            )
        except (KeyError, ValueError, TypeError):
            # One corrupt entry must not lose the rest of a real conversation.
            continue
    return out


def load_session_history(path: str | Path | None, encryption_key: bytes | None) -> list[Message]:
    if path is None:
        return []
    p = Path(path)
    if not p.exists():
        return []
    try:
        raw = crypto_store.read_json(p, encryption_key) or {}
        return _dicts_to_messages(raw.get("messages", []))
    except (OSError, ValueError, KeyError, TypeError):
        logger.exception("Could not read session store at %s; starting with empty history", p)
        return []


def save_session_history(
    path: str | Path | None, encryption_key: bytes | None, messages: list[Message]
) -> None:
    if path is None:
        return
    _atomic_write(Path(path), encryption_key, {"messages": _messages_to_dicts(messages)})


def load_session_map(
    path: str | Path | None, encryption_key: bytes | None
) -> dict[str, list[Message]]:
    if path is None:
        return {}
    p = Path(path)
    if not p.exists():
        return {}
    try:
        raw = crypto_store.read_json(p, encryption_key) or {}
        return {key: _dicts_to_messages(items) for key, items in raw.get("sessions", {}).items()}
    except (OSError, ValueError, KeyError, TypeError):
        logger.exception("Could not read session store at %s; starting with empty registry", p)
        return {}


def save_session_map(
    path: str | Path | None, encryption_key: bytes | None, sessions: dict[str, list[Message]]
) -> None:
    if path is None:
        return
    _atomic_write(
        Path(path),
        encryption_key,
        {"sessions": {key: _messages_to_dicts(items) for key, items in sessions.items()}},
    )
