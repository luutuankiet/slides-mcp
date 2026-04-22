from __future__ import annotations

from slides_mcp.normalize import extract_notes_text, flatten, normalize_page
from tests.fixtures import slide_3col_pill_cards, slide_cover_with_hero


def test_normalize_page_returns_flat_shapes():
    page = slide_3col_pill_cards()
    shapes = normalize_page(page)
    assert len(shapes) == 11  # title + lead + 3 cards + 3 pills + 3 bodies
    kinds = [s.kind for s in shapes]
    assert kinds.count("text") == 8
    assert kinds.count("shape") == 3


def test_normalize_emu_to_inches():
    page = slide_cover_with_hero()
    shapes = normalize_page(page)
    hero = next(s for s in shapes if s.kind == "picture")
    assert hero.w_in == 8.0
    assert hero.h_in == 9.0
    assert hero.left_in == 0.0


def test_normalize_text_runs_and_color():
    page = slide_3col_pill_cards()
    shapes = normalize_page(page)
    pill = next(s for s in shapes if s.text == "Semantic Layer")
    assert pill.runs
    run = pill.runs[0]
    assert run.font_family == "Inter"
    assert run.size_pt == 22
    assert run.bold is True
    assert run.color_hex == "#3366CC"


def test_normalize_shape_fill():
    page = slide_cover_with_hero()
    shapes = normalize_page(page)
    panel = next(s for s in shapes if s.object_id == "accent_panel")
    assert panel.kind == "shape"
    assert panel.fill_hex == "#1F4F9F"


def test_flatten_handles_no_groups():
    page = slide_3col_pill_cards()
    shapes = normalize_page(page)
    assert len(flatten(shapes)) == len(shapes)


def test_extract_notes_text():
    page = slide_3col_pill_cards()
    notes = extract_notes_text(page)
    assert "last-mile" in notes
