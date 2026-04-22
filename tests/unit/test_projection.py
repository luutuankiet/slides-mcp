from __future__ import annotations

import yaml

from slides_mcp.classify import classify
from slides_mcp.normalize import extract_notes_text, normalize_page
from slides_mcp.projection import project
from slides_mcp.theme import load_theme
from tests.fixtures import (
    slide_3col_pill_cards,
    slide_cover_with_hero,
    slide_text_heavy,
)


def _yaml_tokens(d: dict) -> int:
    """Approx token count assuming ~4 chars/token on structured YAML."""
    return len(yaml.safe_dump(d, sort_keys=False)) // 4


def test_project_3col_pill_cards_clean():
    page = slide_3col_pill_cards()
    shapes = normalize_page(page)
    sub = load_theme("example").sub("primary")
    dsl = project(shapes, "3col_pill_cards", "slide_3col",
                  extract_notes_text(page), sub, mode="clean")

    assert dsl["layout"] == "3col_pill_cards"
    assert dsl["title"] == "Looker is the Heart of Business Analytics"
    assert len(dsl["columns"]) == 3
    assert dsl["columns"][0]["pill"] == "Semantic Layer"
    assert "Trusted LookML" in dsl["columns"][0]["body"]
    assert "last-mile" in dsl.get("notes", "")
    # token budget for clean mode should be well under 200
    assert _yaml_tokens(dsl) < 200


def test_project_cover_clean():
    page = slide_cover_with_hero()
    shapes = normalize_page(page)
    sub = load_theme("example").sub("primary")
    dsl = project(shapes, "cover_with_hero", "slide_cover", "", sub, mode="clean")

    assert dsl["layout"] == "cover_with_hero"
    assert dsl["title"] == "Agentic analytics"
    assert dsl["hero"]["side"] == "left"
    assert _yaml_tokens(dsl) < 100


def test_project_text_heavy_clean():
    page = slide_text_heavy()
    shapes = normalize_page(page)
    sub = load_theme("example").sub("primary")
    dsl = project(shapes, "text_heavy_body", "slide_heavy", "", sub, mode="clean")

    assert dsl["layout"] == "text_heavy_body"
    assert dsl["title"] == "Background and Context"
    assert len(dsl["paragraphs"]) >= 1


def test_project_faithful_preserves_geometry():
    page = slide_3col_pill_cards()
    shapes = normalize_page(page)
    sub = load_theme("example").sub("primary")
    dsl = project(shapes, "3col_pill_cards", "slide_3col", "", sub, mode="faithful")

    assert dsl["mode"] == "faithful"
    assert len(dsl["elements"]) == 11
    first = dsl["elements"][0]
    assert "at" in first and len(first["at"]) == 4  # [left, top, w, h]


def test_project_fallback_when_clean_rejects():
    # Classify as 3col_pill_cards but feed a cover slide → should fall back
    page = slide_cover_with_hero()
    shapes = normalize_page(page)
    sub = load_theme("example").sub("primary")
    dsl = project(shapes, "3col_pill_cards", "slide_cover", "", sub, mode="clean")

    assert dsl["mode"] == "faithful"
    assert "fallback_reason" in dsl


def test_project_end_to_end_classify_then_project():
    """Real flow: normalize → classify → project."""
    page = slide_3col_pill_cards()
    shapes = normalize_page(page)
    archetype = classify(shapes)
    sub = load_theme("example").sub("primary")
    dsl = project(shapes, archetype, "slide_3col", extract_notes_text(page), sub, mode="clean")
    assert dsl["layout"] == "3col_pill_cards"
    assert len(dsl["columns"]) == 3


def test_project_theme_role_mapping_for_cover_title():
    """Cover title is #FFFFFF in 60pt Inter bold — should map to no color role
    unless theme has a white role, but font_role should NOT be display (size mismatch)."""
    page = slide_cover_with_hero()
    shapes = normalize_page(page)
    sub = load_theme("example").sub("primary")
    # Use faithful to inspect the title element
    dsl = project(shapes, "cover_with_hero", "slide_cover", "", sub, mode="faithful")
    title = next(e for e in dsl["elements"] if e.get("text", "").startswith("Agentic"))
    # size_pt 60 doesn't match example theme's display (36pt), so font_role should be absent
    assert "font_role" not in title
    assert title.get("font_family") == "Inter"


def test_project_clean_no_elements_by_default():
    """Default read path must NOT include `elements` — preserves 150 tok budget."""
    page = slide_3col_pill_cards()
    shapes = normalize_page(page)
    sub = load_theme("example").sub("primary")
    dsl = project(shapes, "3col_pill_cards", "slide_3col", "", sub, mode="clean")
    assert "elements" not in dsl


def test_project_clean_with_include_elements_emits_geometry_channel():
    """Opt-in geometry channel — caller that wants to move icons asks for elements."""
    page = slide_3col_pill_cards()
    shapes = normalize_page(page)
    sub = load_theme("example").sub("primary")
    dsl = project(
        shapes, "3col_pill_cards", "slide_3col", "", sub,
        mode="clean", include_elements=True,
    )
    assert "elements" in dsl
    assert len(dsl["elements"]) == 11  # same leaf count as faithful (flattened)
    first = dsl["elements"][0]
    assert set(first.keys()) == {"id", "at"}
    assert len(first["at"]) == 4  # [x, y, w, h]
    # semantic slots still present — elements is additive
    assert dsl["layout"] == "3col_pill_cards"
    assert len(dsl["columns"]) == 3
