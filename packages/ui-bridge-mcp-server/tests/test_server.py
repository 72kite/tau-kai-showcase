import json
import pytest
from unittest.mock import patch

from ui_bridge_mcp_server.server import (
    update_transcription,
    update_security_state,
    update_devices_state,
    update_vision_state,
    update_design_state,
    clear_design_state,
    get_ui_state,
    get_transcription_resource,
    get_security_resource,
    get_devices_resource,
    get_vision_resource,
    get_design_resource,
    get_approvals_resource,
    update_recognition_state,
    clear_recognition_state,
    _ui_state,
)


def test_update_transcription_returns_json():
    """update_transcription tool returns JSON."""
    result = update_transcription("Hello Tau", is_active=True)
    data = json.loads(result)

    assert data["status"] == "transcription_updated"
    assert data["text"] == "Hello Tau"


def test_update_security_state_returns_json():
    """update_security_state tool returns JSON."""
    result = update_security_state(lockdown_active=True, reason="Intrusion")
    data = json.loads(result)

    assert data["status"] == "security_updated"
    assert data["lockdown_active"] is True


def test_update_devices_state_returns_json():
    """update_devices_state tool returns JSON."""
    result = update_devices_state(device_count=10, online_count=8)
    data = json.loads(result)

    assert data["status"] == "devices_updated"
    assert data["total"] == 10
    assert data["online"] == 8


def test_update_vision_state_returns_json():
    """update_vision_state tool returns JSON."""
    result = update_vision_state(
        camera_active=True, snapshot_b64="iVBORw0KGgo=", description="Person detected"
    )
    data = json.loads(result)

    assert data["status"] == "vision_updated"
    assert data["camera_active"] is True


def test_update_design_state_returns_json():
    """update_design_state tool returns JSON and activates the design panel state."""
    result = update_design_state(
        title="Mounting bracket",
        description="L-bracket, 40x40x3mm, two M4 holes",
        svg="<svg viewBox='0 0 100 100'></svg>",
    )
    data = json.loads(result)

    assert data["status"] == "design_updated"
    assert data["title"] == "Mounting bracket"
    assert _ui_state.design.active is True
    assert _ui_state.design.svg.startswith("<svg")


def test_update_design_state_requires_svg_or_ascii():
    """Calling update_design_state with neither svg nor ascii_art is a caller error."""
    with pytest.raises(ValueError):
        update_design_state(title="Empty", description="nothing to draw")


def test_clear_design_state_deactivates():
    """clear_design_state hides the drawing panel without deleting the last drawing content."""
    update_design_state(title="Temp", description="d", ascii_art="+-+")
    assert _ui_state.design.active is True

    result = clear_design_state()
    data = json.loads(result)

    assert data["status"] == "design_cleared"
    assert _ui_state.design.active is False


def test_update_recognition_state_returns_json():
    """update_recognition_state tool returns JSON and activates the recognition card."""
    result = update_recognition_state(
        person_id="zion", role="owner", svg="<svg viewBox='0 0 100 100'></svg>"
    )
    data = json.loads(result)

    assert data["status"] == "recognition_updated"
    assert data["person_id"] == "zion"
    assert data["role"] == "owner"
    assert _ui_state.recognition.active is True
    assert _ui_state.recognition.svg.startswith("<svg")


def test_update_recognition_state_requires_svg_or_ascii():
    """Calling update_recognition_state with neither svg nor ascii_art is a caller error."""
    with pytest.raises(ValueError):
        update_recognition_state(person_id="zion", role="owner")


def test_clear_recognition_state_deactivates():
    """clear_recognition_state hides the card without deleting the last portrait content."""
    update_recognition_state(person_id="zion", role="owner", ascii_art="(o_o)")
    assert _ui_state.recognition.active is True

    result = clear_recognition_state()
    data = json.loads(result)

    assert data["status"] == "recognition_cleared"
    assert _ui_state.recognition.active is False


def test_update_transcription_tags_speaker_and_accumulates_history():
    """Successive calls with different speakers build a multi-turn history, not just the
    latest line - this is what the frontend's always-visible transcript zone renders."""
    update_transcription("design a bracket", is_active=True, speaker="user")
    update_transcription("Sketching it now.", is_active=True, speaker="tau")

    assert len(_ui_state.transcription.history) >= 2
    assert _ui_state.transcription.history[-2].speaker == "user"
    assert _ui_state.transcription.history[-1].speaker == "tau"


def test_per_device_transcript_isolation():
    """Phase 6.D: get_device_transcript returns ONLY the given device's turns; another device's
    turns never appear in it, and an unknown id yields nothing (never a leak)."""
    from ui_bridge_mcp_server.server import get_device_transcript

    update_transcription("secret from A", is_active=True, speaker="user", device_id="devA")
    update_transcription("secret from B", is_active=True, speaker="user", device_id="devB")

    a = json.loads(get_device_transcript("devA"))
    b = json.loads(get_device_transcript("devB"))
    a_texts = [e["text"] for e in a["history"]]
    b_texts = [e["text"] for e in b["history"]]
    assert "secret from A" in a_texts and "secret from B" not in a_texts
    assert "secret from B" in b_texts and "secret from A" not in b_texts

    unknown = json.loads(get_device_transcript("never-seen"))
    assert unknown["history"] == []


def test_unified_transcript_has_all_devices_but_public_state_has_none():
    """The unified log (admin view) carries every device's turns; the general ui://state
    resource carries NO history, so the generic passthrough can't leak conversations."""
    from ui_bridge_mcp_server.server import get_unified_transcript

    update_transcription("A line", is_active=True, speaker="user", device_id="devA")
    update_transcription("B line", is_active=True, speaker="user", device_id="devB")

    unified = json.loads(get_unified_transcript())
    unified_texts = [e["text"] for e in unified["history"]]
    assert "A line" in unified_texts and "B line" in unified_texts

    state = json.loads(get_ui_state())
    assert state["transcription"]["history"] == []


# Resource functions (ui://*): these must return a plain str, not a TextContent object -
# FastMCP's @server.resource() decorator wraps whatever the handler returns into its own
# TextContent envelope automatically. Returning a hand-built TextContent here would get
# wrapped a second time, double-nesting the payload - a bug that unit-testing only the tool
# functions (as this file did until now) can never catch, since it only manifests once
# FastMCP's real resource-serving machinery is in the loop. See tau-core's
# test_web_server.py for a test that exercises this over the real MCP transport.


def test_get_ui_state_returns_plain_json_string():
    result = get_ui_state()

    assert isinstance(result, str)
    data = json.loads(result)
    assert "transcription" in data and "security" in data and "design" in data


def test_get_transcription_resource_returns_plain_json_string():
    result = get_transcription_resource()

    assert isinstance(result, str)
    data = json.loads(result)
    assert "active" in data and "history" in data


def test_get_security_resource_returns_plain_json_string():
    result = get_security_resource()

    assert isinstance(result, str)
    data = json.loads(result)
    assert "lockdown_active" in data


def test_get_devices_resource_returns_plain_json_string():
    result = get_devices_resource()

    assert isinstance(result, str)
    data = json.loads(result)
    assert "device_count" in data


def test_get_vision_resource_returns_plain_json_string():
    result = get_vision_resource()

    assert isinstance(result, str)
    data = json.loads(result)
    assert "camera_active" in data


def test_get_design_resource_returns_plain_json_string():
    result = get_design_resource()

    assert isinstance(result, str)
    data = json.loads(result)
    assert "svg" in data and "ascii_art" in data


def test_get_approvals_resource_returns_plain_json_string(tmp_path, monkeypatch):
    monkeypatch.setenv("PROPOSAL_DB_PATH", str(tmp_path / "nonexistent.json"))

    result = get_approvals_resource()

    assert isinstance(result, str)
    data = json.loads(result)
    assert data == {"pending_count": 0, "proposals": []}
