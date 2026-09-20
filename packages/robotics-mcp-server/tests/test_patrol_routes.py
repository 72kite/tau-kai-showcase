import json
import pytest

from robotics_mcp_server.patrol_routes import PatrolRouteStore, UnknownRouteError


@pytest.fixture
def routes_file(tmp_path):
    path = tmp_path / "patrol_routes.json"
    path.write_text(
        json.dumps(
            {
                "routes": [
                    {
                        "name": "perimeter",
                        "description": "Full perimeter walk",
                        "waypoints": [{"lat": 0.0, "lon": 0.0, "alt": 15}],
                    },
                    {"name": "driveway", "waypoints": []},
                ]
            }
        )
    )
    return path


def test_missing_config_yields_no_routes(tmp_path):
    """No config file is not an error - it's the fail-closed default (nothing pre-approved)."""
    store = PatrolRouteStore(path=str(tmp_path / "does_not_exist.json"))
    assert store.list_routes() == []


def test_loads_routes_from_config(routes_file):
    store = PatrolRouteStore(path=str(routes_file))
    names = {r.name for r in store.list_routes()}
    assert names == {"perimeter", "driveway"}


def test_get_known_route(routes_file):
    store = PatrolRouteStore(path=str(routes_file))
    route = store.get("perimeter")
    assert route.description == "Full perimeter walk"
    assert route.waypoints == [{"lat": 0.0, "lon": 0.0, "alt": 15}]


def test_get_unknown_route_raises(routes_file):
    """This is the enforcement point for 'pre-approved routes only' - patrol_route() in
    server.py has no other way to obtain waypoints, so an unrecognized name must fail here.
    """
    store = PatrolRouteStore(path=str(routes_file))
    with pytest.raises(UnknownRouteError):
        store.get("backyard-nobody-approved")


def test_reload_picks_up_config_changes(routes_file):
    store = PatrolRouteStore(path=str(routes_file))
    assert len(store.list_routes()) == 2

    routes_file.write_text(json.dumps({"routes": []}))
    store.reload()

    assert store.list_routes() == []
