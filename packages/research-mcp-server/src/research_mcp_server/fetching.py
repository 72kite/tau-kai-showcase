"""Fetching a web page and reducing it to readable text."""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser

import httpx

from research_mcp_server.safety import (
    DEFAULT_MAX_BYTES,
    DEFAULT_TIMEOUT_SECONDS,
    MAX_REDIRECTS,
    UnsafeURLError,
    validate_url,
)

# Content that is not text is not research material, and Tau's model is text-only. Fetching a
# 2GB video to throw it away is pure cost.
_TEXTUAL_CONTENT_TYPES = ("text/", "application/json", "application/xml", "application/xhtml")

_DROP_TAGS = {"script", "style", "noscript", "svg", "canvas", "iframe", "head"}
_BLOCK_TAGS = {
    "p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
    "section", "article", "header", "footer", "blockquote", "pre",
}


class _TextExtractor(HTMLParser):
    """Pulls visible text out of HTML.

    Uses the stdlib parser rather than adding BeautifulSoup/readability: this server's whole job
    is to turn a page into a paragraph or two of text for a local model, and a dependency that
    ships a C parser is a poor trade for that. It is not a full readability implementation - it
    keeps nav/boilerplate a reader-mode extractor would drop, which costs some prompt tokens and
    loses nothing that matters.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._skip_depth = 0
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in _DROP_TAGS:
            self._skip_depth += 1
        elif tag == "title":
            self._in_title = True
        elif tag in _BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_endtag(self, tag):
        if tag in _DROP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag == "title":
            self._in_title = False
        elif tag in _BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data.strip()
            return
        # `head` is in _DROP_TAGS, and <title> lives inside it, so the title branch must come
        # first or every page would come back untitled.
        if self._skip_depth:
            return
        if data.strip():
            self._chunks.append(data)

    def text(self) -> str:
        joined = "".join(self._chunks)
        joined = re.sub(r"[ \t\r\f\v]+", " ", joined)
        joined = re.sub(r"\n\s*\n\s*", "\n\n", joined)
        return joined.strip()


def extract_text(html: str) -> tuple[str, str]:
    """(title, visible text) for an HTML document. Never raises on malformed markup - the parser
    is lenient by design, and a page that fails to parse cleanly is still worth what text it has.
    """
    parser = _TextExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001 - broken HTML is normal; return what we got
        pass
    return parser.title.strip(), parser.text()


@dataclass(frozen=True)
class FetchedPage:
    url: str
    final_url: str
    title: str
    text: str
    truncated: bool


async def fetch_page(
    url: str,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    client: httpx.AsyncClient | None = None,
) -> FetchedPage:
    """Fetches one page and returns its readable text.

    Redirects are followed MANUALLY, revalidating each hop. httpx's `follow_redirects=True` would
    validate only the URL we passed in, so any page could 302 us to http://192.168.1.1/ and walk
    straight past the SSRF check - the check has to run on whatever we are actually about to
    fetch, every time.
    """
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=timeout, follow_redirects=False)
    try:
        current = validate_url(url)
        for _hop in range(MAX_REDIRECTS + 1):
            response = await client.get(
                current,
                headers={
                    # Identify honestly. A server that would rather not be scraped deserves to
                    # be able to say so, and pretending to be a browser to get around that is
                    # not a thing this project should do quietly.
                    "User-Agent": "TauResearchBot/0.1 (+self-hosted home AI; respects robots)",
                    "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.1",
                },
                follow_redirects=False,
            )
            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    raise UnsafeURLError(f"'{current}' redirected without a target")
                current = validate_url(str(response.url.join(location)))
                continue

            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            if content_type and not any(t in content_type for t in _TEXTUAL_CONTENT_TYPES):
                raise UnsafeURLError(
                    f"Refusing '{current}': content-type '{content_type.split(';')[0]}' is not text."
                )

            raw = response.content[:max_bytes]
            truncated = len(response.content) > max_bytes
            body = raw.decode(response.encoding or "utf-8", errors="replace")
            if "html" in content_type or body.lstrip()[:1] == "<":
                title, text = extract_text(body)
            else:
                title, text = "", body.strip()
            return FetchedPage(
                url=url, final_url=str(response.url), title=title, text=text, truncated=truncated
            )
        raise UnsafeURLError(f"'{url}' exceeded {MAX_REDIRECTS} redirects")
    finally:
        if owns_client:
            await client.aclose()
