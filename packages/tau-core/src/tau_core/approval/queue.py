from __future__ import annotations

import logging
import os
import tempfile
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path

from tau_core import crypto_store
from tau_core.cdg.guard import ApprovedAction
from tau_core.hashing import hash_arguments
from tau_core.logging_setup import log_approval_decision

logger = logging.getLogger(__name__)


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"
    # Approved AND already spent on the call it authorised. Terminal, like DENIED/EXPIRED - see
    # PendingActionQueue.redeem for why an approval has to stop being usable the moment it is used.
    USED = "used"


class ApprovalError(Exception):
    """Raised for invalid transitions against an ActionRequest (unknown id, wrong state, expired)."""


@dataclass
class ActionRequest:
    id: str
    server: str
    tool: str
    arguments: dict
    arguments_hash: str
    reason: str
    requested_by: str
    requested_at: datetime
    expires_at: datetime | None = None
    status: ApprovalStatus = ApprovalStatus.PENDING
    decided_by: str | None = None
    decided_at: datetime | None = None
    decision_note: str | None = None
    used_at: datetime | None = None

    def is_expired(self, now: datetime | None = None) -> bool:
        now = now or datetime.now(timezone.utc)
        return self.expires_at is not None and now >= self.expires_at

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "server": self.server,
            "tool": self.tool,
            "arguments": self.arguments,
            "arguments_hash": self.arguments_hash,
            "reason": self.reason,
            "requested_by": self.requested_by,
            "requested_at": self.requested_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "status": self.status.value,
            "decided_by": self.decided_by,
            "decided_at": self.decided_at.isoformat() if self.decided_at else None,
            "decision_note": self.decision_note,
            "used_at": self.used_at.isoformat() if self.used_at else None,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ActionRequest":
        def _time(key: str) -> datetime | None:
            raw = data.get(key)
            return datetime.fromisoformat(raw) if raw else None

        return cls(
            id=data["id"],
            server=data["server"],
            tool=data["tool"],
            arguments=data["arguments"],
            # Recomputed rather than trusted from disk: the hash is what binds an approval to
            # the exact arguments it was granted for (see CoreDirectiveGuard._approval_satisfies).
            # Reading it back from a file would mean anyone who can edit that file can approve
            # one action and have a different one execute.
            arguments_hash=hash_arguments(data["arguments"]),
            reason=data["reason"],
            requested_by=data["requested_by"],
            requested_at=_time("requested_at") or datetime.now(timezone.utc),
            expires_at=_time("expires_at"),
            status=ApprovalStatus(data.get("status", ApprovalStatus.PENDING.value)),
            decided_by=data.get("decided_by"),
            decided_at=_time("decided_at"),
            decision_note=data.get("decision_note"),
            # A store written before single-use redemption existed has no `used_at`; those
            # approvals read back as un-redeemed, which is the only honest answer (nothing
            # recorded whether they were spent). They still expire on their own TTL.
            used_at=_time("used_at"),
        )


class PendingActionQueue:
    """Generic human-sign-off queue.

    Any MCP server (or the host itself, on the CDG's behalf) can submit a request describing a tool
    call it wants to make. A human approves or denies it out-of-band (CLI, UI, chat). Approval is
    single-use and bound to the exact server/tool/arguments it was granted for - see
    tau_core.cdg.guard.CoreDirectiveGuard._approval_satisfies.

    Durability (Phase 7 Tier 0 item 5): this was a bare in-memory dict in a container with no
    restart policy - the mechanism the entire architecture rests on. A restart silently dropped
    every pending human decision and stranded two-stage voice enrolment mid-flow. Pass
    `store_path` (production does, via TauCoreSettings.approval_store_path) to persist to a
    volume-backed JSON file, in the same shape security-mcp-server already uses for lockdown
    state. `store_path=None` keeps the old in-memory behaviour, which is what tests want.
    """

    def __init__(
        self,
        store_path: str | Path | None = None,
        encryption_key: bytes | None = None,
    ):
        self._lock = threading.Lock()
        self._requests: dict[str, ActionRequest] = {}
        self._store_path = Path(store_path) if store_path else None
        # None (the default) means the store is plaintext - unchanged pre-encryption behavior,
        # and what every existing test exercises since none of them set TAU_MASTER_KEY.
        self._key = encryption_key
        if self._store_path is not None:
            self._load()

    def _load(self) -> None:
        path = self._store_path
        if path is None or not path.exists():
            return
        try:
            raw = crypto_store.read_json(path, self._key) or {}
            requests = [ActionRequest.from_dict(item) for item in raw.get("requests", [])]
        except (OSError, ValueError, KeyError, TypeError):
            # A corrupt store must not stop Tau from booting, but it must be loud and it must
            # not be silently overwritten - approvals are consequential enough that a human
            # should see the file that could not be read.
            logger.exception("Could not read approval store at %s; starting with an empty queue", path)
            return
        self._requests = {request.id: request for request in requests}
        pending = sum(1 for r in self._requests.values() if r.status is ApprovalStatus.PENDING)
        if pending:
            logger.info("Restored %d pending approval request(s) from %s", pending, path)

    def _save_locked(self) -> None:
        """Persists the queue. Caller must hold `self._lock`.

        Written atomically (temp file + os.replace): a half-written approval store is worse than
        a stale one, since the file decides what a human has already authorised. Failures are
        logged, never raised - losing durability must not take down the approval path itself,
        which would turn a disk problem into "Tau can no longer be given permission to act".
        """
        path = self._store_path
        if path is None:
            return
        payload = {"requests": [request.to_dict() for request in self._requests.values()]}
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                "wb", dir=path.parent, prefix=path.name, suffix=".tmp", delete=False
            ) as handle:
                handle.write(crypto_store.write_json_bytes(payload, self._key))
                temp_name = handle.name
            os.replace(temp_name, path)
        except OSError:
            logger.exception("Could not persist approval store to %s", path)

    def submit(
        self,
        server: str,
        tool: str,
        arguments: dict,
        reason: str,
        requested_by: str,
        ttl: timedelta | None = timedelta(hours=1),
    ) -> ActionRequest:
        now = datetime.now(timezone.utc)
        request = ActionRequest(
            id=str(uuid.uuid4()),
            server=server,
            tool=tool,
            arguments=arguments,
            arguments_hash=hash_arguments(arguments),
            reason=reason,
            requested_by=requested_by,
            requested_at=now,
            expires_at=(now + ttl) if ttl else None,
        )
        with self._lock:
            self._requests[request.id] = request
            self._save_locked()
        return request

    def get(self, request_id: str) -> ActionRequest:
        with self._lock:
            request = self._requests.get(request_id)
        if request is None:
            raise ApprovalError(f"No such approval request: {request_id}")
        return request

    def list_pending(self) -> list[ActionRequest]:
        with self._lock:
            requests = list(self._requests.values())
        return [r for r in requests if r.status is ApprovalStatus.PENDING and not r.is_expired()]

    def approve(self, request_id: str, approved_by: str, note: str | None = None) -> ActionRequest:
        return self._decide(request_id, ApprovalStatus.APPROVED, approved_by, note)

    def deny(self, request_id: str, denied_by: str, note: str | None = None) -> ActionRequest:
        return self._decide(request_id, ApprovalStatus.DENIED, denied_by, note)

    def _decide(
        self, request_id: str, status: ApprovalStatus, decided_by: str, note: str | None
    ) -> ActionRequest:
        with self._lock:
            request = self._requests.get(request_id)
            if request is None:
                raise ApprovalError(f"No such approval request: {request_id}")
            if request.status is not ApprovalStatus.PENDING:
                raise ApprovalError(f"Request {request_id} already {request.status.value}")
            if request.is_expired():
                request.status = ApprovalStatus.EXPIRED
                self._save_locked()
                raise ApprovalError(f"Request {request_id} expired before it was decided")
            request.status = status
            request.decided_by = decided_by
            request.decided_at = datetime.now(timezone.utc)
            request.decision_note = note
            self._save_locked()

        # Audited outside the lock (the audit path does I/O) but before returning, so a decision
        # can never be acted on without having been recorded. This is the event the whole
        # architecture rests on - a human authorising a call the CDG refused to let Tau make
        # alone - and until Phase 7 it was written nowhere: the resulting tool call is logged as
        # `requested_by: tau-core`, naming the machine, never the person who signed off.
        log_approval_decision(
            request_id=request.id,
            server=request.server,
            tool=request.tool,
            status=status.value,
            decided_by=decided_by,
            reason=request.reason,
            note=note,
        )
        return request

    def to_approved_action(self, request_id: str) -> ApprovedAction:
        """Validate an approval and describe it, WITHOUT spending it.

        Used to check an approval before knowing whether it is actually needed - the CDG may
        decide this call required no approval at all, and burning one to find that out would
        make an approval disappear for nothing. `redeem` is what spends it.
        """
        request = self.get(request_id)
        if request.status is not ApprovalStatus.APPROVED:
            raise ApprovalError(f"Request {request_id} is not approved (status={request.status.value})")
        if request.is_expired():
            raise ApprovalError(f"Request {request_id} approval has expired")
        return ApprovedAction(
            request_id=request.id,
            server=request.server,
            tool=request.tool,
            arguments_hash=request.arguments_hash,
            approved_by=request.decided_by,
        )

    def redeem(self, request_id: str) -> ApprovedAction:
        """Spend an approval on the call it authorises. Atomic: the second caller loses.

        **This is what makes the single-use property in this class's docstring true.** Until it
        existed, `to_approved_action` was the only path and it never marked anything - so one
        human sign-off authorised the same call an unlimited number of times until its 1h TTL
        ran out. That is reachable from the unauthenticated `POST /api/tools/{server}/{tool}`
        passthrough, which takes an `approval_request_id` and whose pending ids and (mostly
        unredacted) arguments are both readable from the equally unauthenticated
        `GET /api/approvals`. One approved `exit_lockdown` meant unlimited `exit_lockdown`.

        Redemption happens *before* the tool executes, not after, so a transport failure spends
        the approval and a retry needs a fresh one. That is the fail-closed direction: the
        alternative leaves a live approval sitting behind any call that can be made to fail,
        which is a replay window an attacker gets to open on demand.
        """
        with self._lock:
            request = self._requests.get(request_id)
            if request is None:
                raise ApprovalError(f"No such approval request: {request_id}")
            if request.status is ApprovalStatus.USED:
                raise ApprovalError(
                    f"Request {request_id} was already used at "
                    f"{request.used_at.isoformat() if request.used_at else 'an unrecorded time'}; "
                    "approvals authorise exactly one call."
                )
            if request.status is not ApprovalStatus.APPROVED:
                raise ApprovalError(
                    f"Request {request_id} is not approved (status={request.status.value})"
                )
            if request.is_expired():
                request.status = ApprovalStatus.EXPIRED
                self._save_locked()
                raise ApprovalError(f"Request {request_id} approval has expired")

            request.status = ApprovalStatus.USED
            request.used_at = datetime.now(timezone.utc)
            approved_by = request.decided_by
            self._save_locked()
            action = ApprovedAction(
                request_id=request.id,
                server=request.server,
                tool=request.tool,
                arguments_hash=request.arguments_hash,
                approved_by=approved_by,
            )

        # Outside the lock (this does I/O), but before returning: an approval can never be spent
        # without the trail recording that it was spent, and by whom it had been granted.
        log_approval_decision(
            request_id=action.request_id,
            server=action.server,
            tool=action.tool,
            status=ApprovalStatus.USED.value,
            decided_by=approved_by or "unknown",
            reason="approval redeemed for execution",
        )
        return action
