"""The admin account (Phase 38): one username + Argon2id-hashed password, not a user-management
system - "a separate sign-in" for the control panel, deliberately singular. Bootstrapped once from
env vars on first run, changeable afterward via POST /change-password; never resettable by
re-setting the bootstrap env vars (see settings.py's bootstrap_username/bootstrap_password
docstring) - that would turn "forgot the password" into "read the .env file", which defeats having
a password at all.
"""

from __future__ import annotations

import logging
import os
import tempfile
import threading
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHash, VerifyMismatchError

from tau_admin_server import crypto_store

logger = logging.getLogger(__name__)

# A fixed, never-matching hash to verify against when the username doesn't match, so a wrong
# username and a wrong password take the same wall-clock time - otherwise "verify() skipped
# entirely" vs. "verify() ran a real Argon2id pass" is a timing oracle for username existence.
_DUMMY_HASH = PasswordHasher().hash("not-the-real-password-just-here-for-timing")


class AdminCredentialStore:
    def __init__(self, store_path: str | Path, encryption_key: bytes | None = None) -> None:
        self._path = Path(store_path)
        self._key = encryption_key
        self._hasher = PasswordHasher()
        self._lock = threading.Lock()

    def exists(self) -> bool:
        return self._path.exists()

    def bootstrap(self, username: str, password: str) -> bool:
        """Creates the account if (and only if) none exists yet. Returns whether it created one -
        False means a credential store was already there and this was correctly a no-op."""
        with self._lock:
            if self._path.exists():
                return False
            self._write_locked(username, self._hasher.hash(password))
            return True

    def verify(self, username: str, password: str) -> bool:
        data = self._read()
        if data is None or username != data.get("username"):
            # No real hash to check against (unknown username, or no account yet at all) - run a
            # verify anyway so the timing looks identical to the real-account path below.
            try:
                self._hasher.verify(_DUMMY_HASH, password)
            except VerifyMismatchError:
                pass
            return False
        try:
            self._hasher.verify(data["password_hash"], password)
        except (VerifyMismatchError, InvalidHash):
            return False
        return True

    def set_password(self, username: str, new_password: str) -> None:
        with self._lock:
            self._write_locked(username, self._hasher.hash(new_password))

    def _read(self) -> dict | None:
        if not self._path.exists():
            return None
        try:
            return crypto_store.read_json(self._path, self._key)
        except (OSError, ValueError, KeyError, TypeError):
            logger.exception("Could not read admin credential store at %s", self._path)
            return None

    def _write_locked(self, username: str, password_hash: str) -> None:
        """Caller must hold self._lock. Atomic (temp file + os.replace), same pattern as
        DeviceRegistry._save_locked - a half-written credential store is worse than a stale one."""
        payload = {"username": username, "password_hash": password_hash}
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "wb", dir=self._path.parent, prefix=self._path.name, suffix=".tmp", delete=False
        ) as handle:
            handle.write(crypto_store.write_json_bytes(payload, self._key))
            temp_name = handle.name
        os.replace(temp_name, self._path)
