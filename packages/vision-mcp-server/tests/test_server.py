import json
import pytest
from unittest.mock import patch, MagicMock

from vision_mcp_server.server import (
    get_snapshot,
    describe_scene,
    detect_faces,
    get_active_touches,
    ptz_pan,
    ptz_tilt,
    ptz_zoom,
    track_object,
)


@pytest.fixture
def mock_camera(monkeypatch):
    """Inject a mock CameraClient."""
    mock = MagicMock()
    mock.get_snapshot_b64.return_value = "base64_snapshot_data"
    monkeypatch.setattr("vision_mcp_server.server._camera_client", mock)
    return mock


@pytest.fixture
def mock_face(monkeypatch):
    """Inject a mock FaceDetector."""
    mock = MagicMock()
    mock.detect_faces_from_b64.return_value = [
        {
            "bbox": [10, 20, 100, 200],
            "embedding": [0.1, 0.2, 0.3],
            "confidence": 0.95,
        }
    ]
    monkeypatch.setattr("vision_mcp_server.server._face_detector", mock)
    return mock


@pytest.fixture
def mock_scene(monkeypatch):
    """Inject a mock SceneDescriber."""
    mock = MagicMock()
    mock.describe_scene_from_b64.return_value = "well-lit, high detail, greenish tones"
    monkeypatch.setattr("vision_mcp_server.server._scene_describer", mock)
    return mock


@pytest.fixture
def mock_ptz(monkeypatch):
    """Inject a mock PTZClient."""
    mock = MagicMock()
    mock.pan.return_value = {"status": "success"}
    mock.tilt.return_value = {"status": "success"}
    mock.zoom.return_value = {"status": "success"}
    monkeypatch.setattr("vision_mcp_server.server._ptz_client", mock)
    return mock


@pytest.fixture
def mock_touch(monkeypatch):
    """Inject a mock TouchClient."""
    mock = MagicMock()
    mock.get_active_touches.return_value = [
        {"id": 1, "x": 0.5, "y": 0.5, "x_velocity": 0.0, "y_velocity": 0.0, "acceleration": 0.0}
    ]
    monkeypatch.setattr("vision_mcp_server.server._touch_client", mock)
    return mock


async def test_get_snapshot_returns_json(mock_camera):
    """get_snapshot tool returns JSON with base64 snapshot."""
    result = await get_snapshot()
    data = json.loads(result)

    assert "snapshot" in data
    assert data["format"] == "jpeg"
    assert data["snapshot"] == "base64_snapshot_data"


async def test_describe_scene_returns_json(mock_camera, mock_scene):
    """describe_scene tool returns JSON with description."""
    result = await describe_scene()
    data = json.loads(result)

    assert "description" in data
    assert "well-lit" in data["description"]


async def test_detect_faces_returns_json(mock_camera, mock_face):
    """detect_faces tool returns JSON with face data."""
    result = await detect_faces()
    data = json.loads(result)

    assert data["face_count"] == 1
    assert "faces" in data
    assert data["faces"][0]["confidence"] == 0.95


def test_ptz_pan_returns_json(mock_ptz):
    """ptz_pan tool returns JSON."""
    result = ptz_pan(45.0)
    data = json.loads(result)

    assert data["status"] == "panned"
    assert data["degrees"] == 45.0


def test_ptz_tilt_returns_json(mock_ptz):
    """ptz_tilt tool returns JSON."""
    result = ptz_tilt(-30.0)
    data = json.loads(result)

    assert data["status"] == "tilted"
    assert data["degrees"] == -30.0


def test_ptz_zoom_returns_json(mock_ptz):
    """ptz_zoom tool returns JSON."""
    result = ptz_zoom(2.0)
    data = json.loads(result)

    assert data["status"] == "zoomed"
    assert data["factor"] == 2.0


async def test_track_object_returns_json(mock_camera, mock_face):
    """track_object tool returns JSON with tracking data."""
    result = await track_object("person")
    data = json.loads(result)

    assert data["object_type"] == "person"
    assert data["tracked_count"] == 1
    assert "tracking_data" in data


def test_get_active_touches_returns_json(mock_touch):
    """get_active_touches tool returns JSON with the current touch points."""
    result = get_active_touches()
    data = json.loads(result)

    assert data["touch_count"] == 1
    assert data["touches"][0]["id"] == 1


def test_camera_tools_are_async_and_never_block_the_event_loop():
    """Regression guard for the live-testing find: get_snapshot/describe_scene/detect_faces/
    track_object all run blocking camera/model I/O through anyio.to_thread, so FastMCP - which
    calls a plain `def` tool inline on the event loop - can never freeze on them again. Asserted
    at the type-signature level, not just by the tests above passing, since a well-mocked test
    can't tell 'ran off-thread' from 'ran inline and happened to return fast'."""
    import inspect

    for tool in (get_snapshot, describe_scene, detect_faces, track_object):
        assert inspect.iscoroutinefunction(tool), f"{tool.__name__} must be async"
