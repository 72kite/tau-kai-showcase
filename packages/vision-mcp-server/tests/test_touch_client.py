import time
from unittest.mock import patch

import pytest

from vision_mcp_server.touch_client import TouchClient


def test_get_active_touches_raises_when_disabled():
    """TOUCH_ENABLED=false (the default) is the same "loud but off" precedent as PTZClient -
    calling the tool without AirTouch running should fail clearly, not hang or return silence.
    """
    with patch.dict("os.environ", {"TOUCH_ENABLED": "false"}):
        client = TouchClient()
        with pytest.raises(RuntimeError, match="Touch tracking not enabled"):
            client.get_active_touches()


def test_handle_2dcur_set_updates_cursor_state():
    """Unit-level: feeds a synthetic TUIO 'set' message directly, no real socket involved -
    client stays disabled so __init__ never binds a port.
    """
    with patch.dict("os.environ", {"TOUCH_ENABLED": "false"}):
        client = TouchClient()

    client._handle_2dcur("/tuio/2Dcur", "set", 1, 0.5, 0.25, 0.1, -0.1, 0.0)

    assert client._cursors[1] == {
        "id": 1,
        "x": 0.5,
        "y": 0.25,
        "x_velocity": 0.1,
        "y_velocity": -0.1,
        "acceleration": 0.0,
    }


def test_handle_2dcur_fseq_and_source_are_ignored():
    """fseq/source carry no per-cursor state - must not raise or touch _cursors."""
    with patch.dict("os.environ", {"TOUCH_ENABLED": "false"}):
        client = TouchClient()

    client._handle_2dcur("/tuio/2Dcur", "fseq", 42)
    client._handle_2dcur("/tuio/2Dcur", "source", "AirTouch@localhost")

    assert client._cursors == {}


def test_handle_2dcur_alive_drops_lifted_cursors():
    """A cursor missing from the next 'alive' list has been lifted off the surface and must be
    dropped, even though no 'set' ever explicitly removes it.
    """
    with patch.dict("os.environ", {"TOUCH_ENABLED": "false"}):
        client = TouchClient()

    client._handle_2dcur("/tuio/2Dcur", "set", 1, 0.1, 0.1, 0.0, 0.0, 0.0)
    client._handle_2dcur("/tuio/2Dcur", "set", 2, 0.2, 0.2, 0.0, 0.0, 0.0)
    client._handle_2dcur("/tuio/2Dcur", "alive", 2)

    assert list(client._cursors.keys()) == [2]


def test_get_active_touches_over_real_udp_round_trip():
    """End-to-end: binds a real ephemeral UDP port, sends an actual TUIO 1.1 OSC bundle through
    python-osc's own client (not our parser feeding itself), and confirms get_active_touches()
    reflects it - proves the wire format matches AirTouch's, not just our own args-parsing.
    """
    from pythonosc.udp_client import SimpleUDPClient

    with patch.dict("os.environ", {"TOUCH_ENABLED": "true", "TUIO_HOST": "127.0.0.1", "TUIO_PORT": "0"}):
        client = TouchClient()
    try:
        actual_port = client._server.server_address[1]
        sender = SimpleUDPClient("127.0.0.1", actual_port)
        sender.send_message("/tuio/2Dcur", ["set", 7, 0.4, 0.6, 0.0, 0.0, 0.0])

        deadline = time.time() + 2
        touches = []
        while time.time() < deadline and not touches:
            touches = client.get_active_touches()
            time.sleep(0.02)

        assert len(touches) == 1
        assert touches[0]["id"] == 7
        assert touches[0]["x"] == pytest.approx(0.4)
        assert touches[0]["y"] == pytest.approx(0.6)
    finally:
        client.close()
