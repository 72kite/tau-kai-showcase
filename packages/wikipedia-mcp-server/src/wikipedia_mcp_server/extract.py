"""Turns a raw Kiwix/Wikipedia article page into plain readable text.

Vendored and adapted from research_mcp_server.fetching's `_TextExtractor` rather than imported -
every domain server in this repo is self-contained by convention (see that package's README), so
this is a deliberate copy, not a shared dependency.

Extended with class-based dropping. Research's original only drops by tag name, which is enough
for a generic web page, but Kiwix's raw Wikipedia article HTML (modern MediaWiki Vector skin) is
full of class-tagged chrome a tag-only extractor lets straight through - infoboxes' caption
blocks, navboxes, reference-list wrappers, "see also on our sister sites" boxes, hatnotes,
edit-section links, and Kiwix's own footer. Confirmed live against a real article
(Ray_Charles, kiwix-tools 3.8.2, 2026-08-10): without class-based dropping, all of that came
through as prose ahead of - and mixed into - the real article text.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

_DROP_TAGS = {"script", "style", "noscript", "svg", "canvas", "iframe", "head", "table"}
_BLOCK_TAGS = {
    "p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
    "section", "article", "header", "footer", "blockquote", "pre",
}
# HTML elements with no closing tag - HTMLParser never calls handle_endtag for these (even when
# self-closed XML-style, e.g. "<br/>", the default handle_startendtag calls both handler methods,
# so a naive stack push in handle_starttag would never see a matching pop). Handled as a special
# case in both handlers below rather than through the drop stack.
_VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}
# Substrings matched against a start tag's class attribute - any match drops that element and
# everything inside it. Substring, not exact match: real markup composes several classes on one
# element (e.g. class="side-box side-box-right sistersitebox"), and the interesting one is rarely
# first or alone.
_DROP_CLASS_SUBSTRINGS = (
    "navbox", "side-box", "sistersitebox", "mw-references-wrap", "hatnote",
    "mw-editsection", "zim-footer", "infobox-caption", "vector-header",
    "vector-page-toolbar", "printfooter", "catlinks", "mw-jump-link",
)


class _TextExtractor(HTMLParser):
    """Pulls visible article prose out of raw Kiwix/Wikipedia HTML.

    Uses the stdlib parser rather than adding BeautifulSoup/readability, matching research's
    stated rationale: turning a page into text for a local model is not worth a dependency that
    ships a C parser. Not a full readability implementation - see module docstring for what it
    does drop, and expect further class-substring tuning as more article shapes are seen.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._skip_depth = 0
        self._skip_stack: list[bool] = []
        self.title = ""
        self._in_title = False

    def _class_should_drop(self, attrs) -> bool:
        class_attr = next((v for k, v in attrs if k == "class" and v), "") or ""
        return any(needle in class_attr for needle in _DROP_CLASS_SUBSTRINGS)

    def handle_starttag(self, tag, attrs):
        if tag in _VOID_TAGS:
            if tag == "br" and not self._skip_depth:
                self._chunks.append("\n")
            return
        drop = tag in _DROP_TAGS or self._class_should_drop(attrs)
        self._skip_stack.append(drop)
        if drop:
            self._skip_depth += 1
        elif tag == "title":
            self._in_title = True
        elif tag in _BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_endtag(self, tag):
        if tag in _VOID_TAGS:
            return
        drop = self._skip_stack.pop() if self._skip_stack else False
        if drop:
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
    """(title, visible article text) for a Kiwix/Wikipedia article page. Never raises on
    malformed markup - the parser is lenient by design, and a page that fails to parse cleanly is
    still worth what text it has.
    """
    parser = _TextExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001 - broken HTML is normal; return what we got
        pass
    return parser.title.strip(), parser.text()
