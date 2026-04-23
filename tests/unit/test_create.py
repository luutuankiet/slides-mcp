"""Tests for create.py — archetype + content → batchUpdate Requests."""
from __future__ import annotations

import pytest

from slides_mcp import create as create_mod
from slides_mcp import theme as theme_mod


@pytest.fixture
def sub_primary() -> theme_mod.SubTheme:
    return theme_mod.load_theme("example").sub("primary")


def _request_kinds(reqs: list[dict]) -> list[str]:
    kinds: list[str] = []
    for r in reqs:
        kinds.extend(r.keys())
    return kinds


def test_supported_archetypes_covers_mvp():
    supp = create_mod.supported_archetypes()
    assert "text_heavy_body" in supp
    assert "cover_with_hero" in supp
    assert "3col_pill_cards" in supp


def test_text_heavy_body_happy_path(sub_primary):
    content = {
        "title": "Bidi loop",
        "paragraphs": ["One.", "Two.", "Three."],
    }
    reqs, warnings = create_mod.build_slide_requests(
        slide_id="s_x", archetype_name="text_heavy_body", content=content, sub=sub_primary
    )
    assert warnings == []
    kinds = _request_kinds(reqs)
    # title = createShape + insertText + updateTextStyle(font) = 3
    # body  = createShape + insertText + updateTextStyle(font) = 3
    assert kinds.count("createShape") == 2
    assert kinds.count("insertText") == 2
    assert kinds.count("updateTextStyle") >= 1  # at least one font-bearing slot


def test_text_heavy_body_missing_required_slot_warns(sub_primary):
    content = {"title": "Orphan"}  # paragraphs missing
    reqs, warnings = create_mod.build_slide_requests(
        slide_id="s_x", archetype_name="text_heavy_body", content=content, sub=sub_primary
    )
    assert any("paragraphs" in w for w in warnings)
    # title still produces its 3-req burst
    kinds = _request_kinds(reqs)
    assert kinds.count("createShape") == 1


def test_3col_pill_cards_happy_path(sub_primary):
    content = {
        "title": "Compact reads",
        "lead": "Every tool compresses raw JSON.",
        "columns": [
            {"pill": "Clean DSL", "body": "100-150 tokens per slide."},
            {"pill": "Outline", "body": "~20 tok/slide."},
            {"pill": "Grep", "body": "search_deck + list_slides_by."},
        ],
    }
    reqs, warnings = create_mod.build_slide_requests(
        slide_id="s_x", archetype_name="3col_pill_cards", content=content, sub=sub_primary
    )
    assert warnings == []
    kinds = _request_kinds(reqs)
    # title: createShape + insertText + updateTextStyle
    # lead : createShape + insertText + updateTextStyle
    # per column (×3):
    #   pill: createShape + updateShapeProperties(fill) + insertText + updateTextStyle
    #   body: createShape + insertText + updateTextStyle
    #   → 7 requests per column
    # Total: 2*3 (title/lead) + 3*7 = 27
    assert kinds.count("createShape") == 2 + 2 * 3  # 2 text slots + 2 shapes per column
    assert kinds.count("updateShapeProperties") == 3  # 3 pill fills
    assert kinds.count("insertText") == 2 + 2 * 3


def test_3col_pill_cards_partial_columns(sub_primary):
    content = {
        "title": "Partial",
        "columns": [{"pill": "Only one", "body": "Just this."}],
    }
    reqs, _ = create_mod.build_slide_requests(
        slide_id="s_x", archetype_name="3col_pill_cards", content=content, sub=sub_primary
    )
    kinds = _request_kinds(reqs)
    # title (2 shapes) + 1 column (2 shapes) = 3 createShape (title shape + pill + body)
    assert kinds.count("createShape") == 1 + 2
    assert kinds.count("updateShapeProperties") == 1  # 1 pill fill


def test_3col_pill_cards_caps_at_3_columns(sub_primary):
    content = {
        "title": "Overflow",
        "columns": [
            {"pill": "a", "body": "A"},
            {"pill": "b", "body": "B"},
            {"pill": "c", "body": "C"},
            {"pill": "d", "body": "D — should be dropped"},
        ],
    }
    reqs, _ = create_mod.build_slide_requests(
        slide_id="s_x", archetype_name="3col_pill_cards", content=content, sub=sub_primary
    )
    # only 3 pills' worth of fills should land
    kinds = _request_kinds(reqs)
    assert kinds.count("updateShapeProperties") == 3


def test_cover_with_hero_basic(sub_primary):
    content = {"title": "slides-mcp", "subtitle": "Agent-driven Slides editing"}
    reqs, warnings = create_mod.build_slide_requests(
        slide_id="s_x", archetype_name="cover_with_hero", content=content, sub=sub_primary
    )
    # title is required, subtitle optional. No hero image in MVP.
    kinds = _request_kinds(reqs)
    assert kinds.count("createShape") == 2  # title + subtitle
    assert warnings == []


def test_unsupported_archetype_yields_warning_empty_requests(sub_primary):
    # logo_strip is registered but has no builder in create.py yet
    reqs, warnings = create_mod.build_slide_requests(
        slide_id="s_x",
        archetype_name="logo_strip",
        content={"logos": []},
        sub=sub_primary,
    )
    assert reqs == []
    assert any("logo_strip" in w for w in warnings)
    assert any("supported:" in w for w in warnings)


def test_unknown_archetype_raises(sub_primary):
    with pytest.raises(KeyError):
        create_mod.build_slide_requests(
            slide_id="s_x",
            archetype_name="does_not_exist",
            content={},
            sub=sub_primary,
        )


def test_pill_fill_uses_theme_brand_accent(sub_primary):
    content = {
        "title": "x",
        "columns": [{"pill": "p", "body": "b"}],
    }
    reqs, _ = create_mod.build_slide_requests(
        slide_id="s_x", archetype_name="3col_pill_cards", content=content, sub=sub_primary
    )
    # Find the updateShapeProperties for the pill; verify it resolved the theme color
    fill_reqs = [r for r in reqs if "updateShapeProperties" in r]
    assert len(fill_reqs) == 1
    rgb = fill_reqs[0]["updateShapeProperties"]["shapeProperties"]["shapeBackgroundFill"]["solidFill"]["color"]["rgbColor"]
    # example.primary brand_accent = #3366CC → (0.2, 0.4, 0.8)
    assert abs(rgb["red"] - 0.2) < 0.01
    assert abs(rgb["green"] - 0.4) < 0.01
    assert abs(rgb["blue"] - 0.8) < 0.01


def test_emu_conversion_is_correct():
    # 1 inch = 914400 EMU
    assert create_mod._inch_to_emu(1.0) == 914400
    assert create_mod._inch_to_emu(0.5) == 457200
    assert create_mod._inch_to_emu(14.4) == 13167360


def test_hex_to_rgb_fracs_validates():
    r = create_mod._hex_to_rgb_fracs("#3366CC")
    assert abs(r["red"] - 0.2) < 0.01
    with pytest.raises(ValueError):
        create_mod._hex_to_rgb_fracs("xyz")
