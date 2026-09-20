import json
import pytest
from unittest.mock import MagicMock

import robotics_mcp_server.server as server_module
from robotics_mcp_server.patrol_routes import PatrolRoute
from robotics_mcp_server.server import (
    get_telemetry,
    list_patrol_routes,
    patrol_route,
    return_to_home,
    emergency_stop,
)


@pytest.fixture
def mock_drone(monkeypatch):
    mock = MagicMock()
    mock.get_telemetry.return_value = {"battery_pct": 87, "lat": 0.0, "lon": 0.0, "alt": 0}
    mock.start_patrol.return_value = {"success": True, "status": "en_route"}
    mock.return_to_home.return_value = {"success": True, "status": "returning"}
    mock.emergency_stop.return_value = {"success": True, "status": "stopped"}
    monkeypatch.setattr(server_module, "_drone_client", mock)
    monkeypatch.setenv("ROBOT_TYPE", "drone")
    return mock


@pytest.fixture
def mock_route_store(monkeypatch):
    store = MagicMock()
    store.list_routes.return_value = [
        PatrolRoute(name="perimeter", description="Perimeter walk", waypoints=[{"lat": 0, "lon": 0}]),
    ]
    store.get.return_value = PatrolRoute(
        name="perimeter", description="Perimeter walk", waypoints=[{"lat": 0, "lon": 0}]
    )
    monkeypatch.setattr(server_module, "_route_store", store)
    return store


def test_get_telemetry_returns_json(mock_drone):
    result = get_telemetry()
    data = json.loads(result)

    assert data["battery_pct"] == 87


def test_list_patrol_routes_returns_json(mock_route_store):
    result = list_patrol_routes()
    data = json.loads(result)

    assert data["routes"][0]["name"] == "perimeter"
    assert data["routes"][0]["waypoint_count"] == 1


def test_patrol_route_uses_preapproved_waypoints(mock_drone, mock_route_store):
    """patrol_route() must resolve the route by name through the store, not accept waypoints
    directly - there is no waypoints parameter on the tool at all (see server.py)."""
    result = patrol_route("perimeter")
    data = json.loads(result)

    mock_route_store.get.assert_called_once_with("perimeter")
    mock_drone.start_patrol.assert_called_once_with([{"lat": 0, "lon": 0}])
    assert data["success"] is True
    assert data["route_name"] == "perimeter"


def test_patrol_route_rejects_unknown_route_name(mock_drone, mock_route_store):
    from robotics_mcp_server.patrol_routes import UnknownRouteError

    mock_route_store.get.side_effect = UnknownRouteError("Unknown patrol route 'backyard'")

    with pytest.raises(ValueError):
        patrol_route("backyard")


def test_return_to_home_returns_json(mock_drone):
    result = return_to_home()
    data = json.loads(result)

    assert data["status"] == "returning"


def test_emergency_stop_returns_json(mock_drone):
    result = emergency_stop()
    data = json.loads(result)

    assert data["status"] == "stopped"
