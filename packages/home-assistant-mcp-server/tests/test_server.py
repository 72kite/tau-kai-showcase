"""Tests the domain-gating split between call_service and call_security_service - this split is
what lets tau-core's CDG (which only matches on server/tool name, never arguments) gate
lock/alarm/cover actions without any change to the CDG itself. See
tau-core/config/cdg_rules.yaml's home-assistant-security-needs-approval rule.

Also tests turn_on/turn_off/set_light_state/set_climate_temperature (Phase 44) - the
name/area-resolving intent tools added alongside call_service. turn_on/turn_off carry the SAME
domain-gating concern as call_service/call_security_service above: HA's own HassTurnOn/HassTurnOff
intents cover the `cover` domain by default, so INTENT_ALLOWED_DOMAINS is what stops those two
tools from becoming a second, ungated way to open a garage door.
"""

import httpx
import pytest

from ha_mcp_server import server
from ha_mcp_server.client import HomeAssistantClient


def fake_client(handler) -> HomeAssistantClient:
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(base_url="http://ha.local/api", transport=transport)
    return HomeAssistantClient("http://ha.local", "test-token", http_client=http_client)


def always_ok(json_body):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=json_body)

    return handler


async def test_list_devices_simplifies_state_shape(monkeypatch):
    monkeypatch.setattr(
        server,
        "_client",
        lambda: fake_client(
            always_ok(
                [{"entity_id": "light.kitchen", "state": "on", "attributes": {"friendly_name": "Kitchen Light"}}]
            )
        ),
    )

    devices = await server.list_devices()

    assert devices == [{"entity_id": "light.kitchen", "state": "on", "friendly_name": "Kitchen Light"}]


async def test_call_service_allows_routine_domain(monkeypatch):
    monkeypatch.setattr(
        server, "_client", lambda: fake_client(always_ok([{"entity_id": "light.kitchen", "state": "on"}]))
    )

    result = await server.call_service("light", "turn_on", "light.kitchen")

    assert result[0]["state"] == "on"


async def test_call_service_refuses_security_sensitive_domain(monkeypatch):
    monkeypatch.setattr(server, "_client", lambda: fake_client(always_ok([])))

    with pytest.raises(ValueError, match="call_security_service"):
        await server.call_service("lock", "unlock", "lock.front_door")


async def test_call_security_service_allows_security_sensitive_domain(monkeypatch):
    monkeypatch.setattr(
        server, "_client", lambda: fake_client(always_ok([{"entity_id": "lock.front_door", "state": "unlocked"}]))
    )

    result = await server.call_security_service("lock", "unlock", "lock.front_door")

    assert result[0]["state"] == "unlocked"


async def test_call_security_service_refuses_routine_domain(monkeypatch):
    monkeypatch.setattr(server, "_client", lambda: fake_client(always_ok([])))

    with pytest.raises(ValueError, match="not security-sensitive"):
        await server.call_security_service("light", "turn_on", "light.kitchen")


async def test_turn_on_resolves_by_name_via_intent(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"response": {"speech": {}}})

    monkeypatch.setattr(server, "_client", lambda: fake_client(handler))

    await server.turn_on(name="kitchen light")

    assert seen["body"] == {"name": "HassTurnOn", "data": {"name": "kitchen light", "domain": "light"}}


async def test_turn_on_defaults_domain_to_light(monkeypatch):
    monkeypatch.setattr(server, "_client", lambda: fake_client(always_ok({"response": {}})))
    # Just proving the default doesn't raise - the request-shape assertion is above.
    await server.turn_on(area="kitchen")


async def test_turn_on_refuses_a_security_sensitive_domain(monkeypatch):
    monkeypatch.setattr(server, "_client", lambda: fake_client(always_ok({})))

    with pytest.raises(ValueError, match="call_security_service"):
        await server.turn_on(name="garage door", domain="cover")


async def test_turn_off_refuses_a_security_sensitive_domain(monkeypatch):
    monkeypatch.setattr(server, "_client", lambda: fake_client(always_ok({})))

    with pytest.raises(ValueError, match="call_security_service"):
        await server.turn_off(name="front door", domain="lock")


async def test_turn_off_resolves_by_area_via_intent(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"response": {"speech": {}}})

    monkeypatch.setattr(server, "_client", lambda: fake_client(handler))

    await server.turn_off(area="bedroom", domain="switch")

    assert seen["body"] == {"name": "HassTurnOff", "data": {"area": "bedroom", "domain": "switch"}}


async def test_set_light_state_requires_at_least_one_change(monkeypatch):
    monkeypatch.setattr(server, "_client", lambda: fake_client(always_ok({})))

    with pytest.raises(ValueError, match="at least one"):
        await server.set_light_state(name="lounge lamp")


async def test_set_light_state_sends_brightness_via_intent(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"response": {"speech": {}}})

    monkeypatch.setattr(server, "_client", lambda: fake_client(handler))

    await server.set_light_state(area="lounge", brightness=30)

    assert seen["body"] == {"name": "HassLightSet", "data": {"area": "lounge", "brightness": 30}}


async def test_set_climate_temperature_sends_temperature_via_intent(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"response": {"speech": {}}})

    monkeypatch.setattr(server, "_client", lambda: fake_client(handler))

    await server.set_climate_temperature(temperature=20, area="bedroom")

    assert seen["body"] == {"name": "HassClimateSetTemperature", "data": {"area": "bedroom", "temperature": 20}}


async def test_client_missing_env_raises_clear_error(monkeypatch):
    monkeypatch.delenv("HA_URL", raising=False)
    monkeypatch.delenv("HA_TOKEN", raising=False)

    with pytest.raises(RuntimeError, match="HA_URL and HA_TOKEN"):
        server._client()
