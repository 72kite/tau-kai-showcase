import os
import requests


class OctoPrintClient:
    """OctoPrint REST API wrapper for 3D printer control."""

    def __init__(self):
        self.base_url = os.getenv("OCTOPRINT_URL", "http://localhost:5000")
        self.api_key = os.getenv("OCTOPRINT_API_KEY", "")

    def _check_auth(self):
        """Raise if API key not configured."""
        if not self.api_key:
            raise RuntimeError("OCTOPRINT_API_KEY not set")

    def get_printer_status(self) -> dict:
        """Get printer status: state, temperature, bed temperature, etc."""
        self._check_auth()
        try:
            headers = {"X-Api-Key": self.api_key}
            resp = requests.get(
                f"{self.base_url}/api/printer", headers=headers, timeout=5
            )
            resp.raise_for_status()
            data = resp.json()
            return {
                "state": data.get("state", {}).get("text", "unknown"),
                "temperature": data.get("temperature", {}),
                "job": data.get("currentZ", None),
            }
        except Exception as e:
            raise RuntimeError(f"OctoPrint status failed: {e}")

    def submit_print_job(self, gcode_path: str, print_bed_temp: int = 60, print_nozzle_temp: int = 200) -> dict:
        """Submit a print job. gcode_path can be local file or URL."""
        self._check_auth()
        try:
            headers = {"X-Api-Key": self.api_key}
            files = {"file": open(gcode_path, "rb")}
            resp = requests.post(
                f"{self.base_url}/api/files/local",
                files=files,
                headers=headers,
                timeout=10,
            )
            resp.raise_for_status()

            job_data = resp.json()
            job_name = job_data.get("name", "unknown")

            start_resp = requests.post(
                f"{self.base_url}/api/files/local/{job_name}",
                json={"command": "select", "print": True},
                headers=headers,
                timeout=5,
            )
            start_resp.raise_for_status()

            return {
                "success": True,
                "job_name": job_name,
                "bed_temp": print_bed_temp,
                "nozzle_temp": print_nozzle_temp,
            }
        except Exception as e:
            raise RuntimeError(f"OctoPrint submit job failed: {e}")

    def pause_print(self) -> dict:
        """Pause the current print job."""
        self._check_auth()
        try:
            headers = {"X-Api-Key": self.api_key}
            resp = requests.post(
                f"{self.base_url}/api/job",
                json={"command": "pause", "action": "pause"},
                headers=headers,
                timeout=5,
            )
            resp.raise_for_status()
            return {"success": True, "action": "paused"}
        except Exception as e:
            raise RuntimeError(f"OctoPrint pause failed: {e}")

    def resume_print(self) -> dict:
        """Resume a paused print job."""
        self._check_auth()
        try:
            headers = {"X-Api-Key": self.api_key}
            resp = requests.post(
                f"{self.base_url}/api/job",
                json={"command": "pause", "action": "resume"},
                headers=headers,
                timeout=5,
            )
            resp.raise_for_status()
            return {"success": True, "action": "resumed"}
        except Exception as e:
            raise RuntimeError(f"OctoPrint resume failed: {e}")

    def cancel_print(self) -> dict:
        """Cancel the current print job."""
        self._check_auth()
        try:
            headers = {"X-Api-Key": self.api_key}
            resp = requests.post(
                f"{self.base_url}/api/job",
                json={"command": "cancel"},
                headers=headers,
                timeout=5,
            )
            resp.raise_for_status()
            return {"success": True, "action": "cancelled"}
        except Exception as e:
            raise RuntimeError(f"OctoPrint cancel failed: {e}")
