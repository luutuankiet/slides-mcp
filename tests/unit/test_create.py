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
    assert "text_left_image_right" in supp  # LOG-016 Step 5
    assert "4col_numbered_flow" in supp  # LOG-016 Step 7


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


# ---------------------------------------------------------------------------
# cover_with_hero rewrite (LOG-016 Step 6)
# ---------------------------------------------------------------------------


def test_cover_with_hero_url_mode_emits_createimage_plus_text(sub_primary):
    """hero with url → createImage request for the image slot + 2 text slots
    for title/subtitle. Hero is emitted first for z-order."""
    content = {
        "title": "Agentic analytics",
        "subtitle": "From insight to impact",
        "hero": {"url": "https://example.test/cover.jpg", "side": "left"},
    }
    reqs, warnings = create_mod.build_slide_requests(
        slide_id="s", archetype_name="cover_with_hero",
        content=content, sub=sub_primary,
    )
    assert warnings == []
    imgs = _create_image_reqs(reqs)
    assert len(imgs) == 1
    assert imgs[0]["url"] == "https://example.test/cover.jpg"
    # Hero is first in the request list (z-order: back)
    for i, r in enumerate(reqs):
        if "createImage" in r:
            # all text createShapes come AFTER this index
            for j in range(i):
                assert "createImage" not in reqs[j]
            break


def test_cover_with_hero_placeholder_mode_works(sub_primary):
    """hero with prompt → RECTANGLE placeholder with [IMAGE: ...] text."""
    content = {
        "title": "Launch partners",
        "hero": {"prompt": "team photo with branded backdrop", "side": "right"},
    }
    reqs, _ = create_mod.build_slide_requests(
        slide_id="s", archetype_name="cover_with_hero",
        content=content, sub=sub_primary,
    )
    assert len(_create_image_reqs(reqs)) == 0
    placeholder_texts = [
        ins["text"] for ins in _insert_text_reqs(reqs)
        if "[IMAGE:" in ins.get("text", "")
    ]
    assert placeholder_texts == ["[IMAGE: team photo with branded backdrop]"]


def test_cover_with_hero_side_left_vs_right_swaps_text_horizontal_position(sub_primary):
    """side='left' puts text on the right, side='right' puts text on the left."""
    base = {"title": "t", "subtitle": "s", "hero": {"url": "https://x/y.jpg"}}

    reqs_left, _ = create_mod.build_slide_requests(
        slide_id="s", archetype_name="cover_with_hero",
        content={**base, "hero": {**base["hero"], "side": "left"}}, sub=sub_primary,
    )
    reqs_right, _ = create_mod.build_slide_requests(
        slide_id="s", archetype_name="cover_with_hero",
        content={**base, "hero": {**base["hero"], "side": "right"}}, sub=sub_primary,
    )

    # Title is the first createShape after the hero image
    title_left_req = next(r["createShape"] for r in reqs_left if "createShape" in r)
    title_right_req = next(r["createShape"] for r in reqs_right if "createShape" in r)
    tx_left = title_left_req["elementProperties"]["transform"]["translateX"]
    tx_right = title_right_req["elementProperties"]["transform"]["translateX"]
    # side='left' → hero on left → title on right (large X)
    # side='right' → hero on right → title on left (small X)
    assert tx_left > tx_right


def test_cover_with_hero_fullbleed_spans_full_16x9(sub_primary):
    """side='fullbleed' → hero image covers the entire 16×9 archetype frame,
    and the text block overlays centered."""
    content = {
        "title": "Q2 planning",
        "hero": {"url": "https://example.test/bg.jpg", "side": "fullbleed"},
    }
    reqs, _ = create_mod.build_slide_requests(
        slide_id="s", archetype_name="cover_with_hero",
        content=content, sub=sub_primary,
    )
    img = _create_image_reqs(reqs)[0]
    size = img["elementProperties"]["size"]
    transform = img["elementProperties"]["transform"]
    # Hero origin at 0,0
    assert transform["translateX"] == 0
    assert transform["translateY"] == 0
    # Hero size = 16×9 inches in EMU
    assert size["width"]["magnitude"] == int(16.0 * 914400)
    assert size["height"]["magnitude"] == int(9.0 * 914400)


def test_cover_with_hero_content_driven_text_colors(sub_primary):
    """title_color_hex + subtitle_color_hex are emitted as updateTextStyle
    foregroundColor overrides. No hardcoded hex in the builder."""
    content = {
        "title": "Dark cover",
        "subtitle": "white on black",
        "hero": {"prompt": "dark cityscape", "side": "fullbleed"},
        "title_color_hex": "#FFFFFF",
        "subtitle_color_hex": "#CCCCCC",
    }
    reqs, _ = create_mod.build_slide_requests(
        slide_id="s", archetype_name="cover_with_hero",
        content=content, sub=sub_primary,
    )
    # Scan every updateTextStyle with foregroundColor
    styled = []
    for r in reqs:
        uts = r.get("updateTextStyle")
        if uts and "foregroundColor" in uts["fields"]:
            rgb = uts["style"]["foregroundColor"]["opaqueColor"]["rgbColor"]
            styled.append(rgb)
    # Should have exactly 2 color-styled runs (title + subtitle)
    assert len(styled) == 2
    # White: all 1.0
    whites = [s for s in styled if s.get("red") == 1.0 and s.get("green") == 1.0 and s.get("blue") == 1.0]
    assert len(whites) == 1
    # Light gray ~ 0.8 on all channels
    grays = [s for s in styled if round(s.get("red", 0), 1) == 0.8]
    assert len(grays) == 1


def test_cover_with_hero_no_hero_still_builds_title_subtitle(sub_primary):
    """Hero is optional: title + subtitle should still build without it."""
    content = {"title": "Text-only cover", "subtitle": "no image"}
    reqs, warnings = create_mod.build_slide_requests(
        slide_id="s", archetype_name="cover_with_hero",
        content=content, sub=sub_primary,
    )
    assert warnings == []
    assert len(_create_image_reqs(reqs)) == 0
    shapes = _create_shape_reqs(reqs)
    assert len(shapes) == 2  # title + subtitle


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


def test_updateshapeproperties_carries_autofit_none(sub_primary):
    """Regression guard for the 2026-04-23 autofit-batch-rejection bug.

    Google Slides API (post-2026-04 update) auto-applies a non-NONE autofit
    on text-containing shapes during insertText. Subsequent
    updateShapeProperties calls in the SAME batchUpdate then fail with
    'Autofit types other than NONE are not supported' even though the
    field mask never touches autofit.

    Mitigation: every updateShapeProperties emitted by `_build_text_slot`
    explicitly sets autofit.autofitType=NONE alongside the fill, with the
    field mask including 'autofit.autofitType'. This forces the shape into
    the supported state regardless of what Google's batch processor would
    have inferred.

    This test asserts the contract on EVERY updateShapeProperties emitted
    by 3col_pill_cards (which has the largest fan-out: title accent +
    3 dots + 3 pills = 7 fill updates).
    """
    content = {
        "title": "x",
        "columns": [
            {"pill": "p1", "body": "b1"},
            {"pill": "p2", "body": "b2"},
            {"pill": "p3", "body": "b3"},
        ],
    }
    reqs, warnings = create_mod.build_slide_requests(
        slide_id="s", archetype_name="3col_pill_cards", content=content, sub=sub_primary
    )
    assert warnings == []
    usp_reqs = [r["updateShapeProperties"] for r in reqs if "updateShapeProperties" in r]
    assert len(usp_reqs) == 7, "3col_pill_cards should emit 7 updateShapeProperties (title accent + 3 dots + 3 pills)"
    for usp in usp_reqs:
        assert "autofit.autofitType" in usp["fields"], (
            f"field mask {usp['fields']!r} missing autofit.autofitType — "
            f"will fail Google Slides API batch validation"
        )
        autofit = usp["shapeProperties"].get("autofit") or {}
        assert autofit.get("autofitType") == "NONE", (
            f"autofit.autofitType is {autofit.get('autofitType')!r}, "
            f"must be 'NONE' (only supported write value)"
        )


# ---------------------------------------------------------------------------
# text_left_image_right builder (LOG-016 Step 5)
# ---------------------------------------------------------------------------


def _create_shape_reqs(reqs: list[dict]) -> list[dict]:
    return [r["createShape"] for r in reqs if "createShape" in r]


def _create_image_reqs(reqs: list[dict]) -> list[dict]:
    return [r["createImage"] for r in reqs if "createImage" in r]


def _insert_text_reqs(reqs: list[dict]) -> list[dict]:
    return [r["insertText"] for r in reqs if "insertText" in r]


def _translate_x_for(reqs: list[dict], object_id: str) -> float | None:
    """Pull translateX (in EMU) of the given objectId from its createShape
    or createImage request. Returns None if not found."""
    for r in reqs:
        for kind in ("createShape", "createImage"):
            if kind in r and r[kind].get("objectId") == object_id:
                return r[kind]["elementProperties"]["transform"]["translateX"]
    return None


def test_text_left_image_right_happy_path_with_url(sub_primary):
    """URL mode: one createImage request with the given URL. Title + body
    emit their usual createShape + insertText + updateTextStyle bursts."""
    content = {
        "title": "Persona-driven insights",
        "body": "The agent tailors daily digests to each persona.",
        "image": {"url": "https://example.test/persona.jpg"},
    }
    reqs, warnings = create_mod.build_slide_requests(
        slide_id="s", archetype_name="text_left_image_right",
        content=content, sub=sub_primary,
    )
    assert warnings == []
    imgs = _create_image_reqs(reqs)
    assert len(imgs) == 1
    assert imgs[0]["url"] == "https://example.test/persona.jpg"
    # Title + body = 2 createShape text slots
    shapes = _create_shape_reqs(reqs)
    assert len(shapes) == 2
    inserts = _insert_text_reqs(reqs)
    # Title + body → 2 inserts. No placeholder text.
    assert len(inserts) == 2
    # No "[IMAGE:" marker since URL mode was used
    assert not any("[IMAGE:" in ins.get("text", "") for ins in inserts)


def test_text_left_image_right_placeholder_mode(sub_primary):
    """Placeholder mode: image slot emits createShape(RECTANGLE) + insertText
    with '[IMAGE: <prompt>]'. No createImage request."""
    content = {
        "title": "Daily digest",
        "body": "Morning summary of key metrics.",
        "image": {"prompt": "a morning dashboard on a phone screen"},
    }
    reqs, _ = create_mod.build_slide_requests(
        slide_id="s", archetype_name="text_left_image_right",
        content=content, sub=sub_primary,
    )
    imgs = _create_image_reqs(reqs)
    assert len(imgs) == 0  # no raster mode
    inserts = _insert_text_reqs(reqs)
    placeholder_texts = [ins["text"] for ins in inserts if "[IMAGE:" in ins["text"]]
    assert len(placeholder_texts) == 1
    assert placeholder_texts[0] == "[IMAGE: a morning dashboard on a phone screen]"


def test_text_left_image_right_accepts_bare_string_image_as_url(sub_primary):
    """Archetype YAML hint calls `image` a string. For back-compat the builder
    treats a bare string as a URL."""
    content = {
        "title": "x",
        "body": "y",
        "image": "https://example.test/a.png",
    }
    reqs, _ = create_mod.build_slide_requests(
        slide_id="s", archetype_name="text_left_image_right",
        content=content, sub=sub_primary,
    )
    imgs = _create_image_reqs(reqs)
    assert len(imgs) == 1
    assert imgs[0]["url"] == "https://example.test/a.png"


def test_text_left_image_right_joins_paragraphs_into_body(sub_primary):
    """`paragraphs` is joined with blank lines when `body` is absent."""
    content = {
        "title": "Multi-para",
        "paragraphs": ["Alpha.", "Beta.", "Gamma."],
        "image": {"prompt": "x"},
    }
    reqs, _ = create_mod.build_slide_requests(
        slide_id="s", archetype_name="text_left_image_right",
        content=content, sub=sub_primary,
    )
    body_insert = next(
        ins for ins in _insert_text_reqs(reqs)
        if "Alpha" in ins.get("text", "")
    )
    assert body_insert["text"] == "Alpha.\n\nBeta.\n\nGamma."


def test_text_left_image_right_image_side_swaps_horizontal_positions(sub_primary):
    """image_side='left' moves the image slot to the left edge and the text
    block to the right — swap happens at translateX level."""
    base_content = {"title": "t", "body": "b", "image": {"url": "https://x/y.jpg"}}

    # Default (right) — image on the right, text block on the left
    reqs_right, _ = create_mod.build_slide_requests(
        slide_id="s", archetype_name="text_left_image_right",
        content={**base_content, "image_side": "right"}, sub=sub_primary,
    )
    img_right = _create_image_reqs(reqs_right)[0]
    img_right_tx = img_right["elementProperties"]["transform"]["translateX"]
    # Image on right side: translateX should be > deck_midpoint (half of 16in = 8in in EMU)
    assert img_right_tx > int(7.0 * 914400)

    # Left — image on left, text on right
    reqs_left, _ = create_mod.build_slide_requests(
        slide_id="s", archetype_name="text_left_image_right",
        content={**base_content, "image_side": "left"}, sub=sub_primary,
    )
    img_left = _create_image_reqs(reqs_left)[0]
    img_left_tx = img_left["elementProperties"]["transform"]["translateX"]
    assert img_left_tx < int(2.0 * 914400)  # image on left edge (≤ 2in)
    # Swap invariant: image-left's X < image-right's X
    assert img_left_tx < img_right_tx


def test_text_left_image_right_accent_color_emits_bar(sub_primary):
    """When accent_color_hex is set, a small colored RECTANGLE lands under
    the title. Invariant: the fill hex appears in an updateShapeProperties
    request."""
    content = {
        "title": "Accent demo",
        "body": "x",
        "image": {"prompt": "x"},
        "accent_color_hex": "#FF5733",
    }
    reqs, _ = create_mod.build_slide_requests(
        slide_id="s", archetype_name="text_left_image_right",
        content=content, sub=sub_primary,
    )
    usps = [r["updateShapeProperties"] for r in reqs if "updateShapeProperties" in r]
    # Find the one whose color is #FF5733 (approx red=1.0, green=0.341, blue=0.2)
    accent = None
    for usp in usps:
        sp = usp["shapeProperties"]
        color = ((sp.get("shapeBackgroundFill") or {}).get("solidFill") or {}).get("color") or {}
        rgb = color.get("rgbColor") or {}
        if rgb.get("red") == 1.0 and round(rgb.get("green", 0), 2) == 0.34:
            accent = usp
            break
    assert accent is not None, "accent_color_hex fill not emitted"


def test_text_left_image_right_body_text_color_applied(sub_primary):
    """body_text_color_hex sets foregroundColor on the body text run —
    fields mask must include foregroundColor."""
    content = {
        "title": "t",
        "body": "coloured",
        "image": {"prompt": "x"},
        "body_text_color_hex": "#0066CC",
    }
    reqs, _ = create_mod.build_slide_requests(
        slide_id="s", archetype_name="text_left_image_right",
        content=content, sub=sub_primary,
    )
    # Find the body's insertText to get its objectId (body shares the id
    # with the createShape that immediately preceded it)
    body_insert = next(
        ins for ins in _insert_text_reqs(reqs) if ins["text"] == "coloured"
    )
    body_oid = body_insert["objectId"]
    # Then find the updateTextStyle for that objectId
    body_style = None
    for r in reqs:
        uts = r.get("updateTextStyle")
        if uts and uts["objectId"] == body_oid:
            body_style = uts
            break
    assert body_style is not None, "body updateTextStyle missing"
    assert "foregroundColor" in body_style["fields"]
    rgb = body_style["style"]["foregroundColor"]["opaqueColor"]["rgbColor"]
    # #0066CC = (0, 0.4, 0.8) approximately
    assert rgb["red"] == 0.0
    assert round(rgb["green"], 2) == 0.4
    assert round(rgb["blue"], 2) == 0.8


def test_text_left_image_right_no_image_builds_text_only(sub_primary):
    """When neither image url nor prompt is provided, the slide renders
    text-only — no createImage, no placeholder [IMAGE:] marker."""
    content = {
        "title": "Text only",
        "body": "no image here",
    }
    reqs, _ = create_mod.build_slide_requests(
        slide_id="s", archetype_name="text_left_image_right",
        content=content, sub=sub_primary,
    )
    assert len(_create_image_reqs(reqs)) == 0
    assert not any("[IMAGE:" in ins.get("text", "") for ins in _insert_text_reqs(reqs))


def test_text_left_image_right_caption_emitted_when_image_present(sub_primary):
    """image_caption is rendered below the image when an image slot exists.
    Skipped when there's no image (caption-without-image is pointless)."""
    with_img = {
        "title": "t",
        "body": "b",
        "image": {"prompt": "x"},
        "image_caption": "Fig. 1: the thing.",
    }
    reqs, _ = create_mod.build_slide_requests(
        slide_id="s", archetype_name="text_left_image_right",
        content=with_img, sub=sub_primary,
    )
    caption_inserts = [
        ins for ins in _insert_text_reqs(reqs)
        if ins["text"] == "Fig. 1: the thing."
    ]
    assert len(caption_inserts) == 1

    # Same content without image → no caption emitted
    no_img = {"title": "t", "body": "b", "image_caption": "orphan caption"}
    reqs2, _ = create_mod.build_slide_requests(
        slide_id="s", archetype_name="text_left_image_right",
        content=no_img, sub=sub_primary,
    )
    assert not any(
        ins.get("text") == "orphan caption" for ins in _insert_text_reqs(reqs2)
    )


def test_text_left_image_right_missing_title_warns(sub_primary):
    """The archetype YAML lists `title` as required; omitting it warns."""
    content = {"body": "titleless"}
    _, warnings = create_mod.build_slide_requests(
        slide_id="s", archetype_name="text_left_image_right",
        content=content, sub=sub_primary,
    )
    assert any("title" in w for w in warnings)


# ---------------------------------------------------------------------------
# 4col_numbered_flow builder (LOG-016 Step 7)
# ---------------------------------------------------------------------------


def _num_run_colors(reqs: list[dict]) -> list[dict]:
    """Collect foregroundColor rgbColor dicts from every updateTextStyle
    that sets a color. Used to assert num-color cycling."""
    out = []
    for r in reqs:
        uts = r.get("updateTextStyle")
        if uts and "foregroundColor" in uts.get("fields", ""):
            rgb = uts["style"]["foregroundColor"]["opaqueColor"]["rgbColor"]
            out.append(rgb)
    return out


def test_4col_numbered_flow_happy_path(sub_primary):
    """4 columns → 4 num-color updateTextStyle + title + 4 subtitle + 4 body
    = 13 createShape (title + 4×num + 4×subtitle + 4×body) + 3 separator
    rectangles, by default."""
    content = {
        "title": "Priorities from last QBR",
        "columns": [
            {"num": "01", "subtitle": "A", "body": "aa"},
            {"num": "02", "subtitle": "B", "body": "bb"},
            {"num": "03", "subtitle": "C", "body": "cc"},
            {"num": "04", "subtitle": "D", "body": "dd"},
        ],
    }
    reqs, warnings = create_mod.build_slide_requests(
        slide_id="s", archetype_name="4col_numbered_flow",
        content=content, sub=sub_primary,
    )
    assert warnings == []
    shapes = _create_shape_reqs(reqs)
    # 13 text shapes (title + 4×[num + subtitle + body]) + 3 separators = 16
    assert len(shapes) == 16
    # Default separators ON → 3 vertical RECTANGLEs between 4 columns
    sep_fills = [
        r for r in reqs
        if "updateShapeProperties" in r
        and "shapeBackgroundFill" in r["updateShapeProperties"].get("shapeProperties", {})
    ]
    # 3 separator fills only (num text doesn't use fill)
    assert len(sep_fills) == 3


def test_4col_numbered_flow_numbers_palette_cycles_across_columns(sub_primary):
    """`numbers_palette` assigns a distinct color to each column's num text
    in cycle order."""
    palette = ["#DB4437", "#0F9D58", "#4285F4", "#F4B400"]  # Google brand quad
    content = {
        "title": "t",
        "columns": [{"num": str(i), "subtitle": "s", "body": "b"} for i in range(4)],
        "numbers_palette": palette,
    }
    reqs, _ = create_mod.build_slide_requests(
        slide_id="s", archetype_name="4col_numbered_flow",
        content=content, sub=sub_primary,
    )
    # Expect exactly 4 updateTextStyle with foregroundColor (one per num)
    colors = _num_run_colors(reqs)
    assert len(colors) == 4
    # Hex → rgb expected mapping (hex_to_rgb normalization same as in create.py)
    def approx(rgb, r, g, b):
        return (round(rgb["red"], 2) == r
                and round(rgb["green"], 2) == g
                and round(rgb["blue"], 2) == b)
    # #DB4437 ≈ (0.86, 0.27, 0.22)
    assert approx(colors[0], 0.86, 0.27, 0.22)
    # #0F9D58 ≈ (0.06, 0.62, 0.35)
    assert approx(colors[1], 0.06, 0.62, 0.35)
    # #4285F4 ≈ (0.26, 0.52, 0.96)
    assert approx(colors[2], 0.26, 0.52, 0.96)
    # #F4B400 ≈ (0.96, 0.71, 0.0)
    assert approx(colors[3], 0.96, 0.71, 0.0)


def test_4col_numbered_flow_per_column_num_hex_beats_palette(sub_primary):
    """col['num_color_hex'] wins over numbers_palette[i]."""
    content = {
        "title": "t",
        "columns": [
            {"num": "01", "subtitle": "s", "body": "b"},  # → palette[0]
            {"num": "02", "subtitle": "s", "body": "b", "num_color_hex": "#FF00FF"},  # → override
            {"num": "03", "subtitle": "s", "body": "b"},  # → palette[2]
            {"num": "04", "subtitle": "s", "body": "b"},  # → palette[3]
        ],
        "numbers_palette": ["#DB4437", "#0F9D58", "#4285F4", "#F4B400"],
    }
    reqs, _ = create_mod.build_slide_requests(
        slide_id="s", archetype_name="4col_numbered_flow",
        content=content, sub=sub_primary,
    )
    colors = _num_run_colors(reqs)
    # col2 (index 1) should be magenta (1, 0, 1), not palette[1] (green)
    assert colors[1]["red"] == 1.0
    assert colors[1]["green"] == 0.0
    assert colors[1]["blue"] == 1.0


def test_4col_numbered_flow_separators_can_be_disabled(sub_primary):
    """`separators: False` drops the vertical divider RECTANGLEs."""
    content = {
        "title": "t",
        "columns": [
            {"num": "01", "subtitle": "s", "body": "b"},
            {"num": "02", "subtitle": "s", "body": "b"},
            {"num": "03", "subtitle": "s", "body": "b"},
            {"num": "04", "subtitle": "s", "body": "b"},
        ],
        "separators": False,
    }
    reqs, _ = create_mod.build_slide_requests(
        slide_id="s", archetype_name="4col_numbered_flow",
        content=content, sub=sub_primary,
    )
    # No updateShapeProperties with shapeBackgroundFill (separator fills are the
    # only fills this builder emits — num/subtitle/body are TEXT_BOX text slots)
    sep_fills = [
        r for r in reqs
        if "updateShapeProperties" in r
        and "shapeBackgroundFill" in r["updateShapeProperties"].get("shapeProperties", {})
    ]
    assert len(sep_fills) == 0


def test_4col_numbered_flow_separator_color_hex_applied(sub_primary):
    """`separator_color_hex` colors the vertical dividers directly."""
    content = {
        "title": "t",
        "columns": [
            {"num": "01", "subtitle": "s", "body": "b"},
            {"num": "02", "subtitle": "s", "body": "b"},
        ],
        "separator_color_hex": "#FF0000",  # pure red
    }
    reqs, _ = create_mod.build_slide_requests(
        slide_id="s", archetype_name="4col_numbered_flow",
        content=content, sub=sub_primary,
    )
    sep_fills = [
        r["updateShapeProperties"] for r in reqs
        if "updateShapeProperties" in r
        and "shapeBackgroundFill" in r["updateShapeProperties"].get("shapeProperties", {})
    ]
    assert len(sep_fills) == 1  # 2 columns → 1 separator
    rgb = (
        sep_fills[0]["shapeProperties"]["shapeBackgroundFill"]
        ["solidFill"]["color"]["rgbColor"]
    )
    assert rgb["red"] == 1.0
    assert rgb.get("green", 0) == 0.0
    assert rgb.get("blue", 0) == 0.0


def test_4col_numbered_flow_truncates_extra_columns(sub_primary):
    """If content["columns"] has > 4 entries, only the first 4 render."""
    content = {
        "title": "too many",
        "columns": [
            {"num": f"{i+1:02d}", "subtitle": "s", "body": "b"} for i in range(6)
        ],
    }
    reqs, _ = create_mod.build_slide_requests(
        slide_id="s", archetype_name="4col_numbered_flow",
        content=content, sub=sub_primary,
    )
    # Only 4 num text runs should be colored
    colors = _num_run_colors(reqs)
    assert len(colors) == 4


def test_4col_numbered_flow_missing_required_slots_warns(sub_primary):
    """columns is required per YAML; omitting it warns."""
    content = {"title": "no cols"}
    _, warnings = create_mod.build_slide_requests(
        slide_id="s", archetype_name="4col_numbered_flow",
        content=content, sub=sub_primary,
    )
    assert any("columns" in w for w in warnings)
