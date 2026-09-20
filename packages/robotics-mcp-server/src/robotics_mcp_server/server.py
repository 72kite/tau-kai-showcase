import json
import os
from mcp.server.fastmcp import FastMCP

from robotics_mcp_server.drone_client import DroneClient
from robotics_mcp_server.robot_dog_client import RobotDogClient
from robotics_mcp_server.patrol_routes import PatrolRouteStore, UnknownRouteError

server = FastMCP(
    "robotics-mcp-server",
    host=os.getenv("FASTMCP_HOST", "127.0.0.1"),
    port=int(os.getenv("FASTMCP_PORT", "8000")),
)

# Monkeypatch seams for tests
_drone_client = None
_robot_dog_client = None
_route_store = None


def _get_robot_type() -> str:
    return os.getenv("ROBOT_TYPE", "drone").lower()


def _get_client():
    global _drone_client, _robot_dog_client
    if _get_robot_type() == "robot_dog":
        if _robot_dog_client is None:
            _robot_dog_client = RobotDogClient()
        return _robot_dog_client
    if _drone_client is None:
        _drone_client = DroneClient()
    return _drone_client


def _get_route_store() -> PatrolRouteStore:
    global _route_store
    if _route_store is None:
        _route_store = PatrolRouteStore()
    return _route_store


@server.tool()
def get_telemetry() -> str:
    """Get current telemetry: battery, GPS position, altitude, flight/walk status. Read-only."""
    client = _get_client()
    return json.dumps(client.get_telemetry())


@server.tool()
def list_patrol_routes() -> str:
    """List pre-approved patrol routes. Read-only.

    Routes are defined out-of-band in the PATROL_ROUTES_PATH config file by a human, not
    accepted as coordinates from a tool call - use this to see what patrol_route() may
    reference by name.
    """
    store = _get_route_store()
    return json.dumps(
        {
            "routes": [
                {
                    "name": r.name,
                    "description": r.description,
                    "waypoint_count": len(r.waypoints),
                }
                for r in store.list_routes()
            ]
        }
    )


@server.tool()
def patrol_route(route_name: str) -> str:
    """Start a pre-approved patrol route by name. Requires human approval.

    route_name: must match a route already defined in PATROL_ROUTES_PATH (see
    list_patrol_routes) - arbitrary waypoints are never accepted here, by design, regardless
    of what the CDG would otherwise allow.
    """
    store = _get_route_store()
    try:
        route = store.get(route_name)
    except UnknownRouteError as e:
        raise ValueError(str(e))
    client = _get_client()
    result = client.start_patrol(route.waypoints)
    result["route_name"] = route_name
    return json.dumps(result)


@server.tool()
def return_to_home() -> str:
    """Command the robot to return to its home/dock position. Requires human approval."""
    client = _get_client()
    return json.dumps(client.return_to_home())


@server.tool()
def emergency_stop() -> str:
    """Immediately halt all movement. Fail-safe direction - does NOT require human approval,
    mirroring security-mcp-server's enter_lockdown: stopping a physical actuator must never be
    gated behind an approval round-trip, only starting or moving one should be.
    """
    client = _get_client()
    return json.dumps(client.emergency_stop())


if __name__ == "__main__":
    server.run(transport=os.getenv("MCP_TRANSPORT", "stdio"))
