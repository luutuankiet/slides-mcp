"""Unit tests for swatch.py — PIL tone-card rendering."""
from __future__ import annotations

import io

import pytest
from PIL import Image

from slides_mcp import swatch

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _minimal_brief() -> dict:
    return {
        "version": 1,
        "palette": {
            "surface": "#111335",
            "accent": "#E8612E",
            "text": "#FFFFFF",
            "category_set": ["#E8612E", "#0F1A4A", "#5A6B9A"],
        },
        "shape_language": "sharp",
        "numbering_style": "bold",
        "tone": "clean editorial",
        "image_prompt_style": "documentary photography",
    }


def test_hex_to_rgb_standard():
    assert swatch._hex_to_rgb("#000000") == (0, 0, 0)
    assert swatch._hex_to_rgb("#FFFFFF") == (255, 255, 255)
    assert swatch._hex_to_rgb("#E8612E") == (232, 97, 46)


def test_hex_to_rgb_shortform():
    assert swatch._hex_to_rgb("#fff") == (255, 255, 255)
    assert swatch._hex_to_rgb("#000") == (0, 0, 0)


def test_hex_to_rgb_bad_input():
    with pytest.raises(ValueError):
        swatch._hex_to_rgb("not a hex")
    with pytest.raises(ValueError):
        swatch._hex_to_rgb("#GGGGGG")


def test_rgb_luminance_monotonic():
    assert swatch._rgb_luminance((0, 0, 0)) < swatch._rgb_luminance((128, 128, 128))
    assert swatch._rgb_luminance((128, 128, 128)) < swatch._rgb_luminance((255, 255, 255))


def test_readable_on_picks_contrast():
    # White text on dark bg, black text on light bg
    assert swatch._readable_on((0, 0, 0)) == (255, 255, 255)
    assert swatch._readable_on((255, 255, 255)) == (0, 0, 0)


def test_looks_serif_heuristic():
    assert swatch._looks_serif("Fraunces")
    assert swatch._looks_serif("DM Serif Display")
    assert not swatch._looks_serif("Inter")
    assert not swatch._looks_serif(None)


def test_render_swatch_returns_valid_png():
    png = swatch.render_swatch(_minimal_brief())
    assert png.startswith(PNG_MAGIC)
    assert len(png) > 1000  # non-empty, non-trivial


def test_render_swatch_dimensions_default():
    png = swatch.render_swatch(_minimal_brief())
    img = Image.open(io.BytesIO(png))
    assert img.size == (swatch.SWATCH_W, swatch.SWATCH_H)
    assert img.mode == "RGB"


def test_render_swatch_minimal_brief_just_palette():
    """Only palette — no shape_language, numbering, tone, fonts. Should still render."""
    png = swatch.render_swatch({"palette": {"surface": "#333333", "accent": "#FF0000"}})
    assert png.startswith(PNG_MAGIC)
    img = Image.open(io.BytesIO(png))
    assert img.size == (swatch.SWATCH_W, swatch.SWATCH_H)


def test_render_swatch_with_font_family_axis():
    """font_family optional — rendering should succeed regardless of TTF availability."""
    brief = _minimal_brief()
    brief["font_family"] = {"heading": "Fraunces", "body": "Inter"}
    png = swatch.render_swatch(brief)
    assert png.startswith(PNG_MAGIC)
    assert len(png) > 1000


def test_render_swatch_all_shape_languages():
    for lang in ("sharp", "rounded", "mixed"):
        brief = _minimal_brief()
        brief["shape_language"] = lang
        png = swatch.render_swatch(brief)
        assert png.startswith(PNG_MAGIC)


def test_render_swatch_all_numbering_styles():
    for style in ("bold", "outlined", "dot", "hidden"):
        brief = _minimal_brief()
        brief["numbering_style"] = style
        png = swatch.render_swatch(brief)
        assert png.startswith(PNG_MAGIC)


def test_render_swatch_bad_hex_raises():
    bad = {"palette": {"surface": "not-a-hex", "accent": "#E8612E"}}
    with pytest.raises(ValueError, match="bad palette color"):
        swatch.render_swatch(bad)


def test_render_swatch_grid_single_brief():
    png = swatch.render_swatch_grid([_minimal_brief()])
    assert png.startswith(PNG_MAGIC)
    img = Image.open(io.BytesIO(png))
    # 1 col layout
    assert img.size[0] >= swatch.SWATCH_W


def test_render_swatch_grid_three_briefs():
    briefs = []
    for accent in ("#E8612E", "#134E4A", "#B45309"):
        b = _minimal_brief()
        b["palette"]["accent"] = accent
        b["tone"] = f"variant {accent}"
        briefs.append(b)
    png = swatch.render_swatch_grid(briefs)
    assert png.startswith(PNG_MAGIC)
    img = Image.open(io.BytesIO(png))
    # 3-col layout: width ~= 3 tiles + gaps
    assert img.size[0] >= 3 * 200  # conservative lower bound after downscale


def test_render_swatch_grid_five_briefs():
    """5 briefs -> 3 cols, 2 rows."""
    briefs = [_minimal_brief() for _ in range(5)]
    png = swatch.render_swatch_grid(briefs)
    img = Image.open(io.BytesIO(png))
    # 2 rows of tiles + label bars. Tile is ~282px tall at 3-col scale;
    # canvas must be tall enough to contain at least a partial second row.
    assert img.size[1] >= 600, f"expected >=600px for 2-row grid, got {img.size[1]}"
    # width = 3 cols + 4 gaps
    assert img.size[0] >= 1400


def test_render_swatch_grid_empty_raises():
    with pytest.raises(ValueError, match="at least 1"):
        swatch.render_swatch_grid([])


def test_compose_single_scales_proportionally():
    """When called at half-size, output should still be valid PNG + correct dims."""
    img = swatch._compose_single(_minimal_brief(), 400, 225)
    assert img.size == (400, 225)
    assert img.mode == "RGB"


def test_render_swatch_preserves_font_family_label():
    """When font_family is given, family name should be caption-annotated in pixels.

    We can't OCR easily, but we can assert the output bytes differ from the
    no-font-family version — proves the caption code path ran.
    """
    brief_no_font = _minimal_brief()
    brief_with_font = _minimal_brief()
    brief_with_font["font_family"] = {"heading": "Fraunces", "body": "Inter"}
    png_a = swatch.render_swatch(brief_no_font)
    png_b = swatch.render_swatch(brief_with_font)
    assert png_a != png_b, "font_family should produce a visibly different card"


def test_render_swatch_category_set_capped_at_5():
    brief = _minimal_brief()
    brief["palette"]["category_set"] = [
        "#E8612E", "#134E4A", "#B45309", "#5A6B9A", "#888888",
        "#AABBCC", "#DDEEFF",  # extras — should be ignored
    ]
    png = swatch.render_swatch(brief)
    assert png.startswith(PNG_MAGIC)


def test_render_swatch_skips_bad_category_hex_gracefully():
    brief = _minimal_brief()
    brief["palette"]["category_set"] = ["#E8612E", "not-hex", "#134E4A"]
    png = swatch.render_swatch(brief)
    assert png.startswith(PNG_MAGIC)
