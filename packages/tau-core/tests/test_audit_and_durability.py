"""Phase 7 Tier 0 items 5-6: the approval queue survives a restart, decisions are audited, and
the trail no longer archives biometrics.

Why these matter more than they look: the approval queue is the mechanism the whole architecture
rests on (a human authorising a call the CDG refused to let Tau make alone), and it was an
in-memory dict in a container with no restart policy that also never recorded its own decisions.
Meanwhile the audit trail - the thing that makes any of this reviewable - was stderr-only and
logged raw voiceprints and utterance audio verbatim.
"""

import json
import logging
from datetime import timedelta

import pytest

from tau_core.approval import ApprovalStatus, PendingActionQueue
from tau_core.approval.queue import ApprovalError
from tau_core.config import TauCoreSettings
from tau_core.logging_setup import (
    AUDIT_LOGGER_NAME,
    get_audit_logger,
    recent_audit_events,
    redact_arguments,
    reset_audit_logger,
)


# --- Item 5: durability -----------------------------------------------------------------------


def test_pending_approvals_survive_a_restart(tmp_path):
    """The live failure this prevents: tau-core restarts (a redeploy, a crash, an OOM kill) and
    every pending human decision silently disappears - including a two-stage voice enrolment
    stranded between its two approval gates."""
    store = tmp_path / "approvals.json"

    queue = PendingActionQueue(store_path=store)
    request = queue.submit(
        server="security-mcp-server",
        tool="exit_lockdown",
        arguments={"token": "abc"},
        reason="needs a human",
        requested_by="tau-core",
    )

    # A brand-new queue over the same file == the process restarted.
    restarted = PendingActionQueue(store_path=store)
    restored = restarted.get(request.id)

    assert restored.id == request.id
    assert restored.status is ApprovalStatus.PENDING
    assert restored.tool == "exit_lockdown"
    assert [r.id for r in restarted.list_pending()] == [request.id]


def test_a_decision_made_before_a_restart_is_still_binding(tmp_path):
    """An approval is single-use and bound to exact arguments. If a restart forgot that a
    request was already decided, an approved-then-consumed action could be replayed."""
    store = tmp_path / "approvals.json"
    queue = PendingActionQueue(store_path=store)
    request = queue.submit(
        server="proxmox-mcp-server", tool="apply_update", arguments={"vmid": 100},
        reason="r", requested_by="tau-core",
    )
    queue.approve(request.id, approved_by="zion")

    restarted = PendingActionQueue(store_path=store)
    assert restarted.get(request.id).status is ApprovalStatus.APPROVED
    assert restarted.get(request.id).decided_by == "zion"
    # Still not re-decidable after the restart.
    with pytest.raises(ApprovalError, match="already approved"):
        restarted.deny(request.id, denied_by="someone-else")


def test_restored_approval_still_binds_to_its_exact_arguments(tmp_path):
    """The arguments_hash is what stops an approval for one action authorising another
    (CoreDirectiveGuard._approval_satisfies). It is recomputed on load rather than trusted from
    the file, so editing the store cannot decouple an approval from what it approved."""
    store = tmp_path / "approvals.json"
    queue = PendingActionQueue(store_path=store)
    request = queue.submit(
        server="robotics-mcp-server", tool="patrol_route", arguments={"route": "perimeter"},
        reason="r", requested_by="tau-core",
    )
    queue.approve(request.id, approved_by="zion")

    # Tamper: keep the approval, swap the arguments AND the stored hash to match.
    raw = json.loads(store.read_text())
    raw["requests"][0]["arguments"] = {"route": "front-door"}
    raw["requests"][0]["arguments_hash"] = "hash-of-the-swapped-args"
    store.write_text(json.dumps(raw))

    restarted = PendingActionQueue(store_path=store)
    approved = restarted.to_approved_action(request.id)
    # The hash follows the arguments actually present, not the attacker-supplied field, so the
    # CDG will compare it against the real call and refuse a mismatch.
    assert approved.arguments_hash != "hash-of-the-swapped-args"


def test_expired_requests_do_not_come_back_pending_after_a_restart(tmp_path):
    store = tmp_path / "approvals.json"
    queue = PendingActionQueue(store_path=store)
    request = queue.submit(
        server="echo", tool="echo", arguments={}, reason="r", requested_by="tau-core",
        ttl=timedelta(seconds=-1),
    )

    restarted = PendingActionQueue(store_path=store)
    assert restarted.list_pending() == []
    assert restarted.get(request.id).is_expired()


def test_a_corrupt_store_does_not_stop_tau_booting(tmp_path, caplog):
    store = tmp_path / "approvals.json"
    store.write_text("{ this is not json")

    with caplog.at_level(logging.ERROR):
        queue = PendingActionQueue(store_path=store)

    assert queue.list_pending() == []
    assert "approval store" in caplog.text


def test_in_memory_queue_is_still_supported(tmp_path):
    """store_path=None keeps the pre-Phase-7 behaviour, which is what unit tests want."""
    queue = PendingActionQueue()
    request = queue.submit(server="echo", tool="echo", arguments={}, reason="r", requested_by="t")
    assert queue.get(request.id).id == request.id
    assert not list(tmp_path.iterdir())


def test_blank_store_path_setting_means_in_memory_not_the_cwd(monkeypatch):
    """TAU_APPROVAL_STORE_PATH="" must disable persistence, not coerce to Path('.') and try to
    write the store over a directory."""
    monkeypatch.setenv("TAU_APPROVAL_STORE_PATH", "")
    assert TauCoreSettings().approval_store_path is None


def test_approval_store_is_durable_by_default():
    """A fresh install must not silently drop pending approvals because nobody set an env var -
    the same 'unsafe unless configured' pattern the Hermes audit criticised elsewhere."""
    settings = TauCoreSettings(_env_file=None)
    assert settings.approval_store_path is not None
    assert settings.approval_store_path.name == "approvals.json"


# --- Item 5: decisions are audited ------------------------------------------------------------


def test_approval_decision_is_audited_with_who_decided_it():
    """Who approved what was recorded NOWHERE before this. The tool call that follows an
    approval is logged as `requested_by: tau-core` - naming the machine, never the human who
    signed off - so the single most consequential action in the system left no trace."""
    get_audit_logger()
    queue = PendingActionQueue()
    request = queue.submit(
        server="security-mcp-server", tool="exit_lockdown", arguments={},
        reason="intruder cleared", requested_by="tau-core",
    )

    queue.approve(request.id, approved_by="voice:zion", note="checked the cameras")

    event = next(e for e in recent_audit_events() if e.get("message") == "approval_decision")
    assert event["request_id"] == request.id
    assert event["tool"] == "exit_lockdown"
    assert event["outcome"] == "approved"
    assert event["decided_by"] == "voice:zion"
    assert event["note"] == "checked the cameras"


def test_denials_are_audited_too():
    get_audit_logger()
    queue = PendingActionQueue()
    request = queue.submit(
        server="robotics-mcp-server", tool="patrol_route", arguments={"route": "perimeter"},
        reason="r", requested_by="tau-core",
    )

    queue.deny(request.id, denied_by="voice:zion")

    event = next(e for e in recent_audit_events() if e.get("message") == "approval_decision")
    assert event["outcome"] == "denied"
    assert event["decided_by"] == "voice:zion"


# --- Item 6: redaction ------------------------------------------------------------------------


def test_voice_audio_is_not_written_to_the_audit_trail():
    """Every voice command wrote its entire base64 waveform into the audit trail. Item 6 makes
    that trail durable and rotated on disk - so without redaction, this change would have
    upgraded a leak into a leak with retention."""
    audio = "A" * 200_000
    redacted = redact_arguments({"audio_base64": audio, "filename": "utterance.webm"})

    assert audio not in json.dumps(redacted)
    # Still describes what was there - an auditor needs to know a real utterance was passed.
    assert "200000" in redacted["audio_base64"]
    assert redacted["filename"] == "utterance.webm"


def test_voiceprint_and_face_embeddings_are_not_written_to_the_audit_trail():
    """Embeddings ARE the biometric. memory-mcp-server exists to hold exactly this - on its own
    VLAN, encrypted at rest per the plan - so copying it into a log defeats that entirely."""
    redacted = redact_arguments({"person_id": "zion", "embedding": [0.1234] * 384})

    assert "0.1234" not in json.dumps(redacted)
    assert "384" in redacted["embedding"]
    assert redacted["person_id"] == "zion"


def test_camera_snapshots_are_not_written_to_the_audit_trail():
    redacted = redact_arguments({"snapshot_b64": "B" * 50_000})
    assert "BBBB" not in json.dumps(redacted)


def test_an_unanticipated_bulk_payload_is_truncated_by_size():
    """A denylist only covers the payloads someone remembered. A new server's `waveform_b64`
    should be truncated on day one, not on the day it gets added to the list."""
    redacted = redact_arguments({"some_future_blob": "C" * 100_000})
    assert len(redacted["some_future_blob"]) < 400
    assert "100000 chars total" in redacted["some_future_blob"]


def test_ordinary_arguments_are_logged_untouched():
    """Redaction must not gut the trail: the arguments are the point of auditing a tool call."""
    arguments = {"entity_id": "light.kitchen", "brightness": 40, "on": True, "tags": ["a", "b"]}
    assert redact_arguments(arguments) == arguments


async def test_approval_queue_endpoint_does_not_serve_raw_biometrics():
    """Same leak as the audit trail, one surface over - and this one is unauthenticated and
    served to every device on the LAN. A voice-enrolment approval carries a raw voiceprint;
    nobody approving from a tablet can review 384 floats anyway."""
    import sys
    from pathlib import Path

    import httpx
    from httpx import ASGITransport

    from tau_core.host import TauCoreHost
    from tau_core.mcp_client import MCPClientManager, ServerConfig, ServerRegistry
    from tau_core.web.server import create_app

    examples = Path(__file__).resolve().parent.parent / "examples"
    config = Path(__file__).resolve().parent.parent / "config"
    registry = ServerRegistry(
        servers=[
            ServerConfig(
                name="echo", transport="stdio", command=sys.executable,
                args=[str(examples / "echo_mcp_server.py")],
            )
        ]
    )
    settings = TauCoreSettings(cdg_rules_path=config / "cdg_rules.yaml")
    async with MCPClientManager(registry) as manager:
        host = TauCoreHost(manager, settings=settings)
        host.approvals.submit(
            server="memory-mcp-server", tool="store_voice",
            arguments={"person_id": "zion", "embedding": [0.1234] * 384},
            reason="biometric enrolment", requested_by="tablet-ui",
        )
        app = create_app(settings=settings, host=host)

        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/approvals")

    assert resp.status_code == 200
    assert "0.1234" not in resp.text
    assert "384" in resp.json()[0]["arguments"]["embedding"]
    # The human-meaningful part of the decision still survives redaction.
    assert resp.json()[0]["arguments"]["person_id"] == "zion"
    assert resp.json()[0]["reason"] == "biometric enrolment"


def test_nested_payloads_are_redacted_too():
    redacted = redact_arguments({"outer": {"audio_base64": "D" * 9_000}})
    assert "DDDD" not in json.dumps(redacted)


def test_tool_call_audit_events_are_redacted_end_to_end():
    """redact_arguments being correct is worth nothing if log_tool_call doesn't call it."""
    from tau_core.logging_setup import log_tool_call

    get_audit_logger()
    log_tool_call(
        server="voice-mcp-server", tool="transcribe", arguments={"audio_base64": "E" * 80_000},
        effect="allow", outcome="executed", reason="r",
    )

    event = next(e for e in recent_audit_events() if e.get("tool") == "transcribe")
    assert "EEEE" not in json.dumps(event)


# --- Item 6: durability -----------------------------------------------------------------------


def test_audit_trail_is_written_to_a_rotating_file_when_configured(tmp_path, monkeypatch):
    """The trail was stderr-only while its own docstring claimed it fed Loki - so on the real
    deployment the audit trail lived in a container's stdout and died with it."""
    from tau_core.logging_setup import log_tool_call

    log_path = tmp_path / "audit" / "audit.log"
    monkeypatch.setenv("TAU_AUDIT_LOG_PATH", str(log_path))
    reset_audit_logger()

    log_tool_call(
        server="home-assistant-mcp-server", tool="call_service",
        arguments={"entity_id": "light.kitchen"}, effect="allow", outcome="executed", reason="r",
    )
    for handler in logging.getLogger(AUDIT_LOGGER_NAME).handlers:
        handler.flush()

    assert log_path.exists()
    line = json.loads(log_path.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert line["tool"] == "call_service"
    assert line["outcome"] == "executed"
    assert line["arguments"] == {"entity_id": "light.kitchen"}


def test_an_unwritable_audit_path_degrades_instead_of_killing_the_host(tmp_path, monkeypatch):
    """A misconfigured log mount must not take down the whole home AI - a less durable audit
    trail is a much better outcome than no Tau."""
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("I am a file, not a directory")
    monkeypatch.setenv("TAU_AUDIT_LOG_PATH", str(blocker / "audit.log"))
    reset_audit_logger()

    logger = get_audit_logger()  # must not raise

    from tau_core.logging_setup import log_tool_call

    log_tool_call(server="echo", tool="echo", arguments={}, effect="allow", outcome="executed", reason="r")
    assert any(e.get("tool") == "echo" for e in recent_audit_events())
    assert logger.handlers  # stderr + ring still installed


def test_a_third_party_handler_does_not_silently_disable_the_audit_trail():
    """The guard used to be `if not logger.handlers`, so ANYTHING attaching a handler to
    tau_core.audit first - a deployment's dictConfig, a log shipper, pytest's caplog - made Tau
    skip installing its own and the trail vanished silently. Found via pytest; the deployment
    version of this bug is worse, because nothing fails."""
    reset_audit_logger()
    intruder = logging.NullHandler()
    logging.getLogger(AUDIT_LOGGER_NAME).addHandler(intruder)
    try:
        from tau_core.logging_setup import log_tool_call

        get_audit_logger()
        log_tool_call(server="echo", tool="echo", arguments={}, effect="allow", outcome="executed", reason="r")

        # Our ring-buffer handler was installed despite the pre-existing handler.
        assert any(e.get("tool") == "echo" for e in recent_audit_events())
    finally:
        logging.getLogger(AUDIT_LOGGER_NAME).removeHandler(intruder)


def test_reset_leaves_third_party_handlers_alone():
    intruder = logging.NullHandler()
    logging.getLogger(AUDIT_LOGGER_NAME).addHandler(intruder)
    try:
        get_audit_logger()
        reset_audit_logger()
        assert intruder in logging.getLogger(AUDIT_LOGGER_NAME).handlers
    finally:
        logging.getLogger(AUDIT_LOGGER_NAME).removeHandler(intruder)
