import json

import httpx
import pytest

from forgejo_mcp_server.client import ForgejoClient


def make_client(handler) -> ForgejoClient:
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(
        base_url="http://forgejo.local/api/v1",
        transport=transport,
        headers={"Authorization": "token test-token"},
    )
    return ForgejoClient("http://forgejo.local", "test-token", "zion", http_client=http_client)


async def test_list_repos_hits_search_endpoint_with_auth_header():
    def handler(request: httpx.Request) -> httpx.Response:
        # /repos/search, not /user/repos - the latter 403s with only read:repository scope
        # (found live against the real deployment, see client.py's comment).
        assert request.url.path == "/api/v1/repos/search"
        assert request.headers["authorization"] == "token test-token"
        return httpx.Response(200, json={"ok": True, "data": [{"name": "tau-kai", "full_name": "zion/tau-kai"}]})

    client = make_client(handler)
    repos = await client.list_repos()
    assert repos == [{"name": "tau-kai", "full_name": "zion/tau-kai"}]


async def test_get_repo_hits_owner_scoped_endpoint():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/repos/zion/tau-kai"
        return httpx.Response(200, json={"name": "tau-kai", "private": True})

    client = make_client(handler)
    repo = await client.get_repo("tau-kai")
    assert repo["name"] == "tau-kai"


async def test_list_commits_passes_limit():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/repos/zion/tau-kai/commits"
        assert request.url.params["limit"] == "5"
        return httpx.Response(200, json=[{"sha": "abc123"}])

    client = make_client(handler)
    commits = await client.list_commits("tau-kai", limit=5)
    assert commits == [{"sha": "abc123"}]


async def test_list_issues_passes_state():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/repos/zion/tau-kai/issues"
        assert request.url.params["state"] == "open"
        return httpx.Response(200, json=[{"number": 1, "title": "bug"}])

    client = make_client(handler)
    issues = await client.list_issues("tau-kai")
    assert issues == [{"number": 1, "title": "bug"}]


async def test_create_issue_posts_title_and_body():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/repos/zion/tau-kai/issues"
        assert request.method == "POST"
        body = json.loads(request.content)
        assert body == {"title": "found a bug", "body": "details here"}
        return httpx.Response(201, json={"number": 2, "title": "found a bug", "html_url": "http://x"})

    client = make_client(handler)
    result = await client.create_issue("tau-kai", "found a bug", "details here")
    assert result["number"] == 2


async def test_error_status_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "invalid token"})

    client = make_client(handler)
    with pytest.raises(httpx.HTTPStatusError):
        await client.list_repos()
