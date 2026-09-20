import sys

import pytest
from unittest.mock import MagicMock, patch

from vision_mcp_server.camera_client import CameraClient, get_camera_client


class TestGetCameraClientFactory:
    """get_camera_client() is the one switch point server.py goes through - CAMERA_SOURCE=kinect2
    selects the Kinect backend, anything else keeps the existing CameraClient(int index) behavior
    unchanged."""

    def test_default_source_returns_camera_client(self):
        with patch.dict("os.environ", {"CAMERA_SOURCE": "0"}):
            assert isinstance(get_camera_client(), CameraClient)

    def test_unset_source_returns_camera_client(self):
        with patch.dict("os.environ", {}, clear=False):
            import os

            os.environ.pop("CAMERA_SOURCE", None)
            assert isinstance(get_camera_client(), CameraClient)

    def test_kinect2_source_returns_kinect2_client(self):
        from vision_mcp_server.kinect2_client import Kinect2Client

        with patch.dict("os.environ", {"CAMERA_SOURCE": "kinect2"}):
            assert isinstance(get_camera_client(), Kinect2Client)

    def test_kinect2_source_is_case_insensitive(self):
        from vision_mcp_server.kinect2_client import Kinect2Client

        with patch.dict("os.environ", {"CAMERA_SOURCE": "Kinect2"}):
            assert isinstance(get_camera_client(), Kinect2Client)


@pytest.fixture
def mock_kinect_libs():
    """Kinect2Client lazy-imports pylibfreenect2/cv2/numpy inside its methods (same reasoning as
    CameraClient's cv2 - importable/testable without the real heavy/hardware-bound deps
    installed). Patching sys.modules is what actually reaches a function-local import."""
    pylibfreenect2 = MagicMock()
    # FrameType.Color used as both an enum value passed to SyncMultiFrameListener AND as a dict
    # key into the returned frame map - a plain sentinel object works as both.
    pylibfreenect2.FrameType.Color = object()
    cv2 = MagicMock()
    numpy = MagicMock()
    with patch.dict(
        sys.modules,
        {"pylibfreenect2": pylibfreenect2, "cv2": cv2, "numpy": numpy},
    ):
        yield {"pylibfreenect2": pylibfreenect2, "cv2": cv2, "numpy": numpy}


def _make_client(mock_kinect_libs, *, num_devices=1):
    from vision_mcp_server.kinect2_client import Kinect2Client

    fn = MagicMock()
    fn.enumerateDevices.return_value = num_devices
    fn.getDeviceSerialNumber.return_value = "SERIAL123"
    mock_kinect_libs["pylibfreenect2"].Freenect2.return_value = fn
    device = MagicMock()
    device.start.return_value = True
    fn.openDevice.return_value = device
    listener = MagicMock()
    mock_kinect_libs["pylibfreenect2"].SyncMultiFrameListener.return_value = listener
    return Kinect2Client(), fn, device, listener


def test_no_device_found_raises(mock_kinect_libs):
    client, *_ = _make_client(mock_kinect_libs, num_devices=0)
    with pytest.raises(RuntimeError, match="No Kinect v2 found"):
        client.get_frame()


def test_device_fails_to_start_raises(mock_kinect_libs):
    client, fn, device, listener = _make_client(mock_kinect_libs)
    device.start.return_value = False
    with pytest.raises(RuntimeError, match="failed to start streaming"):
        client.get_frame()


def test_stalled_stream_returns_failure_not_hang(mock_kinect_libs):
    """waitForNewFrame timing out (returns None) must surface as (False, b"") like
    CameraClient's read-failure path, not raise or hang the MCP tool call."""
    client, fn, device, listener = _make_client(mock_kinect_libs)
    listener.waitForNewFrame.return_value = None

    success, frame = client.get_frame()

    assert success is False
    assert frame == b""


def test_get_frame_converts_bgrx_to_jpeg(mock_kinect_libs):
    client, fn, device, listener = _make_client(mock_kinect_libs)
    color_frame = MagicMock()
    color_frame.asarray.return_value = b"\x00" * (1920 * 1080 * 4)
    frames = {mock_kinect_libs["pylibfreenect2"].FrameType.Color: color_frame}
    listener.waitForNewFrame.return_value = frames

    cv2 = mock_kinect_libs["cv2"]
    numpy = mock_kinect_libs["numpy"]
    reshaped = MagicMock()
    sliced = MagicMock()
    reshaped.__getitem__.return_value = sliced
    numpy.frombuffer.return_value.reshape.return_value = reshaped
    resized = MagicMock()
    cv2.resize.return_value = resized
    encoded = MagicMock()
    encoded.tobytes.return_value = b"jpeg-bytes"
    cv2.imencode.return_value = (True, encoded)

    success, frame_bytes = client.get_frame()

    assert success is True
    assert frame_bytes == b"jpeg-bytes"
    # Must release the frame back to libfreenect2 every time, success or failure - a leaked
    # frame reference is exactly the kind of bug that only shows up after hours of streaming.
    listener.release.assert_called_once_with(frames)


def test_get_frame_releases_even_on_encode_failure(mock_kinect_libs):
    client, fn, device, listener = _make_client(mock_kinect_libs)
    color_frame = MagicMock()
    color_frame.asarray.return_value = b"\x00" * (1920 * 1080 * 4)
    frames = {mock_kinect_libs["pylibfreenect2"].FrameType.Color: color_frame}
    listener.waitForNewFrame.return_value = frames
    mock_kinect_libs["cv2"].imencode.return_value = (False, None)

    success, frame_bytes = client.get_frame()

    assert success is False
    listener.release.assert_called_once_with(frames)


def test_get_snapshot_b64_round_trips(mock_kinect_libs):
    import base64

    client, fn, device, listener = _make_client(mock_kinect_libs)
    color_frame = MagicMock()
    color_frame.asarray.return_value = b"\x00" * (1920 * 1080 * 4)
    frames = {mock_kinect_libs["pylibfreenect2"].FrameType.Color: color_frame}
    listener.waitForNewFrame.return_value = frames
    encoded = MagicMock()
    encoded.tobytes.return_value = b"jpeg-bytes"
    mock_kinect_libs["cv2"].imencode.return_value = (True, encoded)

    snapshot_b64 = client.get_snapshot_b64()

    assert base64.b64decode(snapshot_b64) == b"jpeg-bytes"


def test_second_get_frame_reuses_the_open_device(mock_kinect_libs):
    """The device must open ONCE and stay open across calls - re-opening a Kinect per frame
    would be extremely slow (real USB3 device init) and is not what _ensure_open's lazy-singleton
    pattern (mirroring CameraClient's) is for."""
    client, fn, device, listener = _make_client(mock_kinect_libs)
    listener.waitForNewFrame.return_value = None

    client.get_frame()
    client.get_frame()

    fn.openDevice.assert_called_once()
    device.start.assert_called_once()


def test_close_stops_and_closes_the_device(mock_kinect_libs):
    client, fn, device, listener = _make_client(mock_kinect_libs)
    listener.waitForNewFrame.return_value = None
    client.get_frame()  # opens the device

    client.close()

    device.stop.assert_called_once()
    device.close.assert_called_once()


def test_close_before_open_is_a_no_op(mock_kinect_libs):
    from vision_mcp_server.kinect2_client import Kinect2Client

    Kinect2Client().close()  # must not raise
