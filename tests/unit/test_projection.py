"""Tests for the v2 projection module — outline / summary / full / raw modes."""
from __future__ import annotations

import pytest

from slides_mcp.classify import classify
from slides_mcp.normalize import extract_notes_text, normalize_page
from slides_mcp.projection import BUDGET_TOK_PER_SLIDE, best_title, project
from tests.fixtures import (
    page as fixt_page,
)
from tests.fixtures import (
    slide_3col_pill_cards,
    slide_4col_numbered_flow,
    slide_cover_with_hero,
    slide_text_heavy,
    slide_text_left_image_right,
    textbox,
)


def _setup(page):
    shapes = normalize_page(page)
    archetype = classify(shapes)
    notes = extract_notes_text(page)
    return shapes, archetype, notes


# ---- best_title --------------------------------------------------------


def test_best_title_picks_largest_text():
    shapes, _, _ = _setup(slide_3col_pill_cards())
    flat = [s for s in shapes if s.kind == "text"]
    title = best_title(flat)
    assert title is not None
    assert "Looker is the Heart" in (title.text or "")


def test_best_title_returns_none_for_empty_list():
    assert best_title([]) is None


# ---- outline mode ------------------------------------------------------


def test_outline_3col():
    page = slide_3col_pill_cards()
    shapes, archetype, notes = _setup(page)
    out = project("slide_3col", shapes, archetype, notes, detail="outline")
    assert out["slide_id"] == "slide_3col"
    assert "Looker" in out["title"]
    assert out["archetype"] == "3col_pill_cards"
    assert out["element_count"] == 11
    assert out["has_notes"] is True
    assert out["has_image"] is False


def test_outline_cover_flags_image():
    page = slide_cover_with_hero()
    shapes, archetype, notes = _setup(page)
    out = project("slide_cover", shapes, archetype, notes, detail="outline")
    assert out["archetype"] == "cover_with_hero"
    assert out["has_image"] is True
    assert out["has_notes"] is False


def test_outline_truncates_long_titles():
    long = "x" * 200
    p = fixt_page("long", [textbox("t", long, 0, 0, 10, 1, font="Inter", size_pt=40, bold=True)])
    shapes, archetype, notes = _setup(p)
    out = project("long", shapes, archetype, notes, detail="outline")
    assert len(out["title"]) == 120


# ---- summary mode ------------------------------------------------------


def test_summary_text_heavy_includes_body():
    page = slide_text_heavy()
    shapes, archetype, notes = _setup(page)
    out = project("slide_heavy", shapes, archetype, notes, detail="summary")
    assert out["title"] == "Background and Context"
    assert "body" in out
    assert "long paragraph" in out["body"]
    assert len(out["body"]) <= 600


def test_summary_emits_notes_preview():
    page = slide_3col_pill_cards()
    shapes, archetype, notes = _setup(page)
    out = project("slide_3col", shapes, archetype, notes, detail="summary")
    assert "notes_preview" in out
    assert "last-mile" in out["notes_preview"]


def test_summary_emits_image_count():
    page = slide_text_left_image_right()
    shapes, archetype, notes = _setup(page)
    out = project("slide_ti", shapes, archetype, notes, detail="summary")
    assert out.get("image_count") == 1


def test_summary_image_count_omitted_when_disabled():
    page = slide_text_left_image_right()
    shapes, archetype, notes = _setup(page)
    out = project(
        "slide_ti", shapes, archetype, notes,
        detail="summary", include_images=False,
    )
    assert "image_count" not in out


def test_summary_no_notes_field_when_notes_empty():
    page = slide_cover_with_hero()  # no notes
    shapes, archetype, notes = _setup(page)
    out = project("slide_cover", shapes, archetype, notes, detail="summary")
    assert "notes_preview" not in out


# ---- full mode ---------------------------------------------------------


def test_full_returns_all_body_lines():
    page = slide_3col_pill_cards()
    shapes, archetype, notes = _setup(page)
    out = project("slide_3col", shapes, archetype, notes, detail="full")
    body = out["body"]
    for header in ("Semantic Layer", "Empowered Users", "Untapped Potential"):
        assert any(header in line for line in body)


def test_full_emits_image_refs():
    page = slide_text_left_image_right()
    shapes, archetype, notes = _setup(page)
    out = project("slide_ti", shapes, archetype, notes, detail="full")
    assert out["images"] == ["ref://screenshot"]


def test_full_omits_images_when_disabled():
    page = slide_text_left_image_right()
    shapes, archetype, notes = _setup(page)
    out = project(
        "slide_ti", shapes, archetype, notes,
        detail="full", include_images=False,
    )
    assert "images" not in out


def test_full_includes_full_notes():
    page = slide_3col_pill_cards()
    shapes, archetype, notes = _setup(page)
    out = project("slide_3col", shapes, archetype, notes, detail="full")
    assert out["notes"] == "Emphasize the last-mile problem in this section."


def test_full_no_body_when_only_title():
    p = fixt_page("only", [
        textbox("t", "Only Title", 1, 1, 8, 1, font="Inter", size_pt=36, bold=True),
    ])
    shapes, archetype, notes = _setup(p)
    out = project("only", shapes, archetype, notes, detail="full")
    assert out["title"] == "Only Title"
    assert "body" not in out


# ---- raw mode ----------------------------------------------------------


def test_raw_emits_every_leaf_element():
    page = slide_3col_pill_cards()
    shapes, archetype, notes = _setup(page)
    out = project("slide_3col", shapes, archetype, notes, detail="raw")
    assert out["slide_id"] == "slide_3col"
    # 1 title + 1 lead + 3 cards + 3 pills + 3 bodies
    assert len(out["elements"]) == 11
    for e in out["elements"]:
        assert "at" in e
        assert len(e["at"]) == 4


def test_raw_carries_fill_hex():
    page = slide_3col_pill_cards()
    shapes, archetype, notes = _setup(page)
    out = project("slide_3col", shapes, archetype, notes, detail="raw")
    cards = [e for e in out["elements"] if e.get("shape_type") == "RECTANGLE"]
    assert len(cards) == 3
    assert all(e.get("fill_hex") == "#F3F3F3" for e in cards)


def test_raw_carries_runs():
    page = slide_3col_pill_cards()
    shapes, archetype, notes = _setup(page)
    out = project("slide_3col", shapes, archetype, notes, detail="raw")
    title_el = next(e for e in out["elements"] if e["id"] == "title")
    assert title_el["runs"][0]["font_family"] == "Inter"
    assert title_el["runs"][0]["size_pt"] == 36
    assert title_el["runs"][0]["bold"] is True


# ---- dispatcher --------------------------------------------------------


def test_invalid_detail_raises():
    page = slide_text_heavy()
    shapes, archetype, notes = _setup(page)
    with pytest.raises(ValueError, match="outline\\|summary\\|full\\|raw"):
        project("x", shapes, archetype, notes, detail="bogus")  # type: ignore[arg-type]


def test_budget_table_complete():
    for d in ("outline", "summary", "full", "raw"):
        assert d in BUDGET_TOK_PER_SLIDE
        assert BUDGET_TOK_PER_SLIDE[d] > 0


def test_4col_numbered_classifies_and_projects():
    """Smoke test that the 4-column flow projection still produces full body."""
    page = slide_4col_numbered_flow()
    shapes, archetype, notes = _setup(page)
    out = project("slide_4col", shapes, archetype, notes, detail="full")
    assert "Field Usage Explore Bug Fixes" in " ".join(out["body"])
    assert "Health check on Monitoring Dashboards" in " ".join(out["body"])
