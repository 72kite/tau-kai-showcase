"""Forgejo MCP server (Phase 31 of project-tau-plan.md - gives Tau tool access to the private git
hosting stood up in Phase 28).

Run directly for manual testing (requires a real Forgejo instance - see ../.env.example):
    python -m forgejo_mcp_server.server
"""

from __future__ import annotations

import os
from typing import Any

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

from forgejo_mcp_server.client import ForgejoClient

load_dotenv()

mcp = FastMCP(
    "forgejo-mcp-server",
    host=os.getenv("FASTMCP_HOST", "127.0.0.1"),
    port=int(os.getenv("FASTMCP_PORT", "8000")),
)


def _client() -> ForgejoClient:
    """Reads FORGEJO_URL/FORGEJO_TOKEN lazily, per call, rather than at process startup - so this
    server can start and list its tools even before real Forgejo credentials exist (same reasoning
    as home-assistant-mcp-server's _client()).
    """
    base_url = os.environ.get("FORGEJO_URL")
    token = os.environ.get("FORGEJO_TOKEN")
    owner = os.environ.get("FORGEJO_OWNER", "zion")
    if not base_url or not token:
        raise RuntimeError("FORGEJO_URL and FORGEJO_TOKEN must be set (see .env.example) to reach Forgejo")
    return ForgejoClient(base_url, token, owner)


@mcp.tool()
async def list_repos() -> list[dict[str, Any]]:
    """List every repository hosted on Tau's private Forgejo instance (git.example.lan), with its
    description, whether it's private, and when it was last updated.

    Answers "what repos do I have", "what's on git.example.lan", "list my projects".
    """
    client = _client()
    try:
        repos = await client.list_repos()
    finally:
        await client.aclose()
    return [
        {
            "name": r["name"],
            "full_name": r["full_name"],
            "description": r.get("description", ""),
            "private": r["private"],
            "default_branch": r.get("default_branch"),
            "updated_at": r.get("updated_at"),
        }
        for r in repos
    ]


@mcp.tool()
async def get_repo(repo_name: str) -> dict[str, Any]:
    """Get details of one repository on Forgejo: description, default branch, size, open issue
    count, last updated. Use list_repos first if you don't already know the exact repo name.
    """
    client = _client()
    try:
        return await client.get_repo(repo_name)
    finally:
        await client.aclose()


@mcp.tool()
async def list_commits(repo_name: str, limit: int = 10) -> list[dict[str, Any]]:
    """List the most recent commits on a repository's default branch. Answers "what's the latest
    on tau-kai", "show me recent commits", "what changed recently".
    """
    client = _client()
    try:
        commits = await client.list_commits(repo_name, limit)
    finally:
        await client.aclose()
    return [
        {
            "sha": c["sha"][:12],
            "message": c["commit"]["message"],
            "author": c["commit"]["author"]["name"],
            "date": c["commit"]["author"]["date"],
        }
        for c in commits
    ]


@mcp.tool()
async def list_issues(repo_name: str, state: str = "open") -> list[dict[str, Any]]:
    """List issues on a repository. `state` is "open", "closed", or "all"."""
    client = _client()
    try:
        issues = await client.list_issues(repo_name, state)
    finally:
        await client.aclose()
    return [
        {"number": i["number"], "title": i["title"], "state": i["state"], "created_at": i["created_at"]}
        for i in issues
    ]


@mcp.tool()
async def create_issue(repo_name: str, title: str, body: str = "") -> dict[str, Any]:
    """File a new issue on a repository. Writes real state on Forgejo - gated by Tau's Core
    Directive Guard and requires human approval before it actually runs.
    """
    client = _client()
    try:
        result = await client.create_issue(repo_name, title, body)
    finally:
        await client.aclose()
    return {"number": result["number"], "title": result["title"], "html_url": result["html_url"]}


if __name__ == "__main__":
    mcp.run(transport=os.getenv("MCP_TRANSPORT", "stdio"))
