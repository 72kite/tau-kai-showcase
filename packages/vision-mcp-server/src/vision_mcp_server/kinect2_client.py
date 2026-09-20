"""Kinect v2 camera backend via libfreenect2/pylibfreenect2.

The Kinect v2's RGB sensor is NOT a standard UVC webcam - `cv2.VideoCapture` (CameraClient's only
backend) cannot open it at all. It speaks a proprietary USB3 protocol that only libfreenect2 (a
real userspace driver, not a Python package alone) understands; `pylibfreenect2` is a Cython
wrapper around it, requiring libfreenect2 built and installed on the system (see
docker/vision.Dockerfile) before `pip install` can even compile the wrapper.

Selected by CAMERA_SOURCE=kinect2 (see camera_client.py's factory) instead of an integer device
index. Implements the same (bool, jpeg_bytes) get_frame()/close() interface as CameraClient so
server.py's tools don't need to know which backend is active.

Known operational requirements, not just code:
- The host's `usbfs_memory_mb` kernel parameter must be raised well above its 16MB default (the
  color stream needs far larger USB buffer allocations) or streaming fails with
  usb_submit_transfer errors - see the Proxmox host's /etc/modprobe.d/usbfs-kinect2.conf.
- CPU-only pipeline (CpuPacketPipeline) deliberately, not OpenGL/OpenCL/CUDA - this box has no GPU
  passthrough into the container (Project Tau is CPU-only by design here, see
  project-tau-plan.md). Slower per-frame than a GPU pipeline, but the only one that doesn't need
  a driver stack this container will never have.
"""

from __future__ import annotations

import base64
import logging

logger = logging.getLogger(__name__)

# The Kinect v2 color frame ships as BGRX (4 bytes/px, the 4th unused) at a fixed 1920x1080 -
# never anything else, unlike a UVC webcam where the driver picks a mode. Cropped/resized down to
# CAMERA_WIDTH/CAMERA_HEIGHT in get_frame() so callers see the same shape as CameraClient's output
# regardless of which backend is active.
_COLOR_WIDTH = 1920
_COLOR_HEIGHT = 1080


class Kinect2Client:
    """Wraps pylibfreenect2 for camera access and snapshot capture - the Kinect v2 counterpart to
    CameraClient. Only the color stream is used (get_snapshot/detect_faces/describe_scene/
    track_object all just want an RGB frame); depth/IR frames are requested from the device
    (SyncMultiFrameListener needs at least one type registered to get a stream going in some
    libfreenect2 builds) but never read - a documented follow-up, not this pass's scope, would be
    surfacing depth data as its own tool.
    """

    def __init__(self):
        import os

        self.width = int(os.getenv("CAMERA_WIDTH", "640"))
        self.height = int(os.getenv("CAMERA_HEIGHT", "480"))
        self._fn = None
        self._device = None
        self._listener = None

    def _ensure_open(self):
        if self._device is not None:
            return
        from pylibfreenect2 import (
            CpuPacketPipeline,
            Freenect2,
            FrameType,
            SyncMultiFrameListener,
        )

        fn = Freenect2()
        num_devices = fn.enumerateDevices()
        if num_devices == 0:
            raise RuntimeError(
                "No Kinect v2 found. Check the device is connected and the udev rule / LXC "
                "passthrough (see project-tau-plan.md) actually reached /dev/kinect2."
            )
        serial = fn.getDeviceSerialNumber(0)
        # CPU pipeline: see the module docstring - no GPU in this container, ever.
        device = fn.openDevice(serial, pipeline=CpuPacketPipeline())

        listener = SyncMultiFrameListener(FrameType.Color)
        device.setColorFrameListener(listener)
        # setIrAndDepthFrameListener still required even though we never read those frames -
        # libfreenect2 will not start the color stream reliably without one registered.
        device.setIrAndDepthFrameListener(listener)

        if not device.start():
            raise RuntimeError("Kinect v2 found but failed to start streaming.")

        self._fn = fn
        self._device = device
        self._listener = listener

    def get_frame(self) -> tuple[bool, bytes]:
        """Get the current color frame from the Kinect. Returns (success, jpeg_bytes)."""
        import cv2
        import numpy as np
        from pylibfreenect2 import FrameType

        self._ensure_open()
        # 10s: generous but bounded - a genuinely stalled Kinect stream should surface as a
        # failure the caller can report, not hang the MCP tool call indefinitely.
        frames = self._listener.waitForNewFrame(milliseconds=10_000)
        if frames is None:
            return False, b""
        try:
            color = frames[FrameType.Color]
            # BGRX -> BGR (drop the unused 4th channel), then down to the configured size so
            # this backend's output shape matches CameraClient's regardless of the Kinect's
            # fixed native 1920x1080.
            bgr = np.frombuffer(color.asarray(), dtype=np.uint8).reshape(
                _COLOR_HEIGHT, _COLOR_WIDTH, 4
            )[:, :, :3]
            resized = cv2.resize(bgr, (self.width, self.height))
            ok, encoded = cv2.imencode(".jpg", resized)
            if not ok:
                return False, b""
            return True, encoded.tobytes()
        finally:
            self._listener.release(frames)

    def get_snapshot_b64(self) -> str:
        """Get current snapshot as base64-encoded JPEG - mirrors CameraClient's method so the
        factory in camera_client.py can hand back either backend interchangeably."""
        success, frame_bytes = self.get_frame()
        if not success:
            raise RuntimeError("Failed to capture frame from Kinect v2")
        return base64.b64encode(frame_bytes).decode("utf-8")

    def close(self):
        if self._device is not None:
            try:
                self._device.stop()
                self._device.close()
            except Exception:  # noqa: BLE001 - best-effort teardown, never let it mask the real error
                logger.warning("Kinect v2 teardown raised", exc_info=True)
            self._device = None
            self._listener = None
            self._fn = None

    def __del__(self):
        self.close()
