import httpx
import pytest

from forgejo_mcp_server import server
from forgejo_mcp_server.client import ForgejoClient


def fake_client(handler) -> ForgejoClient:
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(base_url="http://forgejo.local/api/v1", transport=transport)
    return ForgejoClient("http://forgejo.local", "test-token", "zion", http_client=http_client)


def always_ok(json_body):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=json_body)

    return handler


async def test_list_repos_simplifies_shape(monkeypatch):
    monkeypatch.setattr(
        server,
        "_client",
        lambda: fake_client(
            always_ok(
                {
                    "ok": True,
                    "data": [
                        {
                            "name": "tau-kai",
                            "full_name": "zion/tau-kai",
                            "description": "Project Tau",
                            "private": True,
                            "default_branch": "main",
                            "updated_at": "2026-09-04T00:00:00Z",
                            "extra_field_ignored": True,
                        }
                    ],
                }
            )
        ),
    )

    repos = await server.list_repos()

    assert repos == [
        {
            "name": "tau-kai",
            "full_name": "zion/tau-kai",
            "description": "Project Tau",
            "private": True,
            "default_branch": "main",
            "updated_at": "2026-09-04T00:00:00Z",
        }
    ]


async def test_list_commits_simplifies_shape(monkeypatch):
    monkeypatch.setattr(
        server,
        "_client",
        lambda: fake_client(
            always_ok(
                [
                    {
                        "sha": "abcdef1234567890",
                        "commit": {
                            "message": "fix bug",
                            "author": {"name": "zion", "date": "2026-09-04T00:00:00Z"},
                        },
                    }
                ]
            )
        ),
    )

    commits = await server.list_commits("tau-kai")

    assert commits == [
        {"sha": "abcdef123456", "message": "fix bug", "author": "zion", "date": "2026-09-04T00:00:00Z"}
    ]


async def test_list_issues_simplifies_shape(monkeypatch):
    monkeypatch.setattr(
        server,
        "_client",
        lambda: fake_client(
            always_ok(
                [{"number": 1, "title": "bug", "state": "open", "created_at": "2026-09-04T00:00:00Z", "extra": True}]
            )
        ),
    )

    issues = await server.list_issues("tau-kai")

    assert issues == [{"number": 1, "title": "bug", "state": "open", "created_at": "2026-09-04T00:00:00Z"}]


async def test_create_issue_simplifies_shape(monkeypatch):
    monkeypatch.setattr(
        server,
        "_client",
        lambda: fake_client(always_ok({"number": 3, "title": "new issue", "html_url": "http://x", "extra": True})),
    )

    result = await server.create_issue("tau-kai", "new issue")

    assert result == {"number": 3, "title": "new issue", "html_url": "http://x"}


async def test_client_missing_env_raises_clear_error(monkeypatch):
    monkeypatch.delenv("FORGEJO_URL", raising=False)
    monkeypatch.delenv("FORGEJO_TOKEN", raising=False)

    with pytest.raises(RuntimeError, match="FORGEJO_URL and FORGEJO_TOKEN"):
        server._client()
