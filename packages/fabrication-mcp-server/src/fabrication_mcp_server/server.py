import json
import os
from mcp.server.fastmcp import FastMCP

from fabrication_mcp_server.octoprint_client import OctoPrintClient
from fabrication_mcp_server.moonraker_client import MoonrakerClient

server = FastMCP(
    "fabrication-mcp-server",
    host=os.getenv("FASTMCP_HOST", "127.0.0.1"),
    port=int(os.getenv("FASTMCP_PORT", "8000")),
)

# Monkeypatch seams for tests
_octoprint_client = None
_moonraker_client = None


def _get_printer_type() -> str:
    """Get configured printer type."""
    return os.getenv("PRINTER_TYPE", "octoprint").lower()


def _get_client():
    """Get the appropriate printer client based on configuration."""
    global _octoprint_client, _moonraker_client

    printer_type = _get_printer_type()

    if printer_type == "moonraker":
        if _moonraker_client is None:
            _moonraker_client = MoonrakerClient()
        return _moonraker_client
    else:
        if _octoprint_client is None:
            _octoprint_client = OctoPrintClient()
        return _octoprint_client


@server.tool()
def get_printer_status() -> str:
    """How is the 3D PRINTER doing? Is it printing, how far along, how hot?

    Answers "what's the status of the 3D printer", "is the printer still going", "how long left
    on the print", "what temperature is the bed" - returns printer state, nozzle and bed
    temperature, and current layer.

    This is the tool for any question about the 3D printer. Do not answer it with
    utility-mcp-server__get_system_status, which reports Tau's OWN machine and knows nothing
    about the printer - that substitution is exactly what the 2026-07-22 eval recorded."""
    client = _get_client()
    status = client.get_printer_status()
    return json.dumps(status)


@server.tool()
def submit_print_job(
    gcode_path: str, print_bed_temp: int = 60, print_nozzle_temp: int = 200
) -> str:
    """Submit a 3D print job. Requires human approval.

    gcode_path: local file path or URL to G-code file
    print_bed_temp: heated bed temperature in Celsius
    print_nozzle_temp: nozzle temperature in Celsius
    """
    client = _get_client()
    result = client.submit_print_job(gcode_path, print_bed_temp, print_nozzle_temp)
    return json.dumps(result)


@server.tool()
def pause_print() -> str:
    """Pause the current print job."""
    client = _get_client()
    result = client.pause_print()
    return json.dumps(result)


@server.tool()
def resume_print() -> str:
    """Resume a paused print job."""
    client = _get_client()
    result = client.resume_print()
    return json.dumps(result)


@server.tool()
def cancel_print() -> str:
    """Cancel the current print job. Requires human approval."""
    client = _get_client()
    result = client.cancel_print()
    return json.dumps(result)


if __name__ == "__main__":
    server.run(transport=os.getenv("MCP_TRANSPORT", "stdio"))
