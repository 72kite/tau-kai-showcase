"""Web search, via a self-hosted SearxNG.

**One backend, on purpose.** The first draft of this module had a "zero-setup" DuckDuckGo HTML
fallback so `search_web` would do something on a fresh clone. Pointing it at the real internet
killed it: DDG answers a non-browser client with `HTTP 202` and a challenge page - zero results,
every time. The only way to make it work is to send a browser User-Agent, i.e. to lie about who
we are to get past a service that is explicitly declining bot traffic. A 202 is DuckDuckGo
saying no, and this project should not quietly work around that.

So the fallback is gone rather than faked, and SearxNG - which the compose stack now runs
alongside Ollama, Whisper and Piper - is the backend. That is the answer this project should
have reached first anyway: search queries are a record of what a household is thinking about, and
every other component here is self-hosted for exactly that reason. Shipping a scraper that leaks
every query to a third party *and* is blocked was the worst of both.

Set SEARXNG_URL. In the compose stack it is set for you (http://searxng:8080).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import httpx

from research_mcp_server.safety import DEFAULT_TIMEOUT_SECONDS

DEFAULT_MAX_RESULTS = 5


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str

    def to_dict(self) -> dict:
        return {"title": self.title, "url": self.url, "snippet": self.snippet}


@dataclass(frozen=True)
class ImageResult:
    title: str
    image_url: str
    source_url: str
    source: str

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "image_url": self.image_url,
            "source_url": self.source_url,
            "source": self.source,
        }


class SearchError(RuntimeError):
    pass


def searxng_url() -> str | None:
    raw = os.getenv("SEARXNG_URL", "").strip()
    return raw.rstrip("/") or None


def parse_searxng_results(payload: dict, max_results: int) -> list[SearchResult]:
    """Pure, so the result-shaping is testable without a SearxNG instance."""
    results = [
        SearchResult(
            title=(item.get("title") or "").strip(),
            url=(item.get("url") or "").strip(),
            snippet=(item.get("content") or "").strip(),
        )
        for item in payload.get("results", [])
        if isinstance(item, dict)
    ]
    return [r for r in results if r.url][:max_results]


def parse_searxng_image_results(payload: dict, max_results: int) -> list[ImageResult]:
    """Pure, same reasoning as parse_searxng_results. SearxNG's image-category results carry
    `img_src` (the actual image bytes) separately from `url` (the page it was found on) - a
    result missing img_src is useless for this tool's purpose (showing a picture), even if the
    page URL is fine, so it's filtered out rather than falling back to the page URL."""
    results = [
        ImageResult(
            title=(item.get("title") or "").strip(),
            image_url=(item.get("img_src") or "").strip(),
            source_url=(item.get("url") or "").strip(),
            source=(item.get("source") or item.get("engine") or "").strip(),
        )
        for item in payload.get("results", [])
        if isinstance(item, dict)
    ]
    return [r for r in results if r.image_url][:max_results]


async def _query_searxng(
    query: str,
    *,
    categories: str | None,
    client: httpx.AsyncClient | None,
    timeout: float,
) -> tuple[dict, str]:
    """Shared request/error-handling for search_web and search_images - both hit the same
    SearxNG `/search` endpoint, differing only in the `categories` param and how the response is
    shaped afterward. Returns (raw JSON payload, base URL) so each caller does its own parsing.
    Raises SearchError on any backend problem."""
    query = (query or "").strip()
    if not query:
        raise SearchError("No query given")

    base = searxng_url()
    if not base:
        raise SearchError(
            "No search backend configured: SEARXNG_URL is unset. The compose stack runs SearxNG "
            "and sets this automatically (docker compose up searxng); for local dev, point "
            "SEARXNG_URL at any SearxNG instance. There is deliberately no scraping fallback - "
            "see this module's docstring."
        )

    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=timeout, follow_redirects=True)
    try:
        params = {"q": query, "format": "json"}
        if categories:
            params["categories"] = categories
        response = await client.get(
            f"{base}/search", params=params, headers={"Accept": "application/json"}
        )
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError as exc:
            # SearxNG ships with the JSON API disabled by default. Without this, the operator
            # gets a JSONDecodeError traceback instead of the one sentence that fixes it.
            raise SearchError(
                f"SearxNG at {base} did not return JSON. Enable it in settings.yml: "
                "search.formats must include 'json'."
            ) from exc
        return payload, base
    except httpx.HTTPError as exc:
        raise SearchError(f"SearxNG at {base} unreachable: {type(exc).__name__}: {exc}") from exc
    finally:
        if owns_client:
            await client.aclose()


async def search_web(
    query: str,
    max_results: int = DEFAULT_MAX_RESULTS,
    *,
    client: httpx.AsyncClient | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> tuple[list[SearchResult], str]:
    """Returns (results, backend_name). Raises SearchError on any backend problem."""
    payload, base = await _query_searxng(query, categories=None, client=client, timeout=timeout)
    return parse_searxng_results(payload, max_results), f"searxng ({base})"


async def search_images(
    query: str,
    max_results: int = DEFAULT_MAX_RESULTS,
    *,
    client: httpx.AsyncClient | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> tuple[list[ImageResult], str]:
    """Returns (results, backend_name). Raises SearchError on any backend problem. Needs the
    `duckduckgo images`/`bing images` engines enabled in SearxNG's settings.yml - the general
    `duckduckgo`/`google` engines search_web uses do not return image-category results."""
    payload, base = await _query_searxng(
        query, categories="images", client=client, timeout=timeout
    )
    return parse_searxng_image_results(payload, max_results), f"searxng ({base})"
