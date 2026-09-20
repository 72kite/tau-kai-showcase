import os
import requests


class PTZClient:
    """PTZ (Pan-Tilt-Zoom) camera control via HTTP API."""

    def __init__(self):
        self.enabled = os.getenv("PTZ_ENABLED", "false").lower() == "true"
        self.base_url = os.getenv("PTZ_URL", "http://localhost:8000")

    def pan(self, degrees: float) -> dict:
        """Pan the camera (left/right). Negative = left, positive = right."""
        if not self.enabled:
            raise RuntimeError("PTZ not enabled. Set PTZ_ENABLED=true")

        try:
            resp = requests.post(
                f"{self.base_url}/ptz/pan", json={"degrees": degrees}, timeout=5
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            raise RuntimeError(f"PTZ pan failed: {e}")

    def tilt(self, degrees: float) -> dict:
        """Tilt the camera (up/down). Negative = down, positive = up."""
        if not self.enabled:
            raise RuntimeError("PTZ not enabled. Set PTZ_ENABLED=true")

        try:
            resp = requests.post(
                f"{self.base_url}/ptz/tilt", json={"degrees": degrees}, timeout=5
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            raise RuntimeError(f"PTZ tilt failed: {e}")

    def zoom(self, factor: float) -> dict:
        """Zoom the camera. Factor > 1 = zoom in, 0 < factor < 1 = zoom out."""
        if not self.enabled:
            raise RuntimeError("PTZ not enabled. Set PTZ_ENABLED=true")

        try:
            resp = requests.post(
                f"{self.base_url}/ptz/zoom", json={"factor": factor}, timeout=5
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            raise RuntimeError(f"PTZ zoom failed: {e}")

    def move_to(self, pan: float, tilt: float, zoom: float = 1.0) -> dict:
        """Move to absolute pan/tilt/zoom position."""
        if not self.enabled:
            raise RuntimeError("PTZ not enabled. Set PTZ_ENABLED=true")

        try:
            resp = requests.post(
                f"{self.base_url}/ptz/move",
                json={"pan": pan, "tilt": tilt, "zoom": zoom},
                timeout=5,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            raise RuntimeError(f"PTZ move failed: {e}")
