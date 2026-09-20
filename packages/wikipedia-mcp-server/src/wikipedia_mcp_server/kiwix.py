"""Offline Wikipedia search and article fetch, via a self-hosted Kiwix server.

**Search backend: /suggest, not /search.** kiwix-serve's /search endpoint (full-text query
through its bundled xapian index) returned a bare "500 Unhandled unknown error" against a real
ZIM in live testing (kiwix-tools 3.8.2 / libkiwix 14.2.0, 2026-08-10), reproducibly, across every
parameter combination tried (with/without books.name, with/without pageLength, with
--searchLimit set) - the route itself works (an empty pattern returns 200), only query execution
crashes. Rather than depend on a broken endpoint, this module uses /suggest instead: it's the
same JSON endpoint kiwix's own search-as-you-type UI uses, already does real full-text-ish
matching (not just title-prefix - confirmed live: a "charles" query matched "Live (Ray Charles)"
and "Ray Charles (album)", neither of which starts with "charles"), and never crashed in testing.
Revisit if a future kiwix-tools release fixes /search.

**Book identifier = the ZIM's filename on disk, not any internal metadata.** Confirmed live: a
ZIM whose internal <name> metadata is "wikipedia_en_ray-charles" was reachable at /suggest and
/raw only under "test" - the filename stem of test.zim, the name it was saved under. See
infra/kiwix/pull-wikipedia-zim.sh: it always saves the downloaded ZIM under one fixed filename
for exactly this reason, so KIWIX_ZIM_NAME never has to change when the ZIM variant is swapped
for a bigger tier later.

Set KIWIX_URL and KIWIX_ZIM_NAME. The compose stack sets both for you.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import quote

import httpx

DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_RESULTS = 5


@dataclass(frozen=True)
class WikipediaSearchResult:
    title: str
    path: str

    def to_dict(self) -> dict:
        return {"title": self.title, "path": self.path}


class KiwixError(RuntimeError):
    pass


def kiwix_url() -> str | None:
    raw = os.getenv("KIWIX_URL", "").strip()
    return raw.rstrip("/") or None


def zim_name() -> str | None:
    raw = os.getenv("KIWIX_ZIM_NAME", "").strip()
    return raw or None


def _backend_unconfigured_error() -> KiwixError:
    return KiwixError(
        "No offline Wikipedia backend configured: KIWIX_URL and/or KIWIX_ZIM_NAME is unset. The "
        "compose stack runs kiwix-serve and sets both automatically once "
        "infra/kiwix/pull-wikipedia-zim.sh has been run - see that script and the package README."
    )


def parse_suggest_results(payload: list, max_results: int) -> list[WikipediaSearchResult]:
    """Pure, so the result-shaping is testable without a live kiwix-serve.

    /suggest always appends one trailing `kind: "pattern"` entry (a "search for this literally"
    placeholder for kiwix's own search-as-you-type UI, not a real match) - filtered out here.
    """
    results = [
        WikipediaSearchResult(
            title=(item.get("value") or "").strip(),
            path=(item.get("path") or "").strip(),
        )
        for item in payload
        if isinstance(item, dict) and item.get("kind") == "path"
    ]
    return [r for r in results if r.title and r.path][:max_results]


async def search_wikipedia_backend(
    query: str,
    max_results: int = DEFAULT_MAX_RESULTS,
    *,
    client: httpx.AsyncClient | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> list[WikipediaSearchResult]:
    query = (query or "").strip()
    if not query:
        raise KiwixError("No query given")

    base = kiwix_url()
    name = zim_name()
    if not base or not name:
        raise _backend_unconfigured_error()

    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=timeout)
    try:
        response = await client.get(
            f"{base}/suggest",
            # +1: kiwix counts its own trailing "pattern" placeholder against this limit, so
            # asking for exactly max_results can return one fewer real match than expected.
            params={"content": name, "term": query, "count": max_results + 1},
        )
        if response.status_code == 404:
            raise KiwixError(
                f"kiwix-serve at {base} has no book named '{name}' loaded. Check `docker compose "
                "logs kiwix-serve` for the actual filename it's serving - the book identifier is "
                "the ZIM's filename on disk, not any internal metadata (see this module's "
                "docstring)."
            )
        response.raise_for_status()
        return parse_suggest_results(response.json(), max_results)
    except httpx.HTTPError as exc:
        raise KiwixError(f"kiwix-serve at {base} unreachable: {type(exc).__name__}: {exc}") from exc
    finally:
        if owns_client:
            await client.aclose()


async def get_article_backend(
    path: str,
    *,
    client: httpx.AsyncClient | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> str | None:
    """Raw article HTML for one path (as returned by search_wikipedia_backend), or None if the
    article - or the configured book itself - isn't found (kiwix-serve 404s identically for
    both cases, so this can't distinguish "bad KIWIX_ZIM_NAME" from "article not in snapshot";
    search_wikipedia_backend's book-name check is the one that surfaces that distinction)."""
    path = (path or "").strip()
    if not path:
        raise KiwixError("No article path given")

    base = kiwix_url()
    name = zim_name()
    if not base or not name:
        raise _backend_unconfigured_error()

    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=timeout)
    try:
        # quote(..., safe=""): `path` is one title token, not a multi-segment path - a literal
        # "/" in a title (e.g. "AC/DC") must be percent-encoded or it would split the URL.
        response = await client.get(f"{base}/raw/{name}/content/{quote(path, safe='')}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.text
    except httpx.HTTPError as exc:
        raise KiwixError(f"kiwix-serve at {base} unreachable: {type(exc).__name__}: {exc}") from exc
    finally:
        if owns_client:
            await client.aclose()
