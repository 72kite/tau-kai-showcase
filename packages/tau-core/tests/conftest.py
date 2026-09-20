"""Test-wide isolation for the state Phase 7 made durable and process-global.

The approval queue and the audit log became volume-backed and durable-by-default (Tier 0 items
5-6). That default is right for a deployment and wrong for a test run: without this, every test
constructing a `TauCoreHost` would read and write the same real `tau-core/data/approvals.json`,
so tests would leak pending approvals into each other and into the developer's working tree.

Isolating here rather than weakening the default keeps production durable-by-default - the
alternative (defaulting to in-memory and asking deployments to opt in) is the same "unsafe
unless someone remembered to configure it" pattern the Hermes audit criticised.
"""

import pytest

from tau_core.logging_setup import reset_audit_logger


@pytest.fixture(autouse=True)
def isolate_durable_state(tmp_path, monkeypatch):
    monkeypatch.setenv("TAU_APPROVAL_STORE_PATH", str(tmp_path / "approvals.json"))
    # Phase 27.A made the device registry durable-by-default (./data/devices.json) the same way
    # approvals is - same leak-across-test-runs risk, same fix.
    monkeypatch.setenv("TAU_DEVICE_STORE_PATH", str(tmp_path / "devices.json"))
    # Phase 10.3 #11 made both session stores durable-by-default (./data/sessions.json,
    # ./data/device_sessions.json) - same leak-across-test-runs risk, same fix. Isolated to
    # tmp_path rather than emptied, so tests that specifically exercise persistence (a restart
    # simulated by constructing a second TauCoreHost/TauAssistant against the same settings)
    # still have a real file to round-trip through, matching how approvals/devices are isolated
    # above rather than how the transcript store (never round-trip-tested this way) is emptied.
    monkeypatch.setenv("TAU_SESSION_STORE_PATH", str(tmp_path / "sessions.json"))
    monkeypatch.setenv("TAU_DEVICE_SESSION_STORE_PATH", str(tmp_path / "device_sessions.json"))
    # Phase 14 made the ui-bridge transcript durable-by-default (./data/transcript.json). Tests
    # that spawn ui-bridge-mcp-server over stdio inherit this env, so without forcing it empty the
    # subprocess persists to a relative ./data/transcript.json and *accumulates across runs* -
    # test_web_devices' per-device history then sees stale entries from earlier runs and fails.
    # Empty = pure in-memory, matching how approvals/audit are isolated above.
    monkeypatch.setenv("TAU_TRANSCRIPT_STORE_PATH", "")
    # Stderr-only unless a test opts in by setting TAU_AUDIT_LOG_PATH itself.
    monkeypatch.delenv("TAU_AUDIT_LOG_PATH", raising=False)

    # The audit logger installs its handlers once per process, capturing sys.stderr as it goes.
    # Resetting per test stops the first test that happens to touch it from deciding where every
    # later test's audit output lands - which is exactly what broke test_host_integration once
    # the approval queue started auditing its decisions (it runs earlier alphabetically).
    reset_audit_logger()
    yield
    reset_audit_logger()
