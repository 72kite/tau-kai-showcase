# robotics-mcp-server

The robotics MCP server for Project Tau — see [`project-tau-plan.md`](../project-tau-plan.md)
Section 8. Phase 5 domain server: outdoor drone patrol control (robot dog deferred until its
SDK/ROS integration is stable). Independently deployable, like every domain server in Tau's
architecture — this package does not depend on `tau-core`.

Phase 5 is explicit that robotics tools should be treated as "strictly read/observe +
pre-approved patrol routes" until the security-mcp integration is proven — this package
enforces that at the tool-schema level, not just via CDG policy: `patrol_route()` takes a
route **name**, never coordinates. There is no tool anywhere in this package that accepts
arbitrary waypoints from a caller.

## Layout

```
src/robotics_mcp_server/
  drone_client.py       DroneClient - REST wrapper around a flight-controller bridge
  robot_dog_client.py   RobotDogClient - placeholder, every method raises (integration deferred)
  patrol_routes.py       PatrolRouteStore - loads named routes from a human-edited JSON file
  server.py              FastMCP tools, routes to the configured robot client
tests/
  test_server.py          tool functions with monkeypatched clients/store
  test_patrol_routes.py   PatrolRouteStore behavior (tmp_path-backed)
  test_robot_dog_client.py confirms every action fails loudly, never fakes control
  conftest.py
config/
  patrol_routes.example.json   documents the route-file format
```

## Tools

- `get_telemetry()` — read-only: battery, GPS position, altitude, status.
- `list_patrol_routes()` — read-only: names/descriptions of pre-approved routes.
- `patrol_route(route_name)` — start a pre-approved patrol by name. **Requires human
  approval.** Unknown names are rejected before any client call is made.
- `return_to_home()` — return to home/dock. **Requires human approval.**
- `emergency_stop()` — immediate halt. **Does not require approval** — this is the one
  fail-safe direction, mirroring `security-mcp-server`'s `enter_lockdown`/`exit_lockdown`
  asymmetry: stopping a physical actuator must never wait on a human sign-off round-trip.

## Configuration

```bash
export ROBOT_TYPE=drone            # or robot_dog (not yet implemented - see robot_dog_client.py)
export DRONE_API_URL=http://192.168.1.50:8080
export DRONE_API_KEY=your_bearer_token
export PATROL_ROUTES_PATH=./config/patrol_routes.json   # copy from .example.json and edit
```

With no `PATROL_ROUTES_PATH` file present, `list_patrol_routes()` returns an empty list and
`patrol_route()` rejects every name — this is the correct fail-closed default, not a bug. A
human must populate the route file before Tau can patrol anything.

## Setup

```bash
python -m venv .venv
.venv/Scripts/activate        # .venv/bin/activate on Linux/macOS
pip install -e ".[dev]"
cp config/patrol_routes.example.json config/patrol_routes.json   # edit with real waypoints
pytest
```

## Wiring into tau-core

Registered in `tau-core/config/servers.yaml` as `robotics-mcp-server`, spawned via
`python -m robotics_mcp_server.server`. Install with
`pip install -e ../robotics-mcp-server` into tau-core's `.venv`.

## CDG Rules

Defined in `tau-core/config/cdg_rules.yaml`, in this order (first match wins):
1. `robotics-emergency-stop-is-safe` — `emergency_stop` is **allowed**, no approval.
2. `robotics-telemetry-is-read-only` — `get_telemetry` is **allowed**.
3. `robotics-list-routes-is-read-only` — `list_patrol_routes` is **allowed**.
4. `fabrication-and-robotics-actuation-needs-approval` — everything else on this server
   (`patrol_route`, `return_to_home`) **requires approval**.

The three specific allow rules must stay ordered before the blanket rule, or they would never
be reached (`tests/test_cdg.py` in `tau-core` asserts this against the real repo config).

## Robot dog

Not built yet. `RobotDogClient` exists so `ROBOT_TYPE=robot_dog` is a valid config value, but
every method raises `RobotDogIntegrationPending` rather than silently no-op'ing — a
misconfigured deployment must fail loudly, not pretend to control hardware that isn't wired up.

## Manual smoke test

Ensure the drone's flight-controller bridge is online and reachable at `DRONE_API_URL`. Copy
`config/patrol_routes.example.json` to `config/patrol_routes.json` and replace the placeholder
waypoints with real coordinates. Connect via `tau_core.mcp_client.MCPClientManager`. Call
`get_telemetry()` to verify connectivity, then `list_patrol_routes()` to confirm the route file
loaded correctly before ever calling `patrol_route()`.
