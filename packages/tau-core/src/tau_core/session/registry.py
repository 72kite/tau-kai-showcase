"""Per-device conversation sessions (Phase 12, §10.1 #3 / Tier 1 #8).

The bug this closes: before this, `TauCoreHost` owned exactly **one** `SessionManager`, and
`TauAssistant.chat` appended every device's turns into it. `X-Tau-Device-Id` scoped only the
*rendered* transcript (Phase 6.D), so device B's conversation was still in the model's context
when device A asked - 6.D's privacy goal was met on the glass but not where it counts, inside the
prompt. This registry gives each identified device its own rolling history, so no device's words
ever enter another device's turn.

Design, matching the rest of tau-core's conventions:
- A **factory**, not a stored config, builds each session, so the registry doesn't need to know
  how a `SessionManager` is wired (max_messages, which memory backend) - `TauAssistant` owns that
  knowledge and passes a closure. Mirrors how `host.session` was constructed.
- **LRU-capped** (an `OrderedDict`, most-recently-used at the end). A home has a handful of
  kiosks, but the device id is a client-supplied opaque string (see web/devices.py), so an
  unbounded dict is a memory-growth vector for anything spraying ids. Past the cap the
  least-recently-active session is dropped; that device just starts fresh next turn, which is a
  benign outcome, not a lost approval or a dropped tool call.
- **In-process**, like `DeviceRegistry` and the audit ring buffer: sessions reset on bridge
  restart. Durable session history is Phase 10.3 #11 (session persistence + FTS5), tracked
  separately; this change is about isolation, not durability.

Not thread-locked: the bridge is single-threaded asyncio and `get_or_create` has no `await`
between its read and its write, so two concurrent chat turns for a new key cannot race to
double-create.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Callable

from tau_core.session.manager import SessionManager


class SessionRegistry:
    """Maps an opaque session key (a device id) to its own `SessionManager`, creating one on
    first use via the supplied factory and evicting the least-recently-used past `max_sessions`.
    """

    def __init__(self, factory: Callable[[str], SessionManager], max_sessions: int = 64):
        if max_sessions < 1:
            raise ValueError("max_sessions must be at least 1")
        self._factory = factory
        self._max_sessions = max_sessions
        self._sessions: "OrderedDict[str, SessionManager]" = OrderedDict()

    def get_or_create(self, key: str) -> SessionManager:
        """Return the session for `key`, creating it (and evicting the LRU past the cap) if new.
        Touching an existing key marks it most-recently-used so it survives eviction."""
        existing = self._sessions.get(key)
        if existing is not None:
            self._sessions.move_to_end(key)
            return existing
        session = self._factory(key)
        self._sessions[key] = session
        self._sessions.move_to_end(key)
        while len(self._sessions) > self._max_sessions:
            # popitem(last=False) drops the front = the least-recently-used entry.
            self._sessions.popitem(last=False)
        return session

    def get(self, key: str) -> SessionManager | None:
        """The session for `key` without creating one, and without changing LRU order."""
        return self._sessions.get(key)

    def keys(self) -> list[str]:
        """Known session keys, most-recently-used last (eviction order is front-first)."""
        return list(self._sessions.keys())

    def __len__(self) -> int:
        return len(self._sessions)
