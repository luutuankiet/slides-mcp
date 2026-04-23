"""Tests for brief-fallback resolution in create.py builders (Phase 2B).

Resolution order asserted in every builder:
    per_slide_content > brief.palette.* > theme YAML > safety default

Tests fall into 3 shapes per builder:
  1. `test_<builder>_brief_fills_<field>`: content lacks the field, brief carries
     it — assert the brief's hex lands in the emitted requests.
  2. `test_<builder>_per_slide_wins_over_brief_<field>`: both content and brief
     carry the field — assert the content value wins.
  3. `test_<builder>_falls_through_without_brief`: brief=None preserves the
     pre-Phase-2 code path (regression guard for backward compat).
"""
from __future__ import annotations

import pytest

from slides_mcp import create as create_mod
from slides_mcp import theme as theme_mod


@pytest.fixture
def sub_primary() -> theme_mod.SubTheme:
    return theme_mod.load_theme("example").sub("primary")


def _rgb_fracs(hex_value: str) -> dict[str, float]:
    """Slides API encodes hex as {red, green, blue} with 0..1 fractions.
    Any missing channel is omitted (API treats omitted → 0.0). Builders use
    `_hex_to_rgb_fracs` which always emits all 3 channels.
    """
    h = hex_value.lstrip("#").upper()
    return {
        "red": int(h[0:2], 16) / 255,
        "green": int(h[2:4], 16) / 255,
        "blue": int(h[4:6], 16) / 255,
    }


def _fill_colors_in_reqs(reqs: list[dict]) -> list[dict[str, float]]:
    """Collect every rgbColor dict used as a shapeBackgroundFill."""
    out: list[dict[str, float]] = []
    for r in reqs:
        usp = r.get("updateShapeProperties")
        if not usp:
            continue
        color = (
            usp.get("shapeProperties", {})
            .get("shapeBackgroundFill", {})
            .get("solidFill", {})
            .get("color", {})
            .get("rgbColor")
        )
        if color:
            out.append(color)
    return out


def _text_colors_in_reqs(reqs: list[dict]) -> list[dict[str, float]]:
    """Collect every rgbColor dict used as a textStyle.foregroundColor."""
    out: list[dict[str, float]] = []
    for r in reqs:
        uts = r.get("updateTextStyle")
        if not uts:
            continue
        color = (
            uts.get("style", {})
            .get("foregroundColor", {})
            .get("opaqueColor", {})
            .get("rgbColor")
        )
        if color:
            out.append(color)
    return out


def _approx(expected: dict[str, float], actual: dict[str, float]) -> bool:
    """Loose equality — Slides API omits 0-valued channels, builders include."""
    for ch, exp in expected.items():
        act = actual.get(ch, 0.0)
        if abs(act - exp) > 1e-4:
            return False
    return True


def _contains_color(reqs_colors: list[dict[str, float]], hex_value: str) -> bool:
    target = _rgb_fracs(hex_value)
    return any(_approx(target, actual) for actual in reqs_colors)


# ---------------------------------------------------------------------------
# _brief_get helper
# ---------------------------------------------------------------------------


def test_brief_get_returns_none_when_brief_missing():
    assert create_mod._brief_get(None, "palette.accent") is None


def test_brief_get_navigates_dotted_path():
    brief = {"palette": {"accent": "#E8612E", "text": "#000000"}}
    assert create_mod._brief_get(brief, "palette.accent") == "#E8612E"
    assert create_mod._brief_get(brief, "palette.text") == "#000000"


def test_brief_get_returns_none_when_path_missing():
    brief = {"palette": {"accent": "#E8612E"}}
    assert create_mod._brief_get(brief, "palette.surface") is None
    assert create_mod._brief_get(brief, "shape_language") is None


def test_brief_get_quiet_on_non_dict_intermediate():
    brief = {"palette": "not a dict"}
    assert create_mod._brief_get(brief, "palette.accent") is None


def test_brief_get_returns_list_values():
    brief = {"palette": {"category_set": ["#AAA", "#BBB"]}}
    assert create_mod._brief_get(brief, "palette.category_set") == ["#AAA", "#BBB"]


# ---------------------------------------------------------------------------
# 3col_pill_cards — brief fills pill_palette + title_accent_hex
# ---------------------------------------------------------------------------


def test_3col_brief_fills_pill_palette(sub_primary):
    brief = {
        "palette": {
            "category_set": ["#DB4437", "#0F9D58", "#4285F4"],
            "accent": "#E8612E",
        }
    }
    content = {
        "title": "Three pillars",
        "columns": [
            {"pill": "A", "body": "aa"},
            {"pill": "B", "body": "bb"},
            {"pill": "C", "body": "cc"},
        ],
    }
    reqs, _ = create_mod.build_slide_requests(
        "slide_1", "3col_pill_cards", content, sub_primary, brief=brief
    )
    fills = _fill_colors_in_reqs(reqs)
    # All 3 category_set colors should appear as pill fills
    assert _contains_color(fills, "#DB4437")
    assert _contains_color(fills, "#0F9D58")
    assert _contains_color(fills, "#4285F4")


def test_3col_per_slide_pill_palette_wins_over_brief(sub_primary):
    brief = {"palette": {"category_set": ["#000000", "#000000", "#000000"]}}
    content = {
        "title": "Override",
        "pill_palette": ["#FFAA00", "#00AAFF", "#AA00FF"],
        "columns": [
            {"pill": "A", "body": "a"},
            {"pill": "B", "body": "b"},
            {"pill": "C", "body": "c"},
        ],
    }
    reqs, _ = create_mod.build_slide_requests(
        "s1", "3col_pill_cards", content, sub_primary, brief=brief
    )
    fills = _fill_colors_in_reqs(reqs)
    # Per-slide palette wins — black from brief should NOT be dominant
    assert _contains_color(fills, "#FFAA00")
    assert _contains_color(fills, "#00AAFF")
    assert _contains_color(fills, "#AA00FF")


def test_3col_per_column_pill_hex_wins_over_brief(sub_primary):
    brief = {"palette": {"category_set": ["#111111", "#222222", "#333333"]}}
    content = {
        "title": "Column override",
        "columns": [
            {"pill": "A", "body": "a", "pill_hex": "#FF0000"},
            {"pill": "B", "body": "b"},  # uses brief palette[1]
            {"pill": "C", "body": "c"},  # uses brief palette[2]
        ],
    }
    reqs, _ = create_mod.build_slide_requests(
        "s1", "3col_pill_cards", content, sub_primary, brief=brief
    )
    fills = _fill_colors_in_reqs(reqs)
    assert _contains_color(fills, "#FF0000")  # per-column wins
    assert _contains_color(fills, "#222222")  # brief palette[1]
    assert _contains_color(fills, "#333333")  # brief palette[2]


def test_3col_brief_fills_title_accent_hex(sub_primary):
    brief = {"palette": {"accent": "#C2185B"}}
    content = {
        "title": "Brief accent",
        "columns": [{"pill": "A", "body": "a"}],
    }
    reqs, _ = create_mod.build_slide_requests(
        "s1", "3col_pill_cards", content, sub_primary, brief=brief
    )
    fills = _fill_colors_in_reqs(reqs)
    assert _contains_color(fills, "#C2185B")


def test_3col_per_slide_title_accent_wins_over_brief(sub_primary):
    brief = {"palette": {"accent": "#000000"}}
    content = {
        "title": "Override accent",
        "title_accent_hex": "#FFAA00",
        "columns": [{"pill": "A", "body": "a"}],
    }
    reqs, _ = create_mod.build_slide_requests(
        "s1", "3col_pill_cards", content, sub_primary, brief=brief
    )
    fills = _fill_colors_in_reqs(reqs)
    assert _contains_color(fills, "#FFAA00")


# ---------------------------------------------------------------------------
# cover_with_hero — brief fills title + subtitle color
# ---------------------------------------------------------------------------


def test_cover_with_hero_brief_fills_title_color(sub_primary):
    brief = {"palette": {"accent": "#E8612E", "text": "#000000"}}
    content = {"title": "Cover", "subtitle": "Sub"}
    reqs, _ = create_mod.build_slide_requests(
        "s1", "cover_with_hero", content, sub_primary, brief=brief
    )
    text_colors = _text_colors_in_reqs(reqs)
    # Title colored with accent (#E8612E), subtitle with text (#000000)
    assert _contains_color(text_colors, "#E8612E")
    # subtitle #000000 → all three channels zero; _approx handles omitted


def test_cover_with_hero_per_slide_title_color_wins(sub_primary):
    brief = {"palette": {"accent": "#000000"}}
    content = {
        "title": "Cover",
        "subtitle": "Sub",
        "title_color_hex": "#FFFFFF",
    }
    reqs, _ = create_mod.build_slide_requests(
        "s1", "cover_with_hero", content, sub_primary, brief=brief
    )
    text_colors = _text_colors_in_reqs(reqs)
    assert _contains_color(text_colors, "#FFFFFF")


# ---------------------------------------------------------------------------
# text_left_image_right — brief fills accent bar + body color
# ---------------------------------------------------------------------------


def test_tlir_brief_fills_accent_bar(sub_primary):
    brief = {"palette": {"accent": "#E8612E"}}
    content = {"title": "TLIR", "body": "body text"}
    reqs, _ = create_mod.build_slide_requests(
        "s1", "text_left_image_right", content, sub_primary, brief=brief
    )
    fills = _fill_colors_in_reqs(reqs)
    # The accent bar is emitted as a RECTANGLE with the accent color fill
    assert _contains_color(fills, "#E8612E")


def test_tlir_brief_fills_body_text_color(sub_primary):
    brief = {"palette": {"text": "#446677"}}
    content = {"title": "TLIR", "body": "body text"}
    reqs, _ = create_mod.build_slide_requests(
        "s1", "text_left_image_right", content, sub_primary, brief=brief
    )
    text_colors = _text_colors_in_reqs(reqs)
    assert _contains_color(text_colors, "#446677")


def test_tlir_per_slide_accent_wins_over_brief(sub_primary):
    brief = {"palette": {"accent": "#000000"}}
    content = {
        "title": "TLIR",
        "body": "body",
        "accent_color_hex": "#FFAA00",
    }
    reqs, _ = create_mod.build_slide_requests(
        "s1", "text_left_image_right", content, sub_primary, brief=brief
    )
    fills = _fill_colors_in_reqs(reqs)
    assert _contains_color(fills, "#FFAA00")


# ---------------------------------------------------------------------------
# 4col_numbered_flow — brief fills numbers_palette + separator accent
# ---------------------------------------------------------------------------


def test_4col_brief_fills_numbers_palette(sub_primary):
    brief = {
        "palette": {
            "category_set": ["#AA1111", "#22BB22", "#3333CC", "#DD77AA"],
            "accent": "#888888",
        }
    }
    content = {
        "title": "4col",
        "columns": [
            {"num": "01", "subtitle": "A", "body": "aa"},
            {"num": "02", "subtitle": "B", "body": "bb"},
            {"num": "03", "subtitle": "C", "body": "cc"},
            {"num": "04", "subtitle": "D", "body": "dd"},
        ],
    }
    reqs, _ = create_mod.build_slide_requests(
        "s1", "4col_numbered_flow", content, sub_primary, brief=brief
    )
    text_colors = _text_colors_in_reqs(reqs)
    # Each num renders with its palette color via updateTextStyle
    assert _contains_color(text_colors, "#AA1111")
    assert _contains_color(text_colors, "#22BB22")
    assert _contains_color(text_colors, "#3333CC")
    assert _contains_color(text_colors, "#DD77AA")


def test_4col_brief_accent_fills_separator(sub_primary):
    brief = {"palette": {"accent": "#E8612E"}}
    content = {
        "title": "4col",
        "columns": [
            {"num": "01", "subtitle": "A", "body": "a"},
            {"num": "02", "subtitle": "B", "body": "b"},
        ],
    }
    reqs, _ = create_mod.build_slide_requests(
        "s1", "4col_numbered_flow", content, sub_primary, brief=brief
    )
    fills = _fill_colors_in_reqs(reqs)
    # Separator is a thin RECTANGLE filled with accent
    assert _contains_color(fills, "#E8612E")


def test_4col_per_slide_separator_color_wins(sub_primary):
    brief = {"palette": {"accent": "#000000"}}
    content = {
        "title": "4col",
        "separator_color_hex": "#FFAA00",
        "columns": [
            {"num": "01", "subtitle": "A", "body": "a"},
            {"num": "02", "subtitle": "B", "body": "b"},
        ],
    }
    reqs, _ = create_mod.build_slide_requests(
        "s1", "4col_numbered_flow", content, sub_primary, brief=brief
    )
    fills = _fill_colors_in_reqs(reqs)
    assert _contains_color(fills, "#FFAA00")


# ---------------------------------------------------------------------------
# Backward compatibility — brief=None preserves Phase-1 behavior
# ---------------------------------------------------------------------------


def test_brief_none_matches_pre_phase2_for_3col(sub_primary):
    content = {
        "title": "Default",
        "columns": [
            {"pill": "A", "body": "a"},
            {"pill": "B", "body": "b"},
            {"pill": "C", "body": "c"},
        ],
    }
    reqs_with_none, _ = create_mod.build_slide_requests(
        "s1", "3col_pill_cards", content, sub_primary, brief=None
    )
    reqs_no_brief_kwarg, _ = create_mod.build_slide_requests(
        "s1", "3col_pill_cards", content, sub_primary
    )
    # Same request count + same kinds (we don't assert byte-identity because
    # _new_id() generates fresh UUIDs per call).
    assert len(reqs_with_none) == len(reqs_no_brief_kwarg)


def test_brief_none_falls_through_to_theme_fallback_for_3col(sub_primary):
    """Empty palette brief should behave like no brief."""
    brief_empty = {"palette": {}}
    content = {
        "title": "Default",
        "columns": [{"pill": "A", "body": "a"}],
    }
    reqs_empty, _ = create_mod.build_slide_requests(
        "s1", "3col_pill_cards", content, sub_primary, brief=brief_empty
    )
    reqs_none, _ = create_mod.build_slide_requests(
        "s1", "3col_pill_cards", content, sub_primary, brief=None
    )
    assert len(reqs_empty) == len(reqs_none)


def test_brief_without_palette_field_quiet(sub_primary):
    """Brief with tone/shape_language but no palette — shouldn't crash."""
    brief = {"tone": "warm tech", "shape_language": "rounded"}
    content = {"title": "Hi", "paragraphs": ["hello"]}
    reqs, _ = create_mod.build_slide_requests(
        "s1", "text_heavy_body", content, sub_primary, brief=brief
    )
    # No error; reqs produced
    assert len(reqs) > 0
