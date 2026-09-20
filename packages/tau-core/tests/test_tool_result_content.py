"""Tool results that are not plain text must not kill the turn.

Every MCP server in this repo returned TextContent, so `content[0].text` in the toolset wrapper
was never exercised against anything else - and it raises AttributeError on an ImageContent block,
which `call()` does not catch, so it propagates through PydanticAI and takes the whole chat turn
down. Found while probing openscad-mcp-server (Phase 25), whose `render_scad_png` returns an
image; it would have been found in production otherwise.
"""

from __future__ import annotations

from mcp.types import BlobResourceContents, EmbeddedResource, ImageContent, TextContent, TextResourceContents

from tau_core.llm.toolset import _content_to_text


def test_text_content_is_returned_verbatim():
    assert _content_to_text([TextContent(type="text", text="all good")]) == "all good"


def test_empty_content_is_described():
    assert _content_to_text([]) == "(tool returned no content)"


def test_every_block_is_read_not_just_the_first():
    """FastMCP returns one block per list item; only reading the first silently dropped the rest
    (the same bug web/server.py fixed for list-returning tools)."""
    blocks = [TextContent(type="text", text=f"item {i}") for i in range(3)]
    assert _content_to_text(blocks) == "item 0\nitem 1\nitem 2"


def test_image_content_does_not_raise_and_is_not_inlined():
    """The crash, and the reason not to simply fix it by inlining `.data`: the local model is
    text-only, so thousands of base64 characters would burn context to convey nothing."""
    payload = "A" * 5000
    out = _content_to_text([ImageContent(type="image", data=payload, mimeType="image/png")])
    assert payload not in out
    assert "image/png" in out
    assert "5000" in out


def test_mixed_text_and_image_keeps_the_text():
    out = _content_to_text(
        [
            TextContent(type="text", text="rendered a 20mm cube"),
            ImageContent(type="image", data="B" * 100, mimeType="image/png"),
        ]
    )
    assert "rendered a 20mm cube" in out
    assert "image/png" in out


def test_small_text_resource_is_shown():
    """An STL is ASCII and a small one is worth reading - e.g. so the model can say how many
    facets it produced."""
    resource = EmbeddedResource(
        type="resource",
        resource=TextResourceContents(uri="file:///cube.stl", mimeType="model/stl", text="solid cube\nendsolid"),
    )
    out = _content_to_text([resource])
    assert "solid cube" in out
    assert "model/stl" in out


def test_large_text_resource_is_described_not_inlined():
    resource = EmbeddedResource(
        type="resource",
        resource=TextResourceContents(uri="file:///big.stl", mimeType="model/stl", text="x" * 50_000),
    )
    out = _content_to_text([resource])
    assert "x" * 50_000 not in out
    assert "file:///big.stl" in out


def test_blob_resource_is_described():
    resource = EmbeddedResource(
        type="resource",
        resource=BlobResourceContents(uri="file:///cube.stl", mimeType="model/stl", blob="Q" * 400),
    )
    out = _content_to_text([resource])
    assert "file:///cube.stl" in out
    assert "Q" * 400 not in out


def test_result_is_bounded():
    """A runaway tool result must not blow the context window."""
    out = _content_to_text([TextContent(type="text", text="y" * 50_000)])
    assert len(out) < 9_000
    assert "truncated" in out
