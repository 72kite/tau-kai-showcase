import asyncio
import json
import os

from mcp.server.fastmcp import FastMCP

from vision_mcp_server.camera_client import CameraClient, get_camera_client
from vision_mcp_server.face_detector import FaceDetector
from vision_mcp_server.scene_describer import SceneDescriber
from vision_mcp_server.ptz_client import PTZClient
from vision_mcp_server.touch_client import TouchClient

server = FastMCP(
    "vision-mcp-server",
    host=os.getenv("FASTMCP_HOST", "127.0.0.1"),
    port=int(os.getenv("FASTMCP_PORT", "8000")),
)

# Monkeypatch seams for tests
_camera_client = None
_face_detector = None
_scene_describer = None
_ptz_client = None
_touch_client = None


def _camera() -> CameraClient:
    global _camera_client
    if _camera_client is None:
        _camera_client = get_camera_client()
    return _camera_client


def _face() -> FaceDetector:
    global _face_detector
    if _face_detector is None:
        _face_detector = FaceDetector()
    return _face_detector


def _scene() -> SceneDescriber:
    global _scene_describer
    if _scene_describer is None:
        _scene_describer = SceneDescriber()
    return _scene_describer


def _ptz() -> PTZClient:
    global _ptz_client
    if _ptz_client is None:
        _ptz_client = PTZClient()
    return _ptz_client


def _touch() -> TouchClient:
    global _touch_client
    if _touch_client is None:
        _touch_client = TouchClient()
    return _touch_client


@server.tool()
async def get_snapshot() -> str:
    """Look through the CAMERA right now - take a photo of what it currently sees.

    Answers "take a snapshot", "what does the camera see", "check the front door camera", "show
    me the living room". Returns the current frame as a base64 JPEG. To get a description in
    words rather than an image, use describe_scene (it captures a frame itself).
    """
    # to_thread is load-bearing, not tidiness. FastMCP runs a plain `def` tool inline on the
    # event loop thread (func_metadata.call_fn_with_arg_validation), so a blocking camera read
    # there freezes the entire MCP session for as long as the grab takes - found live against a
    # real webcam in Phase 10.5. camera_client.py's _ensure_open has the Windows backend
    # deadlock this turned up along the way.
    camera = _camera()
    snapshot_b64 = await asyncio.to_thread(camera.get_snapshot_b64)
    return json.dumps({"snapshot": snapshot_b64, "format": "jpeg"})


@server.tool()
async def describe_scene(snapshot_b64: str = "") -> str:
    """What's in view right now? Describe the scene from the camera in words (lighting, detail
    level, dominant colors, objects detected) rather than just returning the raw image - use this
    when the user wants a description, not the photo itself. Captures a fresh frame if
    snapshot_b64 is empty."""
    if not snapshot_b64:
        camera = _camera()
        snapshot_b64 = await asyncio.to_thread(camera.get_snapshot_b64)

    describer = _scene()
    description = await asyncio.to_thread(describer.describe_scene_from_b64, snapshot_b64)
    return json.dumps({"description": description})


@server.tool()
async def detect_faces(snapshot_b64: str = "") -> str:
    """Detect faces in a snapshot and return embeddings. If snapshot_b64 empty, captures current.

    Returns list of {bbox, embedding, confidence}. Embeddings are ready to pass to
    memory-mcp-server's store_face for enrollment.
    """
    if not snapshot_b64:
        camera = _camera()
        snapshot_b64 = await asyncio.to_thread(camera.get_snapshot_b64)

    detector = _face()
    faces = await asyncio.to_thread(detector.detect_faces_from_b64, snapshot_b64)
    return json.dumps(
        {
            "face_count": len(faces),
            "faces": faces,
        }
    )


@server.tool()
def ptz_pan(degrees: float) -> str:
    """Pan the PTZ camera (left/right). Requires human approval.

    degrees: negative = pan left, positive = pan right.
    """
    ptz = _ptz()
    result = ptz.pan(degrees)
    return json.dumps({"status": "panned", "degrees": degrees, "result": result})


@server.tool()
def ptz_tilt(degrees: float) -> str:
    """Tilt the PTZ camera (up/down). Requires human approval.

    degrees: negative = tilt down, positive = tilt up.
    """
    ptz = _ptz()
    result = ptz.tilt(degrees)
    return json.dumps({"status": "tilted", "degrees": degrees, "result": result})


@server.tool()
def ptz_zoom(factor: float) -> str:
    """Zoom the PTZ camera. Requires human approval.

    factor: > 1 = zoom in, 0 < factor < 1 = zoom out.
    """
    ptz = _ptz()
    result = ptz.zoom(factor)
    return json.dumps({"status": "zoomed", "factor": factor, "result": result})


@server.tool()
async def track_object(object_type: str, snapshot_b64: str = "") -> str:
    """Track an object by type (person, car, dog, etc.). Placeholder for real tracking.

    For now, returns detected faces/objects in the scene. Real implementation would
    use continuous video tracking.
    """
    if not snapshot_b64:
        camera = _camera()
        snapshot_b64 = await asyncio.to_thread(camera.get_snapshot_b64)

    detector = _face()
    faces = await asyncio.to_thread(detector.detect_faces_from_b64, snapshot_b64)

    return json.dumps(
        {
            "object_type": object_type,
            "tracked_count": len(faces),
            "tracking_data": faces,
        }
    )


@server.tool()
def get_active_touches() -> str:
    """Current touch points on the AirTouch surface (read-only) - a Kinect/RealSense/OpenNI depth
    camera turned into a touchscreen by AirTouch (https://github.com/jing-interactive/AirTouch),
    which broadcasts touches as standard TUIO 1.1 messages that this tool reads from a background
    listener. Each point has an id, position (x, y, normalized 0..1 over the calibrated surface),
    and a velocity/acceleration vector. Requires TOUCH_ENABLED=true and AirTouch (or any other
    TUIO 1.1 source) actually running - raises a clear error otherwise, same as the PTZ tools do
    when PTZ_ENABLED is false.

    Reads an in-memory dict under a lock (the listener thread is already running), so this never
    blocks - no asyncio.to_thread needed, unlike the camera/model tools above.
    """
    touch = _touch()
    touches = touch.get_active_touches()
    return json.dumps({"touch_count": len(touches), "touches": touches})


if __name__ == "__main__":
    server.run(transport=os.getenv("MCP_TRANSPORT", "stdio"))
