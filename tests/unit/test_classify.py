from __future__ import annotations

from slides_mcp.classify import classify, classify_with_debug
from slides_mcp.normalize import normalize_page
from tests.fixtures import (
    slide_3col_pill_cards,
    slide_4col_numbered_flow,
    slide_cover_with_hero,
    slide_text_heavy,
    slide_text_left_image_right,
)


def test_classify_3col_pill_cards():
    shapes = normalize_page(slide_3col_pill_cards())
    assert classify(shapes) == "3col_pill_cards"


def test_classify_cover_with_hero():
    shapes = normalize_page(slide_cover_with_hero())
    assert classify(shapes) == "cover_with_hero"


def test_classify_text_heavy_body():
    shapes = normalize_page(slide_text_heavy())
    assert classify(shapes) == "text_heavy_body"


def test_classify_text_left_image_right():
    shapes = normalize_page(slide_text_left_image_right())
    assert classify(shapes) == "text_left_image_right"


def test_classify_4col_numbered_flow():
    shapes = normalize_page(slide_4col_numbered_flow())
    assert classify(shapes) == "4_col_numbered_flow"


def test_classify_debug_surfaces_signals():
    shapes = normalize_page(slide_3col_pill_cards())
    dbg = classify_with_debug(shapes)
    assert dbg["archetype"] == "3col_pill_cards"
    assert dbg["dominant_col_count"] == 3
