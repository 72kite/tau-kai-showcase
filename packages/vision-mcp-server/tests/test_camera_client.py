import sys

import pytest
from unittest.mock import Mock, patch, MagicMock
from vision_mcp_server.camera_client import CameraClient


@pytest.fixture
def mock_cv2():
    """Mock the cv2 module for CameraClient's LAZY imports.

    These four tests errored on every run with "camera_client does not have the attribute 'cv2'",
    because they patched `vision_mcp_server.camera_client.cv2` - an attribute that does not
    exist. `camera_client` imports cv2 *inside* its methods, deliberately, so the package can be
    imported and tested without opencv installed. Patching a module attribute cannot intercept a
    function-local import (and `create=True` wouldn't help: the local `import cv2` would still
    rebind to the real module).

    Patching sys.modules is what actually works with a lazy import: the method's `import cv2`
    resolves straight to this mock.

    They had been broken long enough for nobody to notice, because nothing ran them - the only
    workflow was workflow_dispatch-only. Found while building the CI that now does (Phase 9).
    """
    mock = MagicMock()
    with patch.dict(sys.modules, {"cv2": mock}):
        yield mock


def _encoded_buffer(data: bytes) -> MagicMock:
    """Stands in for what cv2.imencode actually returns: (retval, ndarray).

    The buffer is a numpy array and the code calls `.tobytes()` on it. These tests returned a
    plain list, so once they started running (see mock_cv2) they failed on
    "'list' object has no attribute 'tobytes'" - a second bug in the same four tests, hidden
    behind the first.
    """
    buffer = MagicMock()
    buffer.tobytes.return_value = data
    return buffer


def test_camera_client_opens_camera(mock_cv2):
    """CameraClient opens camera on first frame request."""
    mock_cap = MagicMock()
    mock_cap.isOpened.return_value = True
    mock_cap.read.return_value = (True, b"frame_data")
    mock_cv2.VideoCapture.return_value = mock_cap
    mock_cv2.imencode.return_value = (True, _encoded_buffer(b"encoded"))

    with patch.dict("os.environ", {"CAMERA_SOURCE": "0"}):
        client = CameraClient()
        success, frame = client.get_frame()

    assert success is True
    # Windows opens with CAP_DSHOW, not the default (Media Foundation) backend - found by
    # live-testing (Phase 10.5): MSMF deadlocks opening the camera from the worker thread
    # anyio.to_thread.run_sync uses to keep server.py's async tools off the event loop.
    # Everywhere else (this suite also runs in CI on ubuntu-latest) keeps the plain call.
    if sys.platform.startswith("win"):
        mock_cv2.VideoCapture.assert_called_once_with(0, mock_cv2.CAP_DSHOW)
    else:
        mock_cv2.VideoCapture.assert_called_once_with(0)
    mock_cap.set.assert_called()


def test_camera_fails_to_open(mock_cv2):
    """CameraClient raises if camera cannot be opened."""
    mock_cap = MagicMock()
    mock_cap.isOpened.return_value = False
    mock_cv2.VideoCapture.return_value = mock_cap

    with patch.dict("os.environ", {"CAMERA_SOURCE": "0"}):
        client = CameraClient()
        with pytest.raises(RuntimeError, match="Failed to open camera"):
            client.get_frame()


def test_camera_read_fails(mock_cv2):
    """CameraClient returns False if read fails."""
    mock_cap = MagicMock()
    mock_cap.isOpened.return_value = True
    mock_cap.read.return_value = (False, None)
    mock_cv2.VideoCapture.return_value = mock_cap

    with patch.dict("os.environ", {"CAMERA_SOURCE": "0"}):
        client = CameraClient()
        success, frame = client.get_frame()

    assert success is False


def test_get_snapshot_b64(mock_cv2):
    """get_snapshot_b64 returns base64-encoded frame."""
    mock_cap = MagicMock()
    mock_cap.isOpened.return_value = True
    mock_cap.read.return_value = (True, b"frame")
    mock_cv2.VideoCapture.return_value = mock_cap
    mock_cv2.imencode.return_value = (True, _encoded_buffer(b"encoded_data"))

    with patch.dict("os.environ", {"CAMERA_SOURCE": "0"}):
        client = CameraClient()
        snapshot_b64 = client.get_snapshot_b64()

    assert isinstance(snapshot_b64, str)
    assert len(snapshot_b64) > 0
    # Round-trips to exactly what the encoder produced - proves this is really base64 of the
    # frame, not just some non-empty string.
    import base64

    assert base64.b64decode(snapshot_b64) == b"encoded_data"
