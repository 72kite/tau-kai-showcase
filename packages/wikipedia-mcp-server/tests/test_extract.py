"""HTML-to-text extraction, including the class-based dropping this package adds on top of
research-mcp-server's original tag-only extractor."""

from pathlib import Path

from wikipedia_mcp_server.extract import extract_text

_FIXTURE = Path(__file__).parent / "fixtures" / "ray_charles_raw.html"


def test_extract_text_drops_scripts_and_keeps_prose():
    title, text = extract_text(
        "<html><head><title>Kettle Docs</title><style>p{color:red}</style></head>"
        "<body><script>steal()</script><h1>Descaling</h1><p>Use citric acid.</p></body></html>"
    )
    assert title == "Kettle Docs"
    assert "Descaling" in text and "Use citric acid." in text
    assert "steal()" not in text
    assert "color:red" not in text


def test_extract_text_survives_broken_html():
    title, text = extract_text("<p>unclosed <b>bold <div>nested")
    assert "unclosed" in text


def test_extract_text_drops_class_tagged_chrome():
    """The gap research's original extractor doesn't cover: real Wikipedia/MediaWiki markup
    tags navigation/reference/sister-project chrome by class, not by a dedicated tag name."""
    html = (
        "<html><body>"
        '<p>Real article text about kettles.</p>'
        '<div class="navbox">Related topics: Teapots | Stoves</div>'
        '<div class="side-box side-box-right sistersitebox">Wikiquote has quotations related to Kettles.</div>'
        '<div class="hatnote">This article is about the kitchen appliance.</div>'
        '<div class="mw-references-wrap"><ol><li>Citation one</li></ol></div>'
        "</body></html>"
    )
    _, text = extract_text(html)
    assert "Real article text about kettles." in text
    assert "Teapots" not in text
    assert "Wikiquote" not in text
    assert "kitchen appliance" not in text
    assert "Citation one" not in text


def test_void_elements_do_not_desync_the_drop_stack():
    """Regression case: void elements (br, img, ...) get a handle_starttag call but no matching
    handle_endtag. A drop-tracker that pushes/pops a stack entry per start/end tag pair without
    special-casing void elements desyncs after the first one, silently corrupting drop state for
    every sibling that follows - exactly the shape of bug that would pass a quick smoke test and
    only show up on longer real pages."""
    html = (
        "<html><body>"
        '<div class="navbox">dropped nav text<br>with an img<img src="x.png">here</div>'
        "<p>real text after the navbox</p>"
        "</body></html>"
    )
    _, text = extract_text(html)
    assert "dropped nav text" not in text
    assert "real text after the navbox" in text


def test_extract_text_on_real_kiwix_article_fixture():
    """Captured live from a real kiwix-serve /raw/.../content/Ray_Charles response
    (kiwix-tools 3.8.2, 2026-08-10) - see kiwix.py's module docstring for how it was obtained."""
    html = _FIXTURE.read_text(encoding="utf-8")
    title, text = extract_text(html)
    assert title == "Ray Charles"
    # Real prose survives.
    assert "Ray Charles Robinson" in text
    assert "Albany, Georgia" in text
    # Sister-project box, dropped by class.
    assert "Wikiquote has quotations" not in text
    # No raw markup/script/style leaked through.
    assert "<div" not in text
    assert "@media" not in text
