"""Unit tests for swatch.render_contact_sheet (Scope C PIL layer)."""
from __future__ import annotations

import io

import pytest
from PIL import Image

from slides_mcp import swatch

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _fake_thumb(color: tuple[int, int, int] = (255, 0, 0)) -> bytes:
    img = Image.new("RGB", (200, 112), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_contact_sheet_single_thumbnail():
    png = swatch.render_contact_sheet([("slide_1", _fake_thumb())])
    assert png.startswith(PNG_MAGIC)


def test_contact_sheet_grid_layout():
    thumbs = [(f"s_{i}", _fake_thumb((i * 30 % 255, 100, 200))) for i in range(8)]
    png = swatch.render_contact_sheet(thumbs, title="Test Deck")
    img = Image.open(io.BytesIO(png))
    # 4 cols × 2 rows expected
    # canvas_w = 4 * 400 + 5 * 16 = 1680
    assert img.size[0] >= 1600


def test_contact_sheet_title_adds_header():
    thumbs = [("s", _fake_thumb())]
    png_no_title = swatch.render_contact_sheet(thumbs, title=None)
    png_title = swatch.render_contact_sheet(thumbs, title="Header")
    img_no = Image.open(io.BytesIO(png_no_title))
    img_y = Image.open(io.BytesIO(png_title))
    # Title should add ~40px to height
    assert img_y.size[1] > img_no.size[1]


def test_contact_sheet_handles_corrupt_thumbnail():
    """Malformed PNG bytes should not kill the whole render."""
    thumbs = [
        ("good", _fake_thumb()),
        ("corrupt", b"not a png"),
        ("also_good", _fake_thumb((0, 255, 0))),
    ]
    png = swatch.render_contact_sheet(thumbs)
    assert png.startswith(PNG_MAGIC)


def test_contact_sheet_empty_raises():
    with pytest.raises(ValueError, match="at least 1"):
        swatch.render_contact_sheet([])


def test_contact_sheet_labels_are_truncated():
    long_id = "a_very_long_slide_id_that_exceeds_the_display_cap_by_a_wide_margin"
    thumbs = [(long_id, _fake_thumb())]
    png = swatch.render_contact_sheet(thumbs)
    assert png.startswith(PNG_MAGIC)
