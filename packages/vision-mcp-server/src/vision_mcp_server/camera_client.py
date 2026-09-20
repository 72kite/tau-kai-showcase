import os
import sys
import base64


class CameraClient:
    """Wrapper around OpenCV for camera access and snapshot capture."""

    def __init__(self):
        self.camera_source = int(os.getenv("CAMERA_SOURCE", "0"))
        self.width = int(os.getenv("CAMERA_WIDTH", "640"))
        self.height = int(os.getenv("CAMERA_HEIGHT", "480"))
        self.cap = None

    def _ensure_open(self):
        """Open camera if not already open."""
        if self.cap is None:
            import cv2

            if sys.platform.startswith("win"):
                # Found by live-testing against a real webcam (Phase 10.5): OpenCV's default
                # Windows backend, Media Foundation, hung *indefinitely* opening the camera when
                # called from a worker thread spawned off a running asyncio event loop (exactly
                # how server.py now runs this, since a blocking camera call inline on the loop
                # would freeze the whole MCP session - see get_snapshot's docstring). A plain
                # script or an ordinary threading.Thread opened the same camera instantly; only
                # that specific combination deadlocked - almost certainly MSMF's COM apartment
                # model reacting badly to whatever apartment state the async runtime's own
                # thread-pool machinery leaves a worker thread in. DirectShow has no such
                # requirement and opens the same device just as well.
                self.cap = cv2.VideoCapture(self.camera_source, cv2.CAP_DSHOW)
            else:
                self.cap = cv2.VideoCapture(self.camera_source)
            if not self.cap.isOpened():
                raise RuntimeError(
                    f"Failed to open camera {self.camera_source}. "
                    "Check device is connected or set CAMERA_SOURCE env var."
                )
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)

    def get_frame(self) -> tuple[bool, bytes]:
        """Get current frame from camera. Returns (success, frame_bytes)."""
        import cv2
        self._ensure_open()
        ret, frame = self.cap.read()
        if not ret:
            return False, b""
        return True, cv2.imencode(".jpg", frame)[1].tobytes()

    def get_snapshot_b64(self) -> str:
        """Get current snapshot as base64-encoded JPEG."""
        success, frame_bytes = self.get_frame()
        if not success:
            raise RuntimeError("Failed to capture frame")
        return base64.b64encode(frame_bytes).decode("utf-8")

    def close(self):
        """Release camera."""
        if self.cap is not None:
            self.cap.release()
            self.cap = None

    def __del__(self):
        self.close()


def get_camera_client():
    """Factory: CAMERA_SOURCE=kinect2 selects Kinect2Client (libfreenect2 - see that module's
    docstring for why a Kinect v2 needs a completely different backend than a UVC webcam);
    anything else is passed through to CameraClient as its usual int device index, unchanged
    default behavior. One switch point so server.py never needs to know which backend is live -
    both expose the same get_frame()/get_snapshot_b64()/close() shape.
    """
    if os.getenv("CAMERA_SOURCE", "0").strip().lower() == "kinect2":
        from vision_mcp_server.kinect2_client import Kinect2Client

        return Kinect2Client()
    return CameraClient()
