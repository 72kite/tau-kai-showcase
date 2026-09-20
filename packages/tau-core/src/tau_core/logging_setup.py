from __future__ import annotations

import json
import logging
import os
from collections import deque
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

AUDIT_LOGGER_NAME = "tau_core.audit"

# In-process ring of recent audit events, newest last. This is what the web bridge's
# /api/activity serves to the frontend's activity feed - a queryable window over the trail,
# not the trail itself (that's the rotating file below, plus Loki/Grafana in Phase 0).
MAX_RECENT_EVENTS = 500
_recent_events: deque[dict] = deque(maxlen=MAX_RECENT_EVENTS)

# Whether get_audit_logger() has installed ITS handlers yet.
#
# This is deliberately not `if not logger.handlers` (which is what it used to be). That guard
# asks "does this logger have any handlers at all", so anything else attaching one to
# `tau_core.audit` first - a deployment's logging.config.dictConfig, a log-shipping sidecar,
# pytest's caplog - made Tau silently skip installing its own, and the audit trail quietly
# vanished. A flag tracks the only thing this function actually needs to know: whether *it* has
# run. Found via pytest, but the deployment version of that bug is worse and silent.
_configured = False

# Tool arguments that carry biometric or bulk payloads. These were logged VERBATIM until Phase 7
# Tier 0 - every voice command wrote its whole base64 waveform into the audit trail, and every
# face/voice match wrote the raw embedding. That is the most sensitive data in the system
# (memory-mcp-server exists to hold exactly this, on its own VLAN, encrypted at rest per the
# plan) being copied into a log that item 6 simultaneously makes durable and rotated on disk.
# Making the trail durable without this redaction would have turned an audit feature into a
# biometric-PII leak with a longer retention period.
#
# Names verified against the real tool signatures, not guessed:
#   audio_base64  - voice-mcp-server: transcribe/enroll_voiceprint/identify_speaker/
#                   detect_wake_word
#   snapshot_b64  - vision-mcp-server: describe_scene/detect_faces
#   embedding     - memory-mcp-server: store_face/match_face/store_voice/match_voice
_SENSITIVE_ARGUMENT_KEYS = frozenset(
    {"audio_base64", "audio_b64", "snapshot_b64", "image_b64", "data_b64", "embedding", "frame"}
)

# Catch-all for payloads no denylist anticipated: a new server's `waveform_b64` should be
# truncated on day one, not on the day someone remembers to add it above. The audit trail wants
# to record *that* a call happened with *which shape* of arguments - it was never the place to
# archive the payload.
MAX_LOGGED_VALUE_CHARS = 256
MAX_LOGGED_SEQUENCE_ITEMS = 16

DEFAULT_AUDIT_LOG_MAX_BYTES = 10 * 1024 * 1024
DEFAULT_AUDIT_LOG_BACKUP_COUNT = 5


def _redact(value, key: str = ""):
    """Returns `value` with sensitive/bulk payloads replaced by a factual description of them.

    Deliberately describes what it removed (type and size) rather than dropping the key: an
    audit line reading `audio_base64: <redacted str, 214088 chars>` still tells a reader that a
    real utterance was passed and roughly how big it was, which is what an auditor needs. A
    silently missing key would make the trail lie by omission.
    """
    if key.lower() in _SENSITIVE_ARGUMENT_KEYS:
        if isinstance(value, (list, tuple)):
            return f"<redacted {type(value).__name__}, {len(value)} items>"
        if isinstance(value, str):
            return f"<redacted str, {len(value)} chars>"
        return f"<redacted {type(value).__name__}>"
    if isinstance(value, str) and len(value) > MAX_LOGGED_VALUE_CHARS:
        return f"{value[:MAX_LOGGED_VALUE_CHARS]}...<truncated, {len(value)} chars total>"
    if isinstance(value, dict):
        return {k: _redact(v, k) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        if len(value) > MAX_LOGGED_SEQUENCE_ITEMS:
            head = [_redact(item) for item in value[:MAX_LOGGED_SEQUENCE_ITEMS]]
            return head + [f"...<truncated, {len(value)} items total>"]
        return [_redact(item) for item in value]
    return value


def redact_arguments(arguments: dict) -> dict:
    """Public seam so callers/tests can check what the trail will actually record."""
    if not isinstance(arguments, dict):
        return arguments
    return {key: _redact(value, key) for key, value in arguments.items()}


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "message": record.getMessage(),
        }
        payload.update(getattr(record, "audit_fields", {}))
        # An audit line that raises while formatting is an audit line that never gets written,
        # so never let an exotic argument value take the record down.
        return json.dumps(payload, default=repr)


class _RingBufferHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "message": record.getMessage(),
        }
        event.update(getattr(record, "audit_fields", {}))
        _recent_events.append(event)


def recent_audit_events(limit: int = 100) -> list[dict]:
    """Most recent audit events, newest first."""
    events = list(_recent_events)
    return events[::-1][: max(limit, 0)]


def _audit_log_path() -> Path | None:
    """Where the durable audit trail is written, or None to stay stderr-only.

    Read from the environment rather than TauCoreSettings on purpose: this module is imported by
    the CDG/host path and must not depend on settings construction (or fail when a caller builds
    its own settings object). TAU_AUDIT_LOG_PATH="" disables the file handler explicitly.
    """
    raw = os.getenv("TAU_AUDIT_LOG_PATH", "")
    return Path(raw) if raw.strip() else None


def _build_file_handler(path: Path | None) -> logging.Handler | None:
    if path is None:
        return None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            path,
            maxBytes=int(os.getenv("TAU_AUDIT_LOG_MAX_BYTES", DEFAULT_AUDIT_LOG_MAX_BYTES)),
            backupCount=int(os.getenv("TAU_AUDIT_LOG_BACKUP_COUNT", DEFAULT_AUDIT_LOG_BACKUP_COUNT)),
            encoding="utf-8",
        )
    except (OSError, ValueError):
        # A read-only or missing volume must not stop Tau from running - it degrades to the
        # stderr trail, loudly. Raising here would mean a misconfigured log mount takes down the
        # whole host, which is a worse outcome than a less durable audit trail.
        logging.getLogger(__name__).warning(
            "Could not open audit log at %s; continuing with stderr-only audit", path, exc_info=True
        )
        return None
    handler.setFormatter(_JsonFormatter())
    return handler


def get_audit_logger() -> logging.Logger:
    """Every CDG decision, tool call, and approval decision is logged here as one JSON line -
    the audit trail the build plan requires for reviewable history (project-tau-plan.md §1).

    Phase 7 Tier 0 item 6: this was stderr-only, with no file handler and no rotation, while its
    own docstring claimed it fed Loki - so on the real deployment the "audit trail" lived
    entirely in a container's stdout and died with it. Set TAU_AUDIT_LOG_PATH (compose points it
    at a volume) to get a rotating durable copy; stderr stays, so Loki/Grafana remains the Phase
    0 answer rather than being replaced by this.
    """
    global _configured
    logger = logging.getLogger(AUDIT_LOGGER_NAME)
    if not _configured:
        handler = logging.StreamHandler()
        handler.setFormatter(_JsonFormatter())
        for installed in (handler, _RingBufferHandler(), _build_file_handler(_audit_log_path())):
            if installed is not None:
                installed._tau_audit_handler = True  # type: ignore[attr-defined]
                logger.addHandler(installed)
        logger.setLevel(logging.INFO)
        logger.propagate = False
        _configured = True
    return logger


def reset_audit_logger() -> None:
    """Drops the audit logger's handlers and its recent-events ring, so the next
    `get_audit_logger()` rebuilds both from the current environment.

    A test seam, and a deliberate one. This logger is process-global and installs its handlers
    exactly once, which means the first caller in a process fixes the configuration - including
    capturing whatever `sys.stderr` was at that moment - for everything after it. That is
    correct for a long-lived deployment and a trap in a test run: it makes audit assertions
    depend on which test happened to touch the logger first, and it would make
    TAU_AUDIT_LOG_PATH untestable (only the first test's path would ever be honoured).

    Removes only the handlers this module installed - anyone else's (pytest's caplog, a
    deployment's own) are left exactly as found, since ripping out a handler we did not add is
    not this function's business.
    """
    global _configured
    logger = logging.getLogger(AUDIT_LOGGER_NAME)
    for handler in list(logger.handlers):
        if not getattr(handler, "_tau_audit_handler", False):
            continue
        logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:  # noqa: BLE001 - a handler that won't close must not fail the reset
            pass
    _recent_events.clear()
    _configured = False


def log_tool_call(
    *,
    server: str,
    tool: str,
    arguments: dict,
    effect: str,
    outcome: str,
    reason: str,
) -> None:
    get_audit_logger().info(
        "tool_call",
        extra={
            "audit_fields": {
                "server": server,
                "tool": tool,
                "arguments": redact_arguments(arguments),
                "cdg_effect": effect,
                "outcome": outcome,
                "reason": reason,
            }
        },
    )


def log_approval_decision(
    *,
    request_id: str,
    server: str,
    tool: str,
    status: str,
    decided_by: str,
    reason: str = "",
    note: str | None = None,
) -> None:
    """Records WHO approved or denied WHAT, durably.

    Phase 7 Tier 0 item 5: the approval queue's `_decide` wrote no audit event at all, so the
    single most consequential human action in the system - authorising a call the CDG refused to
    make on its own - was recorded nowhere. The tool call that follows an approval is logged, but
    it names `tau-core` as the caller, not the person who signed off. That silently undercut the
    auditability pillar in §1: the approval queue is the whole point of the architecture, and it
    was the one thing not on the trail.
    """
    get_audit_logger().info(
        "approval_decision",
        extra={
            "audit_fields": {
                "request_id": request_id,
                "server": server,
                "tool": tool,
                "outcome": status,
                "decided_by": decided_by,
                "reason": reason,
                "note": note,
            }
        },
    )
