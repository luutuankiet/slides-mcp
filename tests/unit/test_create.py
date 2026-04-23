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


def _pill_shapes(reqs: list[dict]) -> list[dict]:
    """Return the createShape requests whose shapeType is ROUND_RECTANGLE
    (i.e. the pills — distinct from title accent rects and column dot ellipses).
    """
    return [r["createShape"] for r in reqs
            if "createShape" in r and r["createShape"].get("shapeType") == "ROUND_RECTANGLE"]


def _fill_for_objectid(reqs: list[dict], oid: str) -> dict | None:
    for r in reqs:
        usp = r.get("updateShapeProperties")
        if usp and usp.get("objectId") == oid:
            return usp
    return None


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
    # Visual extras — createShape count:
    #   title(1) + title_bar(1) + lead(1) + (dot+pill+body)*3 = 12
    # updateShapeProperties count:
    #   title_bar fill(1) + dot fills(3) + pill fills(3) = 7
    # insertText: 2 (title, lead) + 3*2 (pill + body per col) = 8
    assert kinds.count("createShape") == 12
    assert kinds.count("updateShapeProperties") == 7
    assert kinds.count("insertText") == 8
    # Exactly 3 ROUND_RECTANGLE pills
    assert len(_pill_shapes(reqs)) == 3


def test_3col_pill_cards_partial_columns(sub_primary):
    content = {
        "title": "Partial",
        "columns": [{"pill": "Only one", "body": "Just this."}],
    }
    reqs, _ = create_mod.build_slide_requests(
        slide_id="s_x", archetype_name="3col_pill_cards", content=content, sub=sub_primary
    )
    kinds = _request_kinds(reqs)
    # title(1) + title_bar(1) + dot(1) + pill(1) + body(1) = 5 createShape
    # fills: title_bar(1) + dot(1) + pill(1) = 3 updateShapeProperties
    assert kinds.count("createShape") == 5
    assert kinds.count("updateShapeProperties") == 3
    assert len(_pill_shapes(reqs)) == 1


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
    # Only 3 pills regardless of content overflow
    assert len(_pill_shapes(reqs)) == 3


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
    """Default path — no per-col pill_hex, no pill_palette → all pills fall
    back to theme's brand_accent (#3366CC)."""
    content = {
        "title": "x",
        "columns": [{"pill": "p", "body": "b"}],
    }
    reqs, _ = create_mod.build_slide_requests(
        slide_id="s_x", archetype_name="3col_pill_cards", content=content, sub=sub_primary
    )
    pills = _pill_shapes(reqs)
    assert len(pills) == 1
    fill = _fill_for_objectid(reqs, pills[0]["objectId"])
    assert fill is not None
    rgb = fill["shapeProperties"]["shapeBackgroundFill"]["solidFill"]["color"]["rgbColor"]
    # example.primary brand_accent = #3366CC → (0.2, 0.4, 0.8)
    assert abs(rgb["red"] - 0.2) < 0.01
    assert abs(rgb["green"] - 0.4) < 0.01
    assert abs(rgb["blue"] - 0.8) < 0.01


def test_pill_palette_rotates_colors_across_columns(sub_primary):
    """Agent passes content['pill_palette'] → each pill gets the corresponding
    hex from the palette in column order. Content-driven, no theme edit."""
    content = {
        "title": "Visual story",
        "pill_palette": ["#DB4437", "#0F9D58", "#4285F4"],  # red / green / blue
        "columns": [
            {"pill": "A", "body": "a"},
            {"pill": "B", "body": "b"},
            {"pill": "C", "body": "c"},
        ],
    }
    reqs, _ = create_mod.build_slide_requests(
        slide_id="s_x", archetype_name="3col_pill_cards", content=content, sub=sub_primary
    )
    pills = _pill_shapes(reqs)
    assert len(pills) == 3
    expected_rgbs = [
        (0xDB / 255, 0x44 / 255, 0x37 / 255),  # col 0 — red
        (0x0F / 255, 0x9D / 255, 0x58 / 255),  # col 1 — green
        (0x42 / 255, 0x85 / 255, 0xF4 / 255),  # col 2 — blue
    ]
    for pill, (er, eg, eb) in zip(pills, expected_rgbs, strict=True):
        fill = _fill_for_objectid(reqs, pill["objectId"])
        assert fill is not None
        rgb = fill["shapeProperties"]["shapeBackgroundFill"]["solidFill"]["color"]["rgbColor"]
        assert abs(rgb["red"] - er) < 0.01
        assert abs(rgb["green"] - eg) < 0.01
        assert abs(rgb["blue"] - eb) < 0.01


def test_per_column_pill_hex_overrides_palette(sub_primary):
    """col['pill_hex'] wins over content['pill_palette']."""
    content = {
        "title": "t",
        "pill_palette": ["#000000", "#000000", "#000000"],  # black palette
        "columns": [
            {"pill": "A", "body": "a", "pill_hex": "#DB4437"},  # override → red
            {"pill": "B", "body": "b"},                           # → palette → black
            {"pill": "C", "body": "c", "pill_hex": "#4285F4"},  # override → blue
        ],
    }
    reqs, _ = create_mod.build_slide_requests(
        slide_id="s_x", archetype_name="3col_pill_cards", content=content, sub=sub_primary
    )
    pills = _pill_shapes(reqs)
    assert len(pills) == 3
    fills = [_fill_for_objectid(reqs, p["objectId"]) for p in pills]
    # col 0 overridden to #DB4437
    rgb0 = fills[0]["shapeProperties"]["shapeBackgroundFill"]["solidFill"]["color"]["rgbColor"]
    assert abs(rgb0["red"] - 0xDB / 255) < 0.01
    # col 1 → palette[1] = black → rgbColor will be omitted for 0 values
    rgb1 = fills[1]["shapeProperties"]["shapeBackgroundFill"]["solidFill"]["color"]["rgbColor"]
    assert rgb1.get("red", 0) == 0
    # col 2 overridden to #4285F4
    rgb2 = fills[2]["shapeProperties"]["shapeBackgroundFill"]["solidFill"]["color"]["rgbColor"]
    assert abs(rgb2["blue"] - 0xF4 / 255) < 0.01


def test_pill_shape_is_rounded(sub_primary):
    """MVP visual rhythm — pills use ROUND_RECTANGLE (not plain RECTANGLE)
    for a friendlier look."""
    content = {
        "title": "t",
        "columns": [{"pill": "p", "body": "b"}],
    }
    reqs, _ = create_mod.build_slide_requests(
        slide_id="s_x", archetype_name="3col_pill_cards", content=content, sub=sub_primary
    )
    pills = _pill_shapes(reqs)
    assert len(pills) == 1
    assert pills[0]["shapeType"] == "ROUND_RECTANGLE"


def test_column_dot_and_title_accent_emitted(sub_primary):
    """Archetype emits visual extras: 1 title-accent bar + 1 dot per column."""
    content = {
        "title": "t",
        "pill_palette": ["#DB4437", "#0F9D58", "#4285F4"],
        "columns": [
            {"pill": "A", "body": "a"},
            {"pill": "B", "body": "b"},
            {"pill": "C", "body": "c"},
        ],
    }
    reqs, _ = create_mod.build_slide_requests(
        slide_id="s_x", archetype_name="3col_pill_cards", content=content, sub=sub_primary
    )
    ellipses = [r["createShape"] for r in reqs
                if "createShape" in r and r["createShape"].get("shapeType") == "ELLIPSE"]
    assert len(ellipses) == 3, "expected 3 column-dot ellipses"
    plain_rects = [r["createShape"] for r in reqs
                   if "createShape" in r and r["createShape"].get("shapeType") == "RECTANGLE"]
    assert len(plain_rects) == 1, "expected 1 title-accent bar rectangle"


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


# ---------------------------------------------------------------------------
# Deck-size scaling (regression guard for the 10×5.625 clipping bug)
# ---------------------------------------------------------------------------


def _first_create_shape(reqs: list[dict]) -> dict:
    for r in reqs:
        if "createShape" in r:
            return r["createShape"]
    raise AssertionError("no createShape request found")


def _element_dims_in(create_shape_req: dict) -> tuple[float, float, float, float]:
    """Return (left_in, top_in, w_in, h_in) from a createShape request."""
    ep = create_shape_req["elementProperties"]
    w = ep["size"]["width"]["magnitude"] / create_mod._EMU_PER_INCH
    h = ep["size"]["height"]["magnitude"] / create_mod._EMU_PER_INCH
    left = ep["transform"]["translateX"] / create_mod._EMU_PER_INCH
    top = ep["transform"]["translateY"] / create_mod._EMU_PER_INCH
    return left, top, w, h


def test_default_deck_dims_emit_reference_geometry(sub_primary):
    """Omitting deck dims → 16×9 reference → archetype YAML values land verbatim."""
    content = {"title": "x", "paragraphs": ["y"]}
    reqs, _ = create_mod.build_slide_requests(
        slide_id="s", archetype_name="text_heavy_body", content=content, sub=sub_primary
    )
    # text_heavy_body title at 0.5, 0.5, 15.0, 0.8 per YAML
    left, top, w, h = _element_dims_in(_first_create_shape(reqs))
    assert abs(left - 0.5) < 0.001
    assert abs(top - 0.5) < 0.001
    assert abs(w - 15.0) < 0.001
    assert abs(h - 0.8) < 0.001


def test_10x5_625_deck_scales_geometry_to_fit(sub_primary):
    """Google default 10×5.625 (same 16:9 aspect) → all coords multiplied by 0.625."""
    content = {"title": "x", "paragraphs": ["y"]}
    reqs, _ = create_mod.build_slide_requests(
        slide_id="s",
        archetype_name="text_heavy_body",
        content=content,
        sub=sub_primary,
        deck_width_in=10.0,
        deck_height_in=5.625,
    )
    left, top, w, h = _element_dims_in(_first_create_shape(reqs))
    # sx = 10/16 = 0.625, sy = 5.625/9 = 0.625
    assert abs(left - 0.5 * 0.625) < 0.001
    assert abs(top - 0.5 * 0.625) < 0.001
    assert abs(w - 15.0 * 0.625) < 0.001  # 9.375 — fits in 10-wide deck
    assert abs(h - 0.8 * 0.625) < 0.001


def test_10x5_625_deck_keeps_pill_cards_on_slide(sub_primary):
    """3col_pill_cards on a 10in-wide deck: col_3 must not clip (regression guard)."""
    content = {
        "title": "t",
        "columns": [
            {"pill": "a", "body": "A"},
            {"pill": "b", "body": "B"},
            {"pill": "c", "body": "C"},
        ],
    }
    reqs, _ = create_mod.build_slide_requests(
        slide_id="s",
        archetype_name="3col_pill_cards",
        content=content,
        sub=sub_primary,
        deck_width_in=10.0,
        deck_height_in=5.625,
    )
    # Find every createShape, confirm right edge ≤ 10.0 and bottom edge ≤ 5.625
    for r in reqs:
        if "createShape" not in r:
            continue
        left, top, w, h = _element_dims_in(r["createShape"])
        assert left + w <= 10.0 + 0.01, f"shape clips right: left={left} w={w}"
        assert top + h <= 5.625 + 0.01, f"shape clips bottom: top={top} h={h}"


def test_13_33x7_5_deck_also_scales(sub_primary):
    """Common widescreen 13.33×7.5 deck → scale 0.833."""
    content = {"title": "x", "paragraphs": ["y"]}
    reqs, _ = create_mod.build_slide_requests(
        slide_id="s",
        archetype_name="text_heavy_body",
        content=content,
        sub=sub_primary,
        deck_width_in=13.333,
        deck_height_in=7.5,
    )
    left, _, w, _ = _element_dims_in(_first_create_shape(reqs))
    assert abs(w - 15.0 * (13.333 / 16.0)) < 0.01
    # right edge (left + w) must fit in 13.333
    assert left + w <= 13.333 + 0.01


def test_pill_text_color_emits_opaque_white(sub_primary):
    """Pill header text MUST carry foregroundColor = #FFFFFF (white) so it's
    readable on the brand_accent fill. Regression guard against the observed
    'dark text on dark pill' cap_theme bug — create.py's code path must
    unambiguously resolve to opaque white, not Slides-API-defaulted empty."""
    content = {
        "title": "t",
        "columns": [{"pill": "Pill text", "body": "Body text"}],
    }
    reqs, _ = create_mod.build_slide_requests(
        slide_id="s", archetype_name="3col_pill_cards", content=content, sub=sub_primary
    )
    pills = _pill_shapes(reqs)
    assert len(pills) == 1, "expected exactly 1 pill"
    pill_oid = pills[0]["objectId"]
    pill_style = None
    for r in reqs:
        uts = r.get("updateTextStyle")
        if uts and uts.get("objectId") == pill_oid:
            pill_style = uts
            break
    assert pill_style is not None, "pill shape has no updateTextStyle"
    style = pill_style["style"]
    assert "foregroundColor" in style
    rgb = style["foregroundColor"]["opaqueColor"]["rgbColor"]
    # White: 1.0, 1.0, 1.0
    assert rgb["red"] == 1.0
    assert rgb["green"] == 1.0
    assert rgb["blue"] == 1.0
    # fields mask must include foregroundColor so the style lands
    assert "foregroundColor" in pill_style["fields"]
