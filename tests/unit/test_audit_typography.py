"""Unit tests for audit_typography + supporting TypographyReport helpers."""
from __future__ import annotations

from slides_mcp.audit import (
    _brief_palette_hexes,
    _nearest_brief_role,
    _theme_font_role_for_size,
    audit_typography,
)
from slides_mcp.normalize import normalize_page
from slides_mcp.theme import load_theme
from tests.fixtures import (
    page as page_fx,
)
from tests.fixtures import (
    slide_3col_pill_cards,
    slide_text_heavy,
    textbox,
)


def _slide_shapes(*pages):
    return [(p["objectId"], normalize_page(p)) for p in pages]


# -----------------------------------------------------------------
# _brief_palette_hexes
# -----------------------------------------------------------------

def test_brief_palette_hexes_empty_on_none():
    assert _brief_palette_hexes(None) == {}
    assert _brief_palette_hexes({}) == {}


def test_brief_palette_hexes_flattens_roles_and_category_set():
    brief = {
        "palette": {
            "surface": "#0F1A4A",
            "accent": "#E8612E",
            "text": "#000000",
            "category_set": ["#E8612E", "#0F1A4A", "#888888"],
        },
    }
    flat = _brief_palette_hexes(brief)
    assert flat["accent"] == "#E8612E"
    assert flat["surface"] == "#0F1A4A"
    assert flat["text"] == "#000000"
    assert flat["category_0"] == "#E8612E"
    assert flat["category_2"] == "#888888"


def test_brief_palette_hexes_skips_bad_values():
    brief = {"palette": {"accent": "not-hex", "text": 42, "category_set": ["#111111", None, 7]}}
    flat = _brief_palette_hexes(brief)
    assert "accent" not in flat
    assert "text" not in flat
    assert flat["category_0"] == "#111111"
    assert "category_1" not in flat  # None skipped


# -----------------------------------------------------------------
# _nearest_brief_role
# -----------------------------------------------------------------

def test_nearest_brief_role_exact_match_distance_zero():
    hexes = {"accent": "#E8612E"}
    role, hx, d = _nearest_brief_role("#E8612E", hexes)
    assert role == "accent"
    assert hx == "#E8612E"
    assert d == 0


def test_nearest_brief_role_picks_closest_of_many():
    hexes = {"accent": "#FF0000", "surface": "#00FF00", "text": "#0000FF"}
    # pure red-ish input → accent wins
    role, _, _ = _nearest_brief_role("#F00000", hexes)
    assert role == "accent"


def test_nearest_brief_role_empty_hexes_returns_none():
    role, hx, d = _nearest_brief_role("#123456", {})
    assert role is None and hx is None
    assert d == 9999


# -----------------------------------------------------------------
# _theme_font_role_for_size
# -----------------------------------------------------------------

def test_theme_font_role_for_size_matches_display():
    sub = load_theme("example").sub("primary")
    # example theme has a 'display' role around 36pt; test_3col pill uses 36pt bold.
    role = _theme_font_role_for_size(36.0, sub)
    assert role in sub.fonts  # matches *some* role within 1pt


def test_theme_font_role_for_size_returns_unknown_for_outlier():
    sub = load_theme("example").sub("primary")
    assert _theme_font_role_for_size(7.0, sub) == "unknown"  # nothing at 7pt


# -----------------------------------------------------------------
# audit_typography — full integration
# -----------------------------------------------------------------

def test_audit_typography_dominant_font_matches_majority():
    sub = load_theme("example").sub("primary")
    report = audit_typography(_slide_shapes(slide_3col_pill_cards()), sub)
    # slide_3col_pill_cards uses Inter everywhere
    assert report.dominant_font == "Inter"
    assert report.font_outliers == []


def test_audit_typography_flags_font_outliers():
    # Mix Inter + Calibri pollution
    mixed = page_fx("mixed", [
        textbox("t1", "Title", 0.5, 0.5, 10, 0.8, font="Inter", size_pt=36),
        textbox("b1", "body inter", 0.5, 1.5, 10, 0.8, font="Inter", size_pt=14),
        textbox("b2", "body inter", 0.5, 2.5, 10, 0.8, font="Inter", size_pt=14),
        textbox("b3", "body inter", 0.5, 3.5, 10, 0.8, font="Inter", size_pt=14),
        textbox("b4", "body inter", 0.5, 4.5, 10, 0.8, font="Inter", size_pt=14),
        textbox("b5", "body inter", 0.5, 5.5, 10, 0.8, font="Inter", size_pt=14),
        textbox("b6", "body inter", 0.5, 6.5, 10, 0.8, font="Inter", size_pt=14),
        textbox("b7", "body inter", 0.5, 7.5, 10, 0.8, font="Inter", size_pt=14),
        textbox("b8", "body inter", 0.5, 8.5, 10, 0.8, font="Inter", size_pt=14),
        textbox("orphan", "pasted from word", 0.5, 9.5, 10, 0.8,
                font="Calibri", size_pt=11),
    ])
    sub = load_theme("example").sub("primary")
    report = audit_typography(_slide_shapes(mixed), sub)
    assert report.dominant_font == "Inter"
    outlier_families = [o.family for o in report.font_outliers]
    assert "Calibri" in outlier_families


def test_audit_typography_detects_orphan_size():
    # 10 runs at 14pt, 1 run at 11pt → 11pt is orphan (<5%).
    page = page_fx("sizes", [
        textbox(f"b{i}", f"body {i}", 0.5, i * 0.5, 10, 0.4,
                font="Inter", size_pt=14)
        for i in range(20)
    ] + [
        textbox("orphan", "one-off", 0.5, 11, 10, 0.4, font="Inter", size_pt=11),
    ])
    sub = load_theme("example").sub("primary")
    report = audit_typography(_slide_shapes(page), sub)
    sizes = {c.size_pt: c for c in report.size_clusters}
    assert 14.0 in sizes
    assert 11.0 in sizes
    # 11pt is exactly 1/21 = ~4.8% → orphan (<5% threshold)
    assert sizes[11.0].role_guess == "orphan"


def test_audit_typography_detects_orphan_bolds():
    # A single shape with 2 runs: one bold word inside a mostly-plain shape.
    # Needs mixed runs within one textbox — fixtures only emit one run per
    # textbox, so we hand-build.
    page = {
        "objectId": "orphb",
        "pageElements": [
            {
                "objectId": "mixed_shape",
                "size": {"width": {"magnitude": 5 * 914400, "unit": "EMU"},
                         "height": {"magnitude": 1 * 914400, "unit": "EMU"}},
                "transform": {"scaleX": 1, "scaleY": 1,
                              "translateX": 914400, "translateY": 914400,
                              "unit": "EMU"},
                "shape": {
                    "shapeType": "TEXT_BOX",
                    "text": {"textElements": [
                        {"textRun": {"content": "plain", "style": {"fontFamily": "Inter",
                                     "fontSize": {"magnitude": 14, "unit": "PT"}}}},
                        {"textRun": {"content": "bold-word", "style": {
                            "fontFamily": "Inter",
                            "fontSize": {"magnitude": 14, "unit": "PT"},
                            "bold": True}}},
                        {"textRun": {"content": "more plain", "style": {
                            "fontFamily": "Inter",
                            "fontSize": {"magnitude": 14, "unit": "PT"}}}},
                    ]},
                },
            }
        ],
    }
    sub = load_theme("example").sub("primary")
    report = audit_typography(_slide_shapes(page), sub)
    assert len(report.orphan_bolds) == 1
    ob = report.orphan_bolds[0]
    assert ob.slide_id == "orphb"
    assert ob.object_id == "mixed_shape"
    assert ob.run_index == 1
    assert "bold-word" in ob.text_preview


def test_audit_typography_color_drift_vs_brief():
    # A brief with accent=#E8612E; slide uses a rogue red.
    page = page_fx("drift", [
        textbox("t", "title", 0.5, 0.5, 10, 0.8,
                font="Inter", size_pt=36, color_hex="#CC0000"),
        textbox("b", "body", 0.5, 1.5, 10, 0.8,
                font="Inter", size_pt=14, color_hex="#000000"),
    ])
    sub = load_theme("example").sub("primary")
    brief = {"palette": {"accent": "#E8612E", "text": "#000000"}}
    report = audit_typography(_slide_shapes(page), sub, brief=brief)
    assert report.brief_applied is True
    drift_hexes = [d.hex_value for d in report.color_drifts_vs_brief]
    # The rogue red is >60 RGB distance from both accent + text → drift.
    assert "#CC0000" in drift_hexes
    # Pure black text matches brief.text exactly → no drift.
    assert "#000000" not in drift_hexes


def test_audit_typography_no_brief_skips_color_drift():
    # With no brief, color_drifts_vs_brief is empty.
    shapes = _slide_shapes(slide_text_heavy())
    sub = load_theme("example").sub("primary")
    report = audit_typography(shapes, sub)
    assert report.brief_applied is False
    assert report.color_drifts_vs_brief == []


def test_audit_typography_counts_totals():
    shapes = _slide_shapes(slide_3col_pill_cards())
    sub = load_theme("example").sub("primary")
    report = audit_typography(shapes, sub)
    assert report.total_text_runs > 0
    assert report.total_text_shapes > 0
    # fixture has title + lead + 3 pills + 3 bodies = 8 text shapes
    # (cards are shape_with_fill with no text runs — not counted)
    assert report.total_text_shapes == 8
