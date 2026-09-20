"""Pre-approved patrol route store.

project-tau-plan.md Phase 5 is explicit: robotics tools should be "strictly read/observe +
pre-approved patrol routes" until security-mcp integration is proven. This module is where
that constraint is actually enforced, not just documented - patrol_route() in server.py can
only reference a route by name from this store. There is no tool parameter anywhere in this
package that accepts raw waypoints/coordinates, so a compromised or hallucinating LLM has no
path to send the drone somewhere nobody pre-approved, independent of whatever the CDG allows.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_ROUTES_PATH = "./config/patrol_routes.json"


class UnknownRouteError(ValueError):
    pass


@dataclass
class PatrolRoute:
    name: str
    waypoints: list = field(default_factory=list)
    description: str = ""


class PatrolRouteStore:
    """Loads named patrol routes from a JSON config file a human edits out-of-band.

    Missing/empty config is not an error - it just means no routes are pre-approved yet, which
    is the correct fail-closed default (patrol_route() will reject every name until a human
    populates the file).
    """

    def __init__(self, path: str | None = None):
        self.path = Path(path or os.getenv("PATROL_ROUTES_PATH", DEFAULT_ROUTES_PATH))
        self._routes: dict[str, PatrolRoute] = {}
        self.reload()

    def reload(self) -> None:
        if not self.path.exists():
            self._routes = {}
            return
        with open(self.path) as f:
            data = json.load(f)
        self._routes = {
            entry["name"]: PatrolRoute(
                name=entry["name"],
                waypoints=entry.get("waypoints", []),
                description=entry.get("description", ""),
            )
            for entry in data.get("routes", [])
        }

    def list_routes(self) -> list[PatrolRoute]:
        return list(self._routes.values())

    def get(self, name: str) -> PatrolRoute:
        route = self._routes.get(name)
        if route is None:
            available = ", ".join(sorted(self._routes)) or "(none configured)"
            raise UnknownRouteError(
                f"Unknown patrol route {name!r}. Pre-approved routes: {available}"
            )
        return route
