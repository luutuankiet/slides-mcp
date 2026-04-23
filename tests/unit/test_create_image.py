"""Unit tests for the `create_image` MCP tool.

create_image has two modes (Decision P, LOG-014 — shapes-first):
- URL mode (image_url set) → createImage request
- Placeholder mode (image_prompt set) → createShape(RECTANGLE) + insertText("[IMAGE: …]")

Tests verify:
- URL mode request shape: createImage with url, element_properties
- Placeholder mode request shape: createShape + insertText with [IMAGE: prompt]
- XOR validation: both args or neither → ValueError
- at-shape validation: <4 elements → ValueError
- positive w/h validation: 0/negative → ValueError
- return payload contents (mode, object_id, thumbnail_url, etc.)
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from slides_mcp import server as server_mod


def _call_tool(**kwargs):
    """Unwrap FastMCP-registered tool to its underlying callable."""
    fn = getattr(server_mod.create_image, "fn", server_mod.create_image)
    return fn(**kwargs)


# ---------- URL mode ----------

def test_create_image_url_mode_emits_createimage_with_element_properties():
    """URL mode: one createImage request with correct objectId, url,
    page ref, EMU size + transform."""
    fake_thumb = "https://example.test/thumb.png"
    with patch.object(
        server_mod.slides_api, "batch_update", return_value={"replies": [{}]},
    ) as batch_mock, patch.object(
        server_mod.slides_api, "get_thumbnail", return_value=fake_thumb,
    ):
        result = _call_tool(
            deck_url="https://docs.google.com/presentation/d/DECK42/edit",
            slide_id="s_tgt",
            at=[1.0, 2.0, 4.5, 3.0],
            image_url="https://example.test/photo.jpg",
        )
    assert batch_mock.call_count == 1
    _, reqs = batch_mock.call_args.args
    assert len(reqs) == 1
    req = reqs[0]["createImage"]
    assert req["url"] == "https://example.test/photo.jpg"
    assert req["elementProperties"]["pageObjectId"] == "s_tgt"
    size = req["elementProperties"]["size"]
    assert size["width"]["magnitude"] == int(4.5 * 914400)
    assert size["height"]["magnitude"] == int(3.0 * 914400)
    transform = req["elementProperties"]["transform"]
    assert transform["translateX"] == int(1.0 * 914400)
    assert transform["translateY"] == int(2.0 * 914400)
    assert result["mode"] == "image"
    assert result["deck_id"] == "DECK42"
    assert result["slide_id"] == "s_tgt"
    assert result["applied_request_count"] == 1
    assert result["thumbnail_url"] == fake_thumb
    assert result["object_id"].startswith("i_")


# ---------- Placeholder mode ----------

def test_create_image_placeholder_mode_emits_createshape_plus_inserttext():
    """Placeholder mode: two requests — createShape(RECTANGLE) + insertText
    with '[IMAGE: {prompt}]' marker. Same objectId on both requests."""
    with patch.object(
        server_mod.slides_api, "batch_update", return_value={"replies": [{}, {}]},
    ) as batch_mock, patch.object(
        server_mod.slides_api, "get_thumbnail", return_value="https://t/",
    ):
        result = _call_tool(
            deck_url="https://docs.google.com/presentation/d/DECK42/edit",
            slide_id="s_tgt",
            at=[0.5, 0.5, 5.0, 3.0],
            image_prompt="a cityscape at dusk",
        )
    _, reqs = batch_mock.call_args.args
    assert len(reqs) == 2
    # First: createShape(RECTANGLE)
    shape_req = reqs[0]["createShape"]
    assert shape_req["shapeType"] == "RECTANGLE"
    shape_id = shape_req["objectId"]
    assert shape_id.startswith("i_")
    # Second: insertText with the exact marker format
    text_req = reqs[1]["insertText"]
    assert text_req["objectId"] == shape_id  # same shape
    assert text_req["text"] == "[IMAGE: a cityscape at dusk]"
    assert text_req["insertionIndex"] == 0
    assert result["mode"] == "placeholder"
    assert result["applied_request_count"] == 2
    assert result["object_id"] == shape_id


def test_create_image_placeholder_embeds_multiline_prompt_verbatim():
    """Prompts may include spaces, punctuation, long descriptions — the tool
    embeds the literal string. No truncation, no sanitization."""
    prompt = "A diagram showing: data flow from warehouse → BI tool → agent"
    with patch.object(
        server_mod.slides_api, "batch_update", return_value={"replies": [{}, {}]},
    ) as batch_mock, patch.object(
        server_mod.slides_api, "get_thumbnail", return_value="https://t/",
    ):
        _call_tool(
            deck_url="DECK42",
            slide_id="s_tgt",
            at=[0.0, 0.0, 4.0, 3.0],
            image_prompt=prompt,
        )
    _, reqs = batch_mock.call_args.args
    assert reqs[1]["insertText"]["text"] == f"[IMAGE: {prompt}]"


# ---------- Validation ----------

def test_create_image_rejects_both_url_and_prompt():
    """XOR: caller must pick exactly one mode."""
    with pytest.raises(ValueError, match="exactly one of image_url"):
        _call_tool(
            deck_url="DECK42",
            slide_id="s_tgt",
            at=[0, 0, 2, 2],
            image_url="https://example.test/a.jpg",
            image_prompt="alternate prompt",
        )


def test_create_image_rejects_neither_url_nor_prompt():
    """XOR: caller must pick exactly one mode — not zero."""
    with pytest.raises(ValueError, match="exactly one of image_url"):
        _call_tool(
            deck_url="DECK42",
            slide_id="s_tgt",
            at=[0, 0, 2, 2],
        )


def test_create_image_rejects_short_at():
    """`at` must have 4 components."""
    with pytest.raises(ValueError, match="at must be"):
        _call_tool(
            deck_url="DECK42",
            slide_id="s_tgt",
            at=[0, 0, 2],  # missing h
            image_prompt="x",
        )


def test_create_image_rejects_non_positive_width_or_height():
    """w and h must both be > 0 — zero-area shapes aren't useful placeholders."""
    with pytest.raises(ValueError, match="positive"):
        _call_tool(
            deck_url="DECK42",
            slide_id="s_tgt",
            at=[0, 0, 0, 3],  # w=0
            image_prompt="x",
        )
    with pytest.raises(ValueError, match="positive"):
        _call_tool(
            deck_url="DECK42",
            slide_id="s_tgt",
            at=[0, 0, 3, -1],  # h negative
            image_prompt="x",
        )


def test_create_image_returns_thumbnail_url_for_vision_output_loop():
    """VISION OUTPUT invariant: write tool chains to native MCP ImageContent.
    create_image returns the thumbnail_url — caller calls render_thumbnail
    on slide_id to consume bytes. The next_step_hint surfaces this."""
    with patch.object(
        server_mod.slides_api, "batch_update", return_value={"replies": [{}]},
    ), patch.object(
        server_mod.slides_api, "get_thumbnail", return_value="https://thumb/",
    ):
        result = _call_tool(
            deck_url="DECK42",
            slide_id="s_abc",
            at=[0, 0, 4, 3],
            image_url="https://example.test/x.png",
        )
    assert result["thumbnail_url"] == "https://thumb/"
    assert "render_thumbnail" in result["next_step_hint"]
    assert "s_abc" in result["next_step_hint"]
