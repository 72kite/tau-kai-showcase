import os
import requests


class MoonrakerClient:
    """Moonraker (Klipper) REST API wrapper for 3D printer control."""

    def __init__(self):
        self.base_url = os.getenv("MOONRAKER_URL", "http://localhost:7125")

    def get_printer_status(self) -> dict:
        """Get printer status: state, temperatures, etc."""
        try:
            resp = requests.get(
                f"{self.base_url}/printer/info", timeout=5
            )
            resp.raise_for_status()
            data = resp.json().get("result", {})
            return {
                "state": data.get("state", "unknown"),
                "state_message": data.get("state_message", ""),
                "temperatures": data.get("temperatures", {}),
            }
        except Exception as e:
            raise RuntimeError(f"Moonraker status failed: {e}")

    def submit_print_job(self, gcode_path: str, print_bed_temp: int = 60, print_nozzle_temp: int = 200) -> dict:
        """Submit a print job via Moonraker."""
        try:
            resp = requests.post(
                f"{self.base_url}/printer/gcode/script",
                json={"script": f"G28\nG29\nM104 S{print_nozzle_temp}\nM140 S{print_bed_temp}"},
                timeout=10,
            )
            resp.raise_for_status()

            # Start print
            start_resp = requests.post(
                f"{self.base_url}/printer/print/start",
                json={"filename": gcode_path},
                timeout=5,
            )
            start_resp.raise_for_status()

            return {
                "success": True,
                "gcode_path": gcode_path,
                "bed_temp": print_bed_temp,
                "nozzle_temp": print_nozzle_temp,
            }
        except Exception as e:
            raise RuntimeError(f"Moonraker submit job failed: {e}")

    def pause_print(self) -> dict:
        """Pause the current print job."""
        try:
            resp = requests.post(
                f"{self.base_url}/printer/print/pause",
                timeout=5,
            )
            resp.raise_for_status()
            return {"success": True, "action": "paused"}
        except Exception as e:
            raise RuntimeError(f"Moonraker pause failed: {e}")

    def resume_print(self) -> dict:
        """Resume a paused print job."""
        try:
            resp = requests.post(
                f"{self.base_url}/printer/print/resume",
                timeout=5,
            )
            resp.raise_for_status()
            return {"success": True, "action": "resumed"}
        except Exception as e:
            raise RuntimeError(f"Moonraker resume failed: {e}")

    def cancel_print(self) -> dict:
        """Cancel the current print job."""
        try:
            resp = requests.post(
                f"{self.base_url}/printer/print/cancel",
                timeout=5,
            )
            resp.raise_for_status()
            return {"success": True, "action": "cancelled"}
        except Exception as e:
            raise RuntimeError(f"Moonraker cancel failed: {e}")
