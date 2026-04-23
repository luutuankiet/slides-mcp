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


# --- scale readback (LOG-016 Step 3: 3.281 bug fix) -----------------

def _scaled_shape(
    object_id: str, w_intrinsic: float, h_intrinsic: float,
    tx_in: float, ty_in: float, sx: float, sy: float,
) -> dict:
    """Hand-rolled pageElement with explicit scale in transform.

    The fixture module's `transform()` helper always emits scaleX=scaleY=1.0
    which is correct for the common case but can't exercise the scale-readback
    bug this test guards. So we construct the minimal shape directly.
    """
    from slides_mcp.normalize import EMU_PER_INCH
    return {
        "objectId": object_id,
        "size": {
            "width": {"magnitude": int(w_intrinsic * EMU_PER_INCH), "unit": "EMU"},
            "height": {"magnitude": int(h_intrinsic * EMU_PER_INCH), "unit": "EMU"},
        },
        "transform": {
            "scaleX": sx, "scaleY": sy,
            "translateX": int(tx_in * EMU_PER_INCH),
            "translateY": int(ty_in * EMU_PER_INCH),
            "unit": "EMU",
        },
        "shape": {"shapeType": "RECTANGLE", "text": {"textElements": []}},
    }


def test_normalize_multiplies_size_by_scale():
    """Sandbox repro: archetype geometry 5.25×2.3 inches baked as intrinsic,
    then scaled to 10×5.625 deck via scaleX=scaleY=0.625. The rendered dims
    should be 5.25 × 0.625 = 3.281 and 2.3 × 0.625 = 1.438 — not the raw
    intrinsic 5.25/2.3 the pre-fix code returned.
    """
    page_dict = {
        "objectId": "slide_scaled",
        "pageElements": [_scaled_shape(
            "pill_card", w_intrinsic=5.25, h_intrinsic=2.3,
            tx_in=0.9, ty_in=5.6, sx=0.625, sy=0.625,
        )],
    }
    shapes = normalize_page(page_dict)
    assert len(shapes) == 1
    pill = shapes[0]
    assert pill.w_in == 3.281
    assert pill.h_in == 1.438
    # Translate is an absolute page offset — NOT multiplied by scale
    assert pill.left_in == 0.9
    assert pill.top_in == 5.6


def test_normalize_identity_scale_matches_intrinsic():
    """Regression guard: scaleX=scaleY=1.0 (the fixture default) must keep
    rendered dims equal to intrinsic. Otherwise every pre-existing fixture
    test would drift.
    """
    page_dict = {
        "objectId": "slide_identity",
        "pageElements": [_scaled_shape(
            "card", w_intrinsic=4.6, h_intrinsic=2.3,
            tx_in=1.0, ty_in=2.0, sx=1.0, sy=1.0,
        )],
    }
    shapes = normalize_page(page_dict)
    assert shapes[0].w_in == 4.6
    assert shapes[0].h_in == 2.3


def test_normalize_missing_scale_defaults_to_identity():
    """Slides API omits scaleX/scaleY from the payload when they equal 1.0.
    The extractor must coerce missing → 1.0, not crash or zero the shape.
    """
    page_dict = {
        "objectId": "slide_noscale",
        "pageElements": [{
            "objectId": "bare",
            "size": {"width": {"magnitude": 914400 * 3, "unit": "EMU"},
                     "height": {"magnitude": 914400 * 2, "unit": "EMU"}},
            "transform": {"translateX": 0, "translateY": 0, "unit": "EMU"},
            "shape": {"shapeType": "RECTANGLE", "text": {"textElements": []}},
        }],
    }
    shapes = normalize_page(page_dict)
    assert shapes[0].w_in == 3.0
    assert shapes[0].h_in == 2.0
