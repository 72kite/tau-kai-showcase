import threading

import pytest
from pathlib import Path
from security_mcp_server import crypto_store
from security_mcp_server.security_store import SecurityStore


@pytest.fixture
def tmp_store(tmp_path):
    """Create a SecurityStore backed by a temporary file."""
    return SecurityStore(str(tmp_path / "security.json"))


def test_default_state_is_not_locked(tmp_store):
    """A fresh store starts with lockdown_active=False."""
    status = tmp_store.get_intrusion_status()
    assert status["lockdown_active"] is False


def test_enter_lockdown_sets_state(tmp_store):
    """enter_lockdown changes lockdown_active to True."""
    result = tmp_store.enter_lockdown("Intrusion detected")

    assert result["status"] == "lockdown_entered"
    assert result["reason"] == "Intrusion detected"
    assert result["entered_at"] is not None

    status = tmp_store.get_intrusion_status()
    assert status["lockdown_active"] is True
    assert status["lockdown_reason"] == "Intrusion detected"


def test_lockdown_state_persists(tmp_path):
    """Lockdown state persists across store instances."""
    db_path = str(tmp_path / "security.json")

    store1 = SecurityStore(db_path)
    store1.enter_lockdown("Intrusion")

    store2 = SecurityStore(db_path)
    status = store2.get_intrusion_status()
    assert status["lockdown_active"] is True


def test_exit_lockdown_clears_state(tmp_store):
    """exit_lockdown changes lockdown_active back to False."""
    tmp_store.enter_lockdown("Test intrusion")
    result = tmp_store.exit_lockdown()

    assert result["status"] == "lockdown_exited"

    status = tmp_store.get_intrusion_status()
    assert status["lockdown_active"] is False
    assert status["lockdown_reason"] is None


def test_log_incident_appends_to_log(tmp_store):
    """log_incident adds an entry to intrusion_log."""
    result = tmp_store.log_incident("camera_tampering", "Front door camera offline")

    assert result["logged"] is True
    assert result["incident"]["type"] == "camera_tampering"

    status = tmp_store.get_intrusion_status()
    assert status["intrusion_count"] == 1


def test_multiple_incidents_logged(tmp_store):
    """Multiple incidents accumulate in the log."""
    tmp_store.log_incident("camera_tampering", "Front door camera offline")
    tmp_store.log_incident("motion_detected", "Perimeter motion at 2am")

    status = tmp_store.get_intrusion_status()
    assert status["intrusion_count"] == 2


def test_incidents_logged_during_lockdown(tmp_store):
    """Incidents can be logged independently of lockdown state."""
    tmp_store.enter_lockdown("Intrusion")
    result = tmp_store.log_incident("malware_alert", "Suspicious process detected")

    assert result["logged"] is True
    status = tmp_store.get_intrusion_status()
    assert status["lockdown_active"] is True
    assert status["intrusion_count"] == 1


def test_is_lockdown_active(tmp_store):
    """is_lockdown_active reflects current state."""
    assert tmp_store.is_lockdown_active() is False

    tmp_store.enter_lockdown("Test")
    assert tmp_store.is_lockdown_active() is True

    tmp_store.exit_lockdown()
    assert tmp_store.is_lockdown_active() is False


def test_concurrent_log_incident_calls_do_not_lose_writes(tmp_store):
    """20 threads logging concurrently must all land - the lock is what prevents a lost update
    on a load-mutate-save race (enter_lockdown/log_incident/exit_lockdown are registered as sync
    FastMCP tools, so they run on FastMCP's thread pool, not serialized by an event loop)."""
    threads = [
        threading.Thread(target=tmp_store.log_incident, args=("test", f"incident {i}"))
        for i in range(20)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert tmp_store.get_intrusion_status()["intrusion_count"] == 20


# --- Encryption at rest ---------------------------------------------------------------------


def test_stays_plaintext_without_a_master_key(tmp_path, monkeypatch):
    monkeypatch.delenv("TAU_MASTER_KEY", raising=False)
    path = tmp_path / "security.json"
    store = SecurityStore(str(path))
    store.log_incident("camera_tampering", "Front door camera offline")

    raw = path.read_bytes()
    assert not raw.startswith(crypto_store._MAGIC)
    assert b"camera_tampering" in raw


def test_encrypts_when_master_key_set_and_round_trips(tmp_path, monkeypatch):
    monkeypatch.setenv("TAU_MASTER_KEY", "correct horse battery staple")
    path = tmp_path / "security.json"

    store1 = SecurityStore(str(path))
    store1.enter_lockdown("Intrusion detected")
    store1.log_incident("camera_tampering", "Front door camera offline")

    raw = path.read_bytes()
    assert raw.startswith(crypto_store._MAGIC)
    assert b"camera_tampering" not in raw
    assert b"Intrusion" not in raw

    store2 = SecurityStore(str(path))
    status = store2.get_intrusion_status()
    assert status["lockdown_active"] is True
    assert status["lockdown_reason"] == "Intrusion detected"
    assert status["intrusion_count"] == 1


def test_wrong_master_key_fails_loudly(tmp_path, monkeypatch):
    monkeypatch.setenv("TAU_MASTER_KEY", "right passphrase")
    path = tmp_path / "security.json"
    store = SecurityStore(str(path))
    store.log_incident("test", "test")

    monkeypatch.setenv("TAU_MASTER_KEY", "wrong passphrase")
    with pytest.raises(ValueError):
        SecurityStore(str(path))
