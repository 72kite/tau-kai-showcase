"""Home Assistant MCP server (Phase 2 of project-tau-plan.md, Section 5.2).

Run directly for manual testing (requires a real HA instance - see ../.env.example):
    python -m ha_mcp_server.server
"""

from __future__ import annotations

import os
from typing import Any

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

from ha_mcp_server.client import HomeAssistantClient

load_dotenv()

SECURITY_SENSITIVE_DOMAINS = {"lock", "alarm_control_panel", "cover"}

# Domains turn_on/turn_off (below) are allowed to target. Explicit allowlist, not "anything not
# security-sensitive" - HA's own HassTurnOn/HassTurnOff intents cover the `cover` domain by
# default (HassOpenCover/HassCloseCover are deprecated in favor of them, per HA's own developer
# docs), so an unrestricted or optional domain filter here would silently reopen the
# call_security_service-only approval gate on covers/garage doors through a second, ungated tool.
# Reviewed the same way SECURITY_SENSITIVE_DOMAINS itself is - add to this list deliberately, not
# by widening a pattern.
INTENT_ALLOWED_DOMAINS = {"light", "switch", "fan", "media_player", "humidifier", "valve"}


def _intent_data(**slots: Any) -> dict[str, Any]:
    """HA's /api/intent/handle wants only the slots actually being used - drop the Nones rather
    than sending e.g. {"name": null}."""
    return {key: value for key, value in slots.items() if value is not None}

mcp = FastMCP(
    "home-assistant",
    host=os.getenv("FASTMCP_HOST", "127.0.0.1"),
    port=int(os.getenv("FASTMCP_PORT", "8000")),
)


def _client() -> HomeAssistantClient:
    """Reads HA_URL/HA_TOKEN lazily, per call, rather than at process startup - so this server
    can start and list its tools even before real HA credentials exist.
    """
    base_url = os.environ.get("HA_URL")
    token = os.environ.get("HA_TOKEN")
    if not base_url or not token:
        raise RuntimeError("HA_URL and HA_TOKEN must be set (see .env.example) to reach Home Assistant")
    return HomeAssistantClient(base_url, token)


@mcp.tool()
async def list_devices() -> list[dict[str, Any]]:
    """List all Home Assistant entities with their current state."""
    client = _client()
    try:
        states = await client.list_states()
    finally:
        await client.aclose()
    return [
        {
            "entity_id": state["entity_id"],
            "state": state["state"],
            "friendly_name": state.get("attributes", {}).get("friendly_name"),
        }
        for state in states
    ]


@mcp.tool()
async def get_entity_state(entity_id: str) -> dict[str, Any]:
    """Get the current state and attributes of one Home Assistant entity."""
    client = _client()
    try:
        return await client.get_state(entity_id)
    finally:
        await client.aclose()


@mcp.tool()
async def turn_on(
    name: str | None = None, area: str | None = None, domain: str = "light"
) -> dict[str, Any]:
    """Turn on a device or all matching devices in an area - "turn on the kitchen light", "turn
    on the lounge lamps", "turn everything on in the bedroom". Tau does NOT need to look up an
    entity_id first: Home Assistant's own Assist system resolves the name/area itself (matching
    entity, area, and alias names the same way its voice pipeline does).

    Args:
        name: Device or entity name, e.g. "kitchen light". Omit to target a whole area/domain.
        area: Area/room name, e.g. "kitchen". Omit to match by name only.
        domain: Which kind of device - one of: light, switch, fan, media_player, humidifier,
            valve. Defaults to "light" (the common case). Locks, alarms, and covers/garage doors
            are NOT allowed here - use call_security_service for those.
    """
    if domain not in INTENT_ALLOWED_DOMAINS:
        raise ValueError(
            f"domain '{domain}' is not allowed here (allowed: {sorted(INTENT_ALLOWED_DOMAINS)}); "
            "locks/alarms/covers go through call_security_service instead"
        )
    client = _client()
    try:
        return await client.handle_intent("HassTurnOn", _intent_data(name=name, area=area, domain=domain))
    finally:
        await client.aclose()


@mcp.tool()
async def turn_off(
    name: str | None = None, area: str | None = None, domain: str = "light"
) -> dict[str, Any]:
    """Turn off a device or all matching devices in an area - "turn off the porch light", "turn
    off the TV", "turn everything off in the bedroom". Same name/area resolution as turn_on - no
    entity_id lookup needed first.

    Args:
        name: Device or entity name, e.g. "porch light". Omit to target a whole area/domain.
        area: Area/room name, e.g. "bedroom". Omit to match by name only.
        domain: Which kind of device - one of: light, switch, fan, media_player, humidifier,
            valve. Defaults to "light" (the common case). Locks, alarms, and covers/garage doors
            are NOT allowed here - use call_security_service for those.
    """
    if domain not in INTENT_ALLOWED_DOMAINS:
        raise ValueError(
            f"domain '{domain}' is not allowed here (allowed: {sorted(INTENT_ALLOWED_DOMAINS)}); "
            "locks/alarms/covers go through call_security_service instead"
        )
    client = _client()
    try:
        return await client.handle_intent("HassTurnOff", _intent_data(name=name, area=area, domain=domain))
    finally:
        await client.aclose()


@mcp.tool()
async def set_light_state(
    name: str | None = None,
    area: str | None = None,
    brightness: int | None = None,
    color: str | None = None,
    temperature: int | None = None,
) -> dict[str, Any]:
    """Dim, brighten, or change the color of a light - "dim the lounge lamps to 30 percent", "set
    the kitchen light to blue", "make the bedroom light warmer". Only ever targets lights (HA's
    own HassLightSet intent), so there is no domain to specify and no lock/alarm/cover exposure.

    Args:
        name: Light name, e.g. "lounge lamp". Omit to target a whole area.
        area: Area/room name, e.g. "lounge". Omit to match by name only.
        brightness: 0-100 percent.
        color: A color name, e.g. "blue", "warm white".
        temperature: Color temperature in Kelvin (1000-10000) - lower is warmer/more orange.
    """
    if brightness is None and color is None and temperature is None:
        raise ValueError("give at least one of brightness, color, or temperature to change")
    client = _client()
    try:
        return await client.handle_intent(
            "HassLightSet",
            _intent_data(name=name, area=area, brightness=brightness, color=color, temperature=temperature),
        )
    finally:
        await client.aclose()


@mcp.tool()
async def set_climate_temperature(
    temperature: float, name: str | None = None, area: str | None = None
) -> dict[str, Any]:
    """Set a thermostat's target temperature - "set the bedroom thermostat to 20 degrees", "make
    it warmer in the lounge". Only ever targets climate devices (HA's own
    HassClimateSetTemperature intent) - no entity_id lookup needed first.

    Args:
        temperature: Target temperature, in whatever unit Home Assistant is configured for.
        name: Thermostat name. Omit to target a whole area.
        area: Area/room name, e.g. "bedroom". Omit to match by name only.
    """
    client = _client()
    try:
        return await client.handle_intent(
            "HassClimateSetTemperature", _intent_data(name=name, area=area, temperature=temperature)
        )
    finally:
        await client.aclose()


@mcp.tool()
async def call_service(
    domain: str, service: str, entity_id: str, data: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """Less-common smart-home actions that turn_on/turn_off/set_light_state/
    set_climate_temperature don't cover - media playback control (play/pause/skip), fan speed
    presets, or any domain besides light/switch/fan/media_player/humidifier/valve/climate. For
    "turn on/off X" or "dim/set the temperature of X", use those tools instead - they resolve the
    device by name/area themselves and don't need an entity_id looked up first.

    Locks, alarms, garage doors and covers are NOT here - those are call_security_service, which
    requires human approval through Tau's Core Directive Guard.

    Args:
        domain: Home Assistant domain, e.g. "media_player", "fan".
        service: Service to call, e.g. "media_play", "set_preset_mode".
        entity_id: Which device, e.g. "media_player.living_room". Use list_devices/
            get_entity_state to find it first - unlike turn_on/turn_off, this tool does not
            resolve a name for you.
        data: Optional extra parameters, e.g. {"preset_mode": "sleep"}.
    """
    if domain in SECURITY_SENSITIVE_DOMAINS:
        raise ValueError(f"domain '{domain}' requires call_security_service, not call_service")
    client = _client()
    try:
        return await client.call_service(domain, service, entity_id, data)
    finally:
        await client.aclose()


@mcp.tool()
async def call_security_service(
    domain: str, service: str, entity_id: str, data: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """Call a Home Assistant service for security-relevant domains (locks, alarms,
    covers/garage doors). Gated by Tau's Core Directive Guard - requires human approval before
    it actually runs.
    """
    if domain not in SECURITY_SENSITIVE_DOMAINS:
        raise ValueError(f"domain '{domain}' is not security-sensitive; use call_service instead")
    client = _client()
    try:
        return await client.call_service(domain, service, entity_id, data)
    finally:
        await client.aclose()


if __name__ == "__main__":
    mcp.run(transport=os.getenv("MCP_TRANSPORT", "stdio"))
