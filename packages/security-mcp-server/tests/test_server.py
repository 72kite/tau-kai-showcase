import json
import pytest
from unittest.mock import patch

from security_mcp_server.server import (
    enter_lockdown,
    exit_lockdown,
    get_intrusion_status,
    log_incident,
)


@pytest.fixture
def mock_store(monkeypatch, tmp_path):
    """Inject a SecurityStore into server functions."""
    from security_mcp_server.security_store import SecurityStore

    store = SecurityStore(str(tmp_path / "security.json"))
    monkeypatch.setattr("security_mcp_server.server._security_store", store)
    return store


def test_enter_lockdown_returns_json(mock_store):
    """enter_lockdown tool returns JSON."""
    result = enter_lockdown("Perimeter breach")
    data = json.loads(result)

    assert data["status"] == "lockdown_entered"
    assert data["reason"] == "Perimeter breach"


def test_exit_lockdown_succeeds_with_valid_token(mock_store, monkeypatch):
    """exit_lockdown accepts the correct token."""
    mock_store.enter_lockdown("Test")

    monkeypatch.setenv("LOCKDOWN_APPROVAL_TOKEN", "secret123")
    result = exit_lockdown("secret123")
    data = json.loads(result)

    assert data["status"] == "lockdown_exited"


def test_exit_lockdown_rejects_invalid_token(mock_store, monkeypatch):
    """exit_lockdown rejects incorrect tokens."""
    mock_store.enter_lockdown("Test")

    monkeypatch.setenv("LOCKDOWN_APPROVAL_TOKEN", "secret123")

    with pytest.raises(ValueError, match="Invalid approval token"):
        exit_lockdown("wrong_token")


def test_exit_lockdown_requires_token_configured(mock_store, monkeypatch):
    """exit_lockdown fails if LOCKDOWN_APPROVAL_TOKEN is not set."""
    mock_store.enter_lockdown("Test")

    monkeypatch.delenv("LOCKDOWN_APPROVAL_TOKEN", raising=False)

    with pytest.raises(RuntimeError, match="LOCKDOWN_APPROVAL_TOKEN not configured"):
        exit_lockdown("anything")


def test_get_intrusion_status_returns_json(mock_store):
    """get_intrusion_status tool returns JSON."""
    result = get_intrusion_status()
    data = json.loads(result)

    assert data["lockdown_active"] is False
    assert data["intrusion_count"] == 0


def test_log_incident_returns_json(mock_store):
    """log_incident tool returns JSON."""
    result = log_incident("motion_detected", "Perimeter motion at 2am")
    data = json.loads(result)

    assert data["logged"] is True
    assert data["incident"]["type"] == "motion_detected"
    assert data["incident"]["description"] == "Perimeter motion at 2am"
