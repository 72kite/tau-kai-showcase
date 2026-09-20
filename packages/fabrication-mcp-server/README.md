# fabrication-mcp-server

The fabrication MCP server for Project Tau — see [`project-tau-plan.md`](../project-tau-plan.md)
Section 5.7. Phase 2.7 domain server: 3D printer control via OctoPrint or Moonraker (Klipper).
Independently deployable, like every domain server in Tau's architecture — this package does not
depend on `tau-core`.

## Layout

```
src/fabrication_mcp_server/
  octoprint_client.py    OctoPrintClient - REST API wrapper for OctoPrint
  moonraker_client.py    MoonrakerClient - REST API wrapper for Moonraker/Klipper
  server.py              FastMCP tools, routes to appropriate printer client
tests/
  test_server.py         tool functions with monkeypatched clients
  conftest.py
```

## Tools

- `get_printer_status()` — read-only: state, temperature, bed temp, current layer.
- `submit_print_job(gcode_path, bed_temp, nozzle_temp)` — submit G-code for printing. **Requires
  human approval** — allocating hardware time is a resource commitment.
- `pause_print()` — pause current job (read-write but reversible).
- `resume_print()` — resume a paused job.
- `cancel_print()` — cancel current job. **Requires human approval** — waste mitigation.

## Configuration

Set `PRINTER_TYPE` to choose backend:

```bash
# OctoPrint (FDM, most common)
export PRINTER_TYPE=octoprint
export OCTOPRINT_URL=http://192.168.1.100:5000
export OCTOPRINT_API_KEY=your_api_key

# Moonraker (Klipper, high-performance FDM)
export PRINTER_TYPE=moonraker
export MOONRAKER_URL=http://192.168.1.100:7125
```

Both OctoPrint and Moonraker expose REST APIs; this server wraps them uniformly so Tau sees a
consistent interface regardless of printer type.

## Setup

```bash
python -m venv .venv
.venv/Scripts/activate        # .venv/bin/activate on Linux/macOS
pip install -e ".[dev]"
cp .env.example .env           # configure PRINTER_TYPE and credentials
pytest
```

## Wiring into tau-core

Registered in `tau-core/config/servers.yaml` as `fabrication-mcp-server`, spawned via
`python -m fabrication_mcp_server.server`. Install with
`pip install -e ../fabrication-mcp-server` into tau-core's `.venv`.

## CDG Rules

Defined in `tau-core/config/cdg_rules.yaml`:
- `fabrication-submit-job-needs-approval`: `submit_print_job` requires approval
- `fabrication-cancel-job-needs-approval`: `cancel_print` requires approval

Read-only tools (`get_printer_status`, `pause_print`, `resume_print`) fall through to
`default_effect: allow`.

## Supported Printers

- **OctoPrint**: Prusa, Anycubic, Creality, Ender, Anet, and any FDM printer that can run
  OctoPrint (RPi + USB).
- **Moonraker/Klipper**: Voron, Ender 3 (with Klipper), Sovol, and any printer with Klipper
  firmware.

## Manual smoke test

Ensure printer is online and accessible. Set credentials in `.env`. Connect via
`tau_core.mcp_client.MCPClientManager`. Call `get_printer_status()` to verify API connectivity.

With a print job ready, call `submit_print_job("model.gcode", 60, 200)` to queue it.
