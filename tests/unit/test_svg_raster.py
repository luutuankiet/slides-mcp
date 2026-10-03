"""SVG rasterising for place_image: real resvg, in its child process."""
from __future__ import annotations

import struct

import pytest

from slides_mcp import svg_raster

LABELLED = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 100">'
            '<rect width="200" height="100" fill="#eee"/>'
            '<text x="20" y="60" font-family="sans-serif" font-size="30">Label</text></svg>')


def png_size(data: bytes) -> tuple[int, int]:
    return struct.unpack(">II", data[16:24])


def test_text_with_no_font_comes_back_as_a_warning():
    raster = svg_raster.rasterise(LABELLED, 200, 100, system_fonts=False)
    assert png_size(raster.png) == (600, 300)
    (warning,) = raster.warnings
    assert "sans-serif" in warning and "did not render" in warning


def test_square_svg_in_a_wide_box_is_rendered_at_the_box_aspect():
    square = ('<svg xmlns="http://www.w3.org/2000/svg" width="50" height="50">'
              '<circle cx="25" cy="25" r="20" fill="red"/></svg>')
    raster = svg_raster.rasterise(square, 300, 100)
    assert png_size(raster.png) == (900, 300)
    assert raster.warnings == []


def test_huge_box_stays_under_googles_pixel_limit():
    w, h = svg_raster.pixel_size(5000, 5000)
    assert w * h <= svg_raster.MAX_PIXELS < 25_000_000
    assert svg_raster.pixel_size(720, 405) == (2160, 1215)


def test_renderer_refusal_is_quoted():
    # Well-formed XML that resvg itself rejects: nesting past its node limit.
    with pytest.raises(svg_raster.SvgError, match="renderer refused it"):
        svg_raster.rasterise('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 9 9">'
                             + '<g>' * 2000 + '</g>' * 2000 + '</svg>',
                             100, 100)


def test_svg_without_a_size_is_refused():
    with pytest.raises(svg_raster.SvgError, match="viewBox"):
        svg_raster.rasterise('<svg xmlns="http://www.w3.org/2000/svg"/>', 100, 100)
