"""kiwix-serve client: env config, /suggest parsing, /raw content fetch, and the error paths."""

import json
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from wikipedia_mcp_server.kiwix import (
    KiwixError,
    WikipediaSearchResult,
    get_article_backend,
    kiwix_url,
    parse_suggest_results,
    search_wikipedia_backend,
    zim_name,
)

_FIXTURES = Path(__file__).parent / "fixtures"


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _configured():
    return patch.multiple(
        "wikipedia_mcp_server.kiwix",
        kiwix_url=lambda: "http://kiwix-serve:8080",
        zim_name=lambda: "wikipedia",
    )


# --- env config ---------------------------------------------------------------------------------


def test_kiwix_url_strips_trailing_slash():
    with patch.dict("os.environ", {"KIWIX_URL": "http://kiwix-serve:8080/"}):
        assert kiwix_url() == "http://kiwix-serve:8080"


def test_zim_name_unset_is_none():
    with patch.dict("os.environ", {}, clear=True):
        assert zim_name() is None


# --- /suggest parsing (pure, offline) ------------------------------------------------------------


def test_parse_suggest_results_drops_the_trailing_pattern_entry():
    """Real payload captured live from /suggest?content=test&term=charles&count=5
    (kiwix-tools 3.8.2, 2026-08-10) - see kiwix.py's module docstring."""
    payload = json.loads((_FIXTURES / "suggest_charles.json").read_text(encoding="utf-8"))
    results = parse_suggest_results(payload, max_results=10)
    assert results == [
        WikipediaSearchResult(title="Charles, Ray", path="Charles,_Ray"),
        WikipediaSearchResult(title="Live (Ray Charles)", path="Live_(Ray_Charles)"),
        WikipediaSearchResult(title="Ray Charles (album)", path="Ray_Charles_(album)"),
        WikipediaSearchResult(title="Ray Charles Anthology", path="Ray_Charles_Anthology"),
        WikipediaSearchResult(title="Ray Charles discography", path="Ray_Charles_discography"),
    ]


def test_parse_suggest_results_respects_max_results():
    payload = json.loads((_FIXTURES / "suggest_charles.json").read_text(encoding="utf-8"))
    assert len(parse_suggest_results(payload, max_results=2)) == 2


def test_parse_suggest_results_empty_when_only_the_pattern_entry_matches():
    """Real payload for a nonsense query with zero real matches - /suggest still returns one
    entry (kind: "pattern"), which must not be mistaken for a result."""
    payload = json.loads((_FIXTURES / "suggest_empty.json").read_text(encoding="utf-8"))
    assert parse_suggest_results(payload, max_results=10) == []


# --- search_wikipedia_backend --------------------------------------------------------------------


async def test_search_without_a_backend_says_what_to_do_not_just_fail():
    with patch("wikipedia_mcp_server.kiwix.kiwix_url", return_value=None):
        with pytest.raises(KiwixError, match="KIWIX_URL"):
            await search_wikipedia_backend("anything")


async def test_search_wrong_book_name_names_the_fix():
    """kiwix-serve 404s on an unknown book name (confirmed live) - the book identifier is the
    ZIM's filename on disk, not any internal metadata, which is exactly the footgun this error
    message has to head off."""
    def handler(request):
        return httpx.Response(404, text="No such book")

    with _configured():
        with pytest.raises(KiwixError, match="no book named"):
            await search_wikipedia_backend("charles", client=_client(handler))


async def test_search_returns_parsed_results():
    payload = json.loads((_FIXTURES / "suggest_charles.json").read_text(encoding="utf-8"))

    def handler(request):
        assert request.url.path == "/suggest"
        assert request.url.params["content"] == "wikipedia"
        assert request.url.params["term"] == "charles"
        return httpx.Response(200, json=payload)

    with _configured():
        results = await search_wikipedia_backend("charles", client=_client(handler))
    assert results[0] == WikipediaSearchResult(title="Charles, Ray", path="Charles,_Ray")


async def test_search_backend_unreachable_is_reported_not_raised_raw():
    def handler(request):
        raise httpx.ConnectError("refused")

    with _configured():
        with pytest.raises(KiwixError, match="unreachable"):
            await search_wikipedia_backend("charles", client=_client(handler))


# --- get_article_backend --------------------------------------------------------------------------


async def test_get_article_without_a_backend_says_what_to_do():
    with patch("wikipedia_mcp_server.kiwix.kiwix_url", return_value=None):
        with pytest.raises(KiwixError, match="KIWIX_URL"):
            await get_article_backend("Ray_Charles")


async def test_get_article_returns_html():
    def handler(request):
        assert request.url.path == "/raw/wikipedia/content/Ray_Charles"
        return httpx.Response(200, html="<html><title>Ray Charles</title></html>")

    with _configured():
        html = await get_article_backend("Ray_Charles", client=_client(handler))
    assert html is not None and "Ray Charles" in html


async def test_get_article_not_found_returns_none_not_an_exception():
    """404 is an expected, common outcome (article outside the snapshot's tier) - it should be
    a normal return value the tool layer turns into a helpful message, not an exception."""
    def handler(request):
        return httpx.Response(404)

    with _configured():
        html = await get_article_backend("Some_Article_Not_In_The_Snapshot", client=_client(handler))
    assert html is None


async def test_get_article_path_with_special_characters_is_percent_encoded():
    """A literal '/' in a title (e.g. "AC/DC") must not be allowed to split the URL path.

    Checked via raw_path (the actual bytes sent on the wire), not the decoded .path property -
    httpx's .path getter unescapes %2F back to '/' for display, which would make this test pass
    even if the encoding were silently lost.
    """
    seen = {}

    def handler(request):
        seen["raw_path"] = request.url.raw_path
        return httpx.Response(200, text="ok")

    with _configured():
        await get_article_backend("AC/DC", client=_client(handler))
    assert seen["raw_path"] == b"/raw/wikipedia/content/AC%2FDC"
