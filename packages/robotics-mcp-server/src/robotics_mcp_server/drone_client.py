import os
import requests


class DroneClient:
    """REST wrapper around a drone flight-controller bridge (e.g. a MAVLink/DroneKit companion
    service exposing HTTP endpoints - the exact bridge is a Phase 5 hardware-integration detail;
    this client only assumes a base URL and bearer token, matching the OctoPrint/Moonraker
    client pattern used elsewhere in this repo).
    """

    def __init__(self):
        self.base_url = os.getenv("DRONE_API_URL", "http://localhost:8080")
        self.api_key = os.getenv("DRONE_API_KEY", "")

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    def get_telemetry(self) -> dict:
        """Battery, GPS position, altitude, flight status. Read-only."""
        try:
            resp = requests.get(f"{self.base_url}/telemetry", headers=self._headers(), timeout=5)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            raise RuntimeError(f"Drone telemetry fetch failed: {e}")

    def start_patrol(self, waypoints: list) -> dict:
        """Begin flying a pre-approved waypoint list. Never called with LLM-supplied
        coordinates - waypoints always come from PatrolRouteStore, which only a human can edit.
        """
        try:
            resp = requests.post(
                f"{self.base_url}/patrol",
                json={"waypoints": waypoints},
                headers=self._headers(),
                timeout=10,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            raise RuntimeError(f"Drone patrol start failed: {e}")

    def return_to_home(self) -> dict:
        try:
            resp = requests.post(
                f"{self.base_url}/return_to_home", headers=self._headers(), timeout=10
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            raise RuntimeError(f"Drone return-to-home failed: {e}")

    def emergency_stop(self) -> dict:
        """Immediate halt. Kept as its own short-timeout call, separate from the other
        commands, so a slow/hung flight controller doesn't delay the one action that must
        never wait.
        """
        try:
            resp = requests.post(
                f"{self.base_url}/emergency_stop", headers=self._headers(), timeout=3
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            raise RuntimeError(f"Drone emergency stop failed: {e}")
