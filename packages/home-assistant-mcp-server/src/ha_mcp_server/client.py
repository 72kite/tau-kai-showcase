from __future__ import annotations

from typing import Any

import httpx


class HomeAssistantClient:
    """Thin async wrapper around Home Assistant's REST API. Has no knowledge of MCP, env vars,
    or the CDG - server.py owns all of that; this class only knows how to talk to HA.
    """

    def __init__(self, base_url: str, token: str, http_client: httpx.AsyncClient | None = None):
        self._client = http_client or httpx.AsyncClient(
            base_url=f"{base_url.rstrip('/')}/api",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            timeout=10.0,
        )

    async def list_states(self) -> list[dict[str, Any]]:
        response = await self._client.get("/states")
        response.raise_for_status()
        return response.json()

    async def get_state(self, entity_id: str) -> dict[str, Any]:
        response = await self._client.get(f"/states/{entity_id}")
        response.raise_for_status()
        return response.json()

    async def call_service(
        self, domain: str, service: str, entity_id: str, data: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        payload = {"entity_id": entity_id, **(data or {})}
        response = await self._client.post(f"/services/{domain}/{service}", json=payload)
        response.raise_for_status()
        return response.json()

    async def handle_intent(self, name: str, data: dict[str, Any]) -> dict[str, Any]:
        """POST /api/intent/handle - HA's own Assist intent system, which resolves a device/area
        NAME (e.g. "kitchen light") to the right entity_id internally (matching entity/area/alias
        names, the same resolution the voice Assist pipeline uses), rather than requiring the
        caller to already know one. Requires HA's `intent:` integration enabled in
        configuration.yaml - see this package's README.
        """
        response = await self._client.post("/intent/handle", json={"name": name, "data": data})
        response.raise_for_status()
        return response.json()

    async def aclose(self) -> None:
        await self._client.aclose()
