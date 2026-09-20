from __future__ import annotations

from typing import Any

import httpx


class ForgejoClient:
    """Thin async wrapper around Forgejo's REST API (Gitea-compatible - Forgejo is a fork, see
    Phase 28, project-tau-plan.md). Has no knowledge of MCP, env vars, or the CDG - server.py owns
    all of that; this class only knows how to talk to Forgejo.
    """

    def __init__(self, base_url: str, token: str, owner: str, http_client: httpx.AsyncClient | None = None):
        self.owner = owner
        self._client = http_client or httpx.AsyncClient(
            base_url=f"{base_url.rstrip('/')}/api/v1",
            headers={"Authorization": f"token {token}"},
            timeout=10.0,
        )

    async def list_repos(self) -> list[dict[str, Any]]:
        # /user/repos requires a broader token scope (effectively read:user, not just
        # read:repository) and 403s otherwise - found live, not from docs. /repos/search with no
        # query returns the same content for a single-user instance and only needs
        # read:repository, matching this server's actual token scope.
        response = await self._client.get("/repos/search", params={"limit": 50})
        response.raise_for_status()
        return response.json()["data"]

    async def get_repo(self, repo_name: str) -> dict[str, Any]:
        response = await self._client.get(f"/repos/{self.owner}/{repo_name}")
        response.raise_for_status()
        return response.json()

    async def list_commits(self, repo_name: str, limit: int = 10) -> list[dict[str, Any]]:
        response = await self._client.get(
            f"/repos/{self.owner}/{repo_name}/commits", params={"limit": limit}
        )
        response.raise_for_status()
        return response.json()

    async def list_issues(self, repo_name: str, state: str = "open") -> list[dict[str, Any]]:
        response = await self._client.get(
            f"/repos/{self.owner}/{repo_name}/issues", params={"state": state, "type": "issues"}
        )
        response.raise_for_status()
        return response.json()

    async def create_issue(self, repo_name: str, title: str, body: str = "") -> dict[str, Any]:
        response = await self._client.post(
            f"/repos/{self.owner}/{repo_name}/issues", json={"title": title, "body": body}
        )
        response.raise_for_status()
        return response.json()

    async def aclose(self) -> None:
        await self._client.aclose()
