"""Device identity for the multi-device bridge (Phase 6.D), extended with device-approval state
and bearer tokens (Phase 27.A).

The bug this is the foundation for: before 6.D the bridge had no concept of *which* client is
talking to it, so ui-bridge-mcp-server held one global transcript that every tablet polled -
every device saw everyone's conversation. Per-device chat isolation needs a stable per-client id
first; this module is that id's bookkeeping.

A device id is opaque (a client-generated UUID in localStorage). This module never mints ids -
the client owns its identity; the bridge only records names, last-seen times, and (as of 27.A)
admin-approval state against it.

Persistence (27.A): device *identity* (activity, names) was in-process-only, matching the audit
ring-buffer's precedent - fine when nothing durable hung off it. Approval state changes that:
an approved device losing its minted token on every bridge restart would force re-approval on
every deploy, which defeats the point of a "long-lived" credential. So this module now optionally
persists to a `crypto_store`-encrypted JSON file, in the same shape as `approvals.json` - see
`approval/queue.py`'s `PendingActionQueue` for the pattern this mirrors (atomic temp-file +
`os.replace`, corrupt-store-must-not-block-boot). `store_path=None` (the default, and what every
existing test uses) keeps the old pure in-memory behaviour.

**What is deliberately NOT persisted on every call:** `touch()` runs on every device-bearing
request - a listening kiosk polls roughly once per second - so writing to disk there would turn
routine traffic into constant disk I/O for data (last-seen churn) that doesn't need to survive a
restart; a still-connected device simply re-touches itself on its next request. Only `approve()`
triggers a write, because only approval state has to survive a restart for 27.A's stated purpose -
but that write snapshots the *whole* in-memory registry (simplest correct option; there is only
one file), so any device touched earlier in the process rides along in that snapshot too. A
restart can therefore still lose a pending device that was never involved in any approve() call,
which is the acceptable case: nothing durable depended on it yet.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import tempfile
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from tau_core import crypto_store

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Device:
    device_id: str
    name: str
    first_seen: str
    last_seen: str
    # Revocation (Phase 16 admin portal). Orthogonal to `status` below: a device can be blocked
    # regardless of whether it was ever approved - this is "kick this device off now", not part
    # of the approval workflow.
    blocked: bool = False
    # Device-approval workflow (Phase 27.A). "pending" until an admin calls approve(); "blocked"
    # is a valid value here too but nothing currently writes it - block() sets the `blocked` flag
    # above instead, which is what server.py's enforcement chokepoint checks today. Reconciling
    # the two into one axis is deliberately deferred (see project-tau-plan.md §8.28's 27.A
    # write-up on why admin identity / approval-decider identity / device identity stay separate
    # questions).
    status: str = "pending"
    # sha256 hex digest of the minted bearer token, never the raw token itself. None until
    # approve() mints one.
    token_hash: str | None = None

    def as_dict(self) -> dict:
        """Shape returned to HTTP clients (admin device list, register/block/unblock responses).
        `token_hash` never leaves the process - there is no reason for a hash of a bearer
        credential to appear in an API response, even though leaking it isn't the same risk as
        leaking the token itself."""
        data = asdict(self)
        del data["token_hash"]
        return data

    def to_store_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_store_dict(cls, data: dict) -> "Device":
        # .get() with defaults: a store written before 27.A has no status/token_hash keys at
        # all, and should read back as an unapproved device rather than fail to load - the same
        # additive-migration shape Phase 13.5 used for legacy memory rows.
        return cls(
            device_id=data["device_id"],
            name=data["name"],
            first_seen=data["first_seen"],
            last_seen=data["last_seen"],
            blocked=data.get("blocked", False),
            status=data.get("status", "pending"),
            token_hash=data.get("token_hash"),
        )


class DeviceRegistry:
    """device_id -> Device. `touch` records activity (creating a minimal entry for a never-seen
    id); `register` sets a human-readable name; `approve` grants a bearer token. Neither trusts
    the id to be anything but opaque."""

    def __init__(
        self,
        store_path: str | Path | None = None,
        encryption_key: bytes | None = None,
    ) -> None:
        self._devices: dict[str, Device] = {}
        # Monotonic activity order, so list() is deterministically most-recent-first even when
        # two touches land in the same clock tick (last_seen timestamps can tie at microsecond
        # resolution). Tracks the sequence of the last touch per device.
        self._order: dict[str, int] = {}
        self._seq = 0
        # Reentrant: register() calls touch() while already holding the lock in some call paths,
        # and a plain Lock would deadlock on that nesting.
        self._lock = threading.RLock()
        self._store_path = Path(store_path) if store_path else None
        self._key = encryption_key
        if self._store_path is not None:
            self._load()

    def _load(self) -> None:
        path = self._store_path
        if path is None or not path.exists():
            return
        try:
            raw = crypto_store.read_json(path, self._key) or {}
            devices = [Device.from_store_dict(item) for item in raw.get("devices", [])]
        except (OSError, ValueError, KeyError, TypeError):
            # A corrupt store must not stop Tau from booting, but it must be loud - mirrors
            # PendingActionQueue._load.
            logger.exception("Could not read device store at %s; starting with an empty registry", path)
            return
        self._devices = {device.device_id: device for device in devices}
        # Stored most-recent-first (see _save_locked); replay in reverse so touch-order sequence
        # numbers come out ascending, keeping list()'s ordering stable across a restart.
        for seq, device in enumerate(reversed(devices), start=1):
            self._order[device.device_id] = seq
        self._seq = len(devices)
        approved = sum(1 for d in self._devices.values() if d.status == "approved")
        if approved:
            logger.info("Restored %d approved device(s) from %s", approved, path)

    def _save_locked(self) -> None:
        """Persists the registry. Caller must hold `self._lock`. Atomic (temp file +
        os.replace): a half-written device store is worse than a stale one, since it decides
        which devices hold a live bearer token."""
        path = self._store_path
        if path is None:
            return
        ordered = sorted(
            self._devices.values(), key=lambda d: self._order.get(d.device_id, 0), reverse=True
        )
        payload = {"devices": [device.to_store_dict() for device in ordered]}
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                "wb", dir=path.parent, prefix=path.name, suffix=".tmp", delete=False
            ) as handle:
                handle.write(crypto_store.write_json_bytes(payload, self._key))
                temp_name = handle.name
            os.replace(temp_name, path)
        except OSError:
            logger.exception("Could not persist device store to %s", path)

    def touch(self, device_id: str, name: str | None = None) -> Device | None:
        """Record that `device_id` is active now. Returns the Device, or None for a falsy id
        (an anonymous client that sent no id - we don't fabricate identity for it). If the id is
        new, create an entry; a provided name updates it, otherwise a short fallback name is
        used so the admin device list is never full of blank rows. Not persisted - see module
        docstring."""
        device_id = (device_id or "").strip()
        if not device_id:
            return None
        with self._lock:
            now = _now_iso()
            existing = self._devices.get(device_id)
            if existing is None:
                self._devices[device_id] = Device(
                    device_id=device_id,
                    name=(name or "").strip() or f"device-{device_id[:8]}",
                    first_seen=now,
                    last_seen=now,
                )
            else:
                existing.last_seen = now
                if name and name.strip():
                    existing.name = name.strip()
            self._seq += 1
            self._order[device_id] = self._seq
            return self._devices[device_id]

    def register(self, device_id: str, name: str) -> Device:
        """Explicit name assignment (POST /api/devices/register). Same storage as touch() but
        always sets the name."""
        device_id = (device_id or "").strip()
        if not device_id:
            raise ValueError("device_id must not be empty")
        return self.touch(device_id, name=name or f"device-{device_id[:8]}")

    def get(self, device_id: str) -> Device | None:
        with self._lock:
            return self._devices.get((device_id or "").strip())

    def block(self, device_id: str) -> Device | None:
        """Revoke a device (Phase 16 admin portal). No-op (returns None) if the id has never
        been seen - nothing to block yet. Enforcement lives in server.py's _device_id(), the
        one chokepoint both /api/chat and /api/state already call."""
        with self._lock:
            device = self._devices.get((device_id or "").strip())
            if device is not None:
                device.blocked = True
            return device

    def unblock(self, device_id: str) -> Device | None:
        with self._lock:
            device = self._devices.get((device_id or "").strip())
            if device is not None:
                device.blocked = False
            return device

    def approve(self, device_id: str) -> str | None:
        """Approve a pending device, minting a long-lived bearer token. Returns the raw token -
        shown to the admin exactly once, since only its sha256 hash is stored - or None if the
        id has never been seen (nothing to approve). Persisted: see module docstring on why this
        is the one write path that must survive a restart."""
        device_id = (device_id or "").strip()
        with self._lock:
            device = self._devices.get(device_id)
            if device is None:
                return None
            token = secrets.token_urlsafe(32)
            device.status = "approved"
            device.token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
            self._save_locked()
            return token

    def verify_token(self, device_id: str, token: str) -> bool:
        """True iff `token` is the live token for an approved, non-blocked device. Constant-time
        compare (hmac.compare_digest) against the stored hash - this repo's first raw
        bearer-token check, where every other secret comparison (AEAD tag, Argon2id) gets this
        for free from the underlying primitive."""
        device = self.get(device_id)
        if device is None or device.blocked or device.status != "approved" or not device.token_hash:
            return False
        candidate = hashlib.sha256((token or "").encode("utf-8")).hexdigest()
        return hmac.compare_digest(device.token_hash, candidate)

    def list(self) -> list[Device]:
        """All known devices, most-recently-active first (by monotonic touch order, so ties in
        the last_seen timestamp don't make the order nondeterministic)."""
        with self._lock:
            return sorted(
                self._devices.values(), key=lambda d: self._order.get(d.device_id, 0), reverse=True
            )
