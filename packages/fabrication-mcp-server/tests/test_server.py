import json
import pytest
from unittest.mock import patch, MagicMock

from fabrication_mcp_server.server import (
    get_printer_status,
    submit_print_job,
    pause_print,
    resume_print,
    cancel_print,
)


@pytest.fixture
def mock_octoprint(monkeypatch):
    """Inject a mock OctoPrintClient."""
    mock = MagicMock()
    mock.get_printer_status.return_value = {
        "state": "Idle",
        "temperature": {"tool0": {"actual": 25.0, "target": 0.0}},
    }
    mock.submit_print_job.return_value = {
        "success": True,
        "job_name": "test.gcode",
        "bed_temp": 60,
        "nozzle_temp": 200,
    }
    mock.pause_print.return_value = {"success": True, "action": "paused"}
    mock.resume_print.return_value = {"success": True, "action": "resumed"}
    mock.cancel_print.return_value = {"success": True, "action": "cancelled"}
    monkeypatch.setattr("fabrication_mcp_server.server._octoprint_client", mock)
    return mock


def test_get_printer_status_returns_json(mock_octoprint):
    """get_printer_status tool returns JSON."""
    result = get_printer_status()
    data = json.loads(result)

    assert "state" in data
    assert data["state"] == "Idle"


def test_submit_print_job_returns_json(mock_octoprint):
    """submit_print_job tool returns JSON."""
    result = submit_print_job("test.gcode", 60, 200)
    data = json.loads(result)

    assert data["success"] is True
    assert data["job_name"] == "test.gcode"


def test_pause_print_returns_json(mock_octoprint):
    """pause_print tool returns JSON."""
    result = pause_print()
    data = json.loads(result)

    assert data["success"] is True
    assert data["action"] == "paused"


def test_resume_print_returns_json(mock_octoprint):
    """resume_print tool returns JSON."""
    result = resume_print()
    data = json.loads(result)

    assert data["success"] is True
    assert data["action"] == "resumed"


def test_cancel_print_returns_json(mock_octoprint):
    """cancel_print tool returns JSON."""
    result = cancel_print()
    data = json.loads(result)

    assert data["success"] is True
    assert data["action"] == "cancelled"
