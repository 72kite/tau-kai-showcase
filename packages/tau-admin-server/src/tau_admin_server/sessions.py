"""Login sessions for the admin panel (Phase 38). Same shape as tau-core's DeviceRegistry token
handling: a random opaque bearer token is minted and returned exactly once at login, only its
sha256 hash is ever persisted, and verification is a constant-time compare against that hash.
Session state (not just the credential) is durable by default - a restart mid-use shouldn't force
every open admin tab to log back in.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import tempfile
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from tau_admin_server import crypto_store

logger = logging.getLogger(__name__)


@dataclass
class _Session:
    token_hash: str
    username: str
    created_at: float
    expires_at: float


class SessionStore:
    def __init__(
        self,
        store_path: str | Path | None = None,
        encryption_key: bytes | None = None,
        ttl_seconds: float = 12 * 60 * 60,
    ) -> None:
        self._sessions: dict[str, _Session] = {}  # keyed by token_hash
        self._lock = threading.RLock()
        self._store_path = Path(store_path) if store_path else None
        self._key = encryption_key
        self._ttl = ttl_seconds
        if self._store_path is not None:
            self._load()

    def _load(self) -> None:
        path = self._store_path
        if path is None or not path.exists():
            return
        try:
            raw = crypto_store.read_json(path, self._key) or {}
            sessions = [_Session(**item) for item in raw.get("sessions", [])]
        except (OSError, ValueError, KeyError, TypeError):
            logger.exception("Could not read session store at %s; starting with no sessions", path)
            return
        now = time.time()
        self._sessions = {s.token_hash: s for s in sessions if s.expires_at > now}

    def _save_locked(self) -> None:
        path = self._store_path
        if path is None:
            return
        payload = {"sessions": [asdict(s) for s in self._sessions.values()]}
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                "wb", dir=path.parent, prefix=path.name, suffix=".tmp", delete=False
            ) as handle:
                handle.write(crypto_store.write_json_bytes(payload, self._key))
                temp_name = handle.name
            os.replace(temp_name, path)
        except OSError:
            logger.exception("Could not persist session store to %s", path)

    def create(self, username: str) -> str:
        """Mints a new session for `username`, returning the raw bearer token - this is the only
        place it ever exists outside the caller's response body."""
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        now = time.time()
        with self._lock:
            self._sessions[token_hash] = _Session(
                token_hash=token_hash, username=username, created_at=now, expires_at=now + self._ttl
            )
            self._save_locked()
        return token

    def verify(self, token: str) -> str | None:
        """Returns the session's username if `token` is live and unexpired, else None. Constant-
        time compare against each stored hash - there's normally exactly one session, so this
        isn't the O(n) concern it would be at device-registry scale."""
        if not token:
            return None
        candidate = hashlib.sha256(token.encode("utf-8")).hexdigest()
        now = time.time()
        with self._lock:
            expired = [h for h, s in self._sessions.items() if s.expires_at <= now]
            for h in expired:
                del self._sessions[h]
            if expired:
                self._save_locked()
            for token_hash, session in self._sessions.items():
                if hmac.compare_digest(token_hash, candidate):
                    return session.username
        return None

    def revoke(self, token: str) -> bool:
        """Logout. Returns whether a live session actually matched (not that it matters much to
        the caller - logout is idempotent either way)."""
        if not token:
            return False
        candidate = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with self._lock:
            for token_hash in list(self._sessions):
                if hmac.compare_digest(token_hash, candidate):
                    del self._sessions[token_hash]
                    self._save_locked()
                    return True
        return False
