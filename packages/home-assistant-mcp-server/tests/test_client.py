import httpx
import pytest

from ha_mcp_server.client import HomeAssistantClient


def make_client(handler) -> HomeAssistantClient:
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(
        base_url="http://ha.local/api",
        transport=transport,
        headers={"Authorization": "Bearer test-token"},
    )
    return HomeAssistantClient("http://ha.local", "test-token", http_client=http_client)


async def test_list_states_hits_states_endpoint_with_auth_header():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/states"
        assert request.headers["authorization"] == "Bearer test-token"
        return httpx.Response(200, json=[{"entity_id": "light.kitchen", "state": "on"}])

    client = make_client(handler)
    states = await client.list_states()
    assert states == [{"entity_id": "light.kitchen", "state": "on"}]


async def test_get_state_hits_entity_specific_endpoint():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/states/light.kitchen"
        return httpx.Response(200, json={"entity_id": "light.kitchen", "state": "on", "attributes": {}})

    client = make_client(handler)
    state = await client.get_state("light.kitchen")
    assert state["entity_id"] == "light.kitchen"


async def test_call_service_posts_entity_id_and_extra_data():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/services/light/turn_on"
        assert request.method == "POST"
        import json

        body = json.loads(request.content)
        assert body == {"entity_id": "light.kitchen", "brightness": 128}
        return httpx.Response(200, json=[{"entity_id": "light.kitchen", "state": "on"}])

    client = make_client(handler)
    result = await client.call_service("light", "turn_on", "light.kitchen", {"brightness": 128})
    assert result[0]["state"] == "on"


async def test_handle_intent_posts_name_and_data():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/intent/handle"
        assert request.method == "POST"
        import json

        body = json.loads(request.content)
        assert body == {"name": "HassTurnOn", "data": {"name": "kitchen light", "domain": "light"}}
        return httpx.Response(200, json={"response": {"speech": {"plain": {"speech": "Done"}}}})

    client = make_client(handler)
    result = await client.handle_intent("HassTurnOn", {"name": "kitchen light", "domain": "light"})
    assert result["response"]["speech"]["plain"]["speech"] == "Done"


async def test_error_status_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "invalid auth"})

    client = make_client(handler)
    with pytest.raises(httpx.HTTPStatusError):
        await client.list_states()
