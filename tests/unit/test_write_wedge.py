"""Tests for the v2.1 write-wedge — exec_batch_update + add_section_footers.

Mocks `slides_api.batch_update` and `slides_api.get_presentation` via
monkeypatch. Reuses the existing `tests.fixtures` page builders for prez
shapes that match real Slides API JSON.
"""
from __future__ import annotations

from typing import Any

import pytest

from slides_mcp.server import (
    DESTRUCTIVE_KINDS,
    _extract_affected_slide_ids,
    add_section_footers,
    exec_batch_update,
)
from slides_mcp.slides_api import SlidesApiError
from tests.fixtures import (
    page as fixt_page,
)
from tests.fixtures import (
    slide_3col_pill_cards,
    slide_cover_with_hero,
    textbox,
)

# ---- prez fixture ---------------------------------------------------


def _three_slide_prez() -> dict[str, Any]:
    """Three-slide deck: slide_3col + slide_cover + a minimal slide_x."""
    return {
        "presentationId": "deck_test",
        "title": "Test Deck",
        "slides": [
            slide_3col_pill_cards(),  # objectId=slide_3col
            slide_cover_with_hero(),  # objectId=slide_cover
            fixt_page(
                "slide_x",
                [textbox("title_x", "X", 0, 0, 10, 1, font="Inter", size_pt=24)],
            ),
        ],
    }


# ---- _extract_affected_slide_ids -----------------------------------


def test_extract_empty():
    assert _extract_affected_slide_ids([], [], _three_slide_prez()) == []


def test_extract_page_object_id():
    requests = [{
        "createShape": {
            "objectId": "new_x",
            "shapeType": "RECTANGLE",
            "elementProperties": {"pageObjectId": "slide_3col"},
        }
    }]
    assert _extract_affected_slide_ids(requests, [], _three_slide_prez()) == ["slide_3col"]


def test_extract_replace_all_text_scoped():
    requests = [{
        "replaceAllText": {
            "containsText": {"text": "foo"},
            "replaceText": "bar",
            "pageObjectIds": ["slide_3col", "slide_cover"],
        }
    }]
    assert _extract_affected_slide_ids(requests, [], _three_slide_prez()) == [
        "slide_3col",
        "slide_cover",
    ]


def test_extract_replace_all_text_whole_deck():
    """replaceAllText without pageObjectIds is deck-wide → returns ALL slide ids."""
    requests = [{"replaceAllText": {"containsText": {"text": "foo"}, "replaceText": "bar"}}]
    result = _extract_affected_slide_ids(requests, [], _three_slide_prez())
    assert sorted(result) == sorted(["slide_3col", "slide_cover", "slide_x"])


def test_extract_create_slide_reply():
    requests = [{"createSlide": {}}]
    replies = [{"createSlide": {"objectId": "newly_minted"}}]
    assert "newly_minted" in _extract_affected_slide_ids(
        requests, replies, _three_slide_prez()
    )


def test_extract_element_level_objectid_maps_to_slide():
    """updateTextStyle on element 'pill1' (unique to slide_3col) → slide_3col affected.

    Note: avoid using 'title' or 'subtitle' as objectIds in tests — the existing
    tests/fixtures slide builders share those ids across multiple slides, and
    elem_to_slide is dict-overwrite (last slide wins).
    """
    requests = [{
        "updateTextStyle": {
            "objectId": "pill1",  # only exists on slide_3col_pill_cards
            "textRange": {"type": "ALL"},
            "style": {"bold": True},
            "fields": "bold",
        }
    }]
    assert _extract_affected_slide_ids(requests, [], _three_slide_prez()) == ["slide_3col"]


# ---- exec_batch_update validation ----------------------------------


def test_exec_empty_requests_raises():
    with pytest.raises(ValueError, match="non-empty"):
        exec_batch_update(
            "https://docs.google.com/presentation/d/abc/edit",
            [],
        )


def test_exec_invalid_post_state_raises():
    with pytest.raises(ValueError, match="post_state"):
        exec_batch_update(
            "https://docs.google.com/presentation/d/abc/edit",
            [{"createSlide": {}}],
            post_state="bogus",  # type: ignore[arg-type]
        )


def test_exec_dry_run_no_fire_surfaces_kinds(monkeypatch):
    fired = []
    monkeypatch.setattr(
        "slides_mcp.slides_api.batch_update",
        lambda *a, **kw: fired.append("fire") or {"replies": []},
    )
    monkeypatch.setattr(
        "slides_mcp.slides_api.get_presentation",
        lambda *a, **kw: fired.append("read") or {},
    )
    requests = [
        {"deleteObject": {"objectId": "x"}},
        {"createShape": {"objectId": "s1", "shapeType": "RECTANGLE"}},
    ]
    out = exec_batch_update("deck_id_xyz", requests, dry_run=True)
    assert fired == []  # neither fire nor re-read
    assert out["dry_run"] is True
    assert out["request_kinds"] == ["deleteObject", "createShape"]
    assert out["preview"] == requests
    assert "deleteObject" in out["destructive_kinds_detected"]
    assert "createShape" not in out["destructive_kinds_detected"]
    assert out["isError"] is False


# ---- destructive guard ---------------------------------------------


def test_exec_destructive_refused_without_confirm(monkeypatch):
    fired = []
    monkeypatch.setattr(
        "slides_mcp.slides_api.batch_update",
        lambda *a, **kw: fired.append("fire") or {"replies": []},
    )
    monkeypatch.setattr(
        "slides_mcp.slides_api.get_presentation",
        lambda *a, **kw: fired.append("read") or _three_slide_prez(),
    )
    out = exec_batch_update(
        "deck_id_xyz",
        [{"deleteObject": {"objectId": "x"}}],
        confirm_destructive=False,
    )
    assert fired == []
    assert out["isError"] is True
    assert any("destructive" in w.lower() for w in out["warnings"])
    assert out["applied_request_count"] == 0


def test_exec_destructive_allowed_with_confirm(monkeypatch):
    monkeypatch.setattr(
        "slides_mcp.slides_api.batch_update",
        lambda deck_id, requests: {"replies": [{}], "presentationId": deck_id},
    )
    monkeypatch.setattr(
        "slides_mcp.slides_api.get_presentation",
        lambda deck_id: _three_slide_prez(),
    )
    out = exec_batch_update(
        "deck_id_xyz",
        [{"deleteObject": {"objectId": "slide_3col"}}],
        confirm_destructive=True,
        post_state="none",
    )
    assert out["isError"] is False
    assert out["applied_request_count"] == 1


# ---- post_state variants -------------------------------------------


def test_exec_post_state_none_skips_reread(monkeypatch):
    reads = []
    monkeypatch.setattr(
        "slides_mcp.slides_api.batch_update",
        lambda *a, **kw: {"replies": [{"createSlide": {"objectId": "new"}}]},
    )
    monkeypatch.setattr(
        "slides_mcp.slides_api.get_presentation",
        lambda *a, **kw: reads.append("read") or _three_slide_prez(),
    )
    out = exec_batch_update(
        "deck_id_xyz",
        [{"createSlide": {}}],
        post_state="none",
    )
    assert reads == []  # no re-read when post_state="none"
    assert "post_state" not in out
    assert out["replies"] == [{"createSlide": {"objectId": "new"}}]


def test_exec_post_state_outline_deck_only(monkeypatch):
    monkeypatch.setattr(
        "slides_mcp.slides_api.batch_update", lambda *a, **kw: {"replies": [{}]}
    )
    monkeypatch.setattr(
        "slides_mcp.slides_api.get_presentation", lambda *a, **kw: _three_slide_prez()
    )
    requests = [{
        "createShape": {
            "objectId": "x",
            "shapeType": "RECTANGLE",
            "elementProperties": {"pageObjectId": "slide_3col"},
        }
    }]
    out = exec_batch_update("deck_id_xyz", requests, post_state="outline")
    assert "post_state" in out
    assert "deck_outline" in out["post_state"]
    assert out["post_state"]["deck_outline"]["slide_count"] == 3
    assert "slides" not in out["post_state"]


def test_exec_post_state_summary_includes_touched_slides(monkeypatch):
    monkeypatch.setattr(
        "slides_mcp.slides_api.batch_update", lambda *a, **kw: {"replies": [{}]}
    )
    monkeypatch.setattr(
        "slides_mcp.slides_api.get_presentation", lambda *a, **kw: _three_slide_prez()
    )
    requests = [{
        "createShape": {
            "objectId": "x",
            "shapeType": "RECTANGLE",
            "elementProperties": {"pageObjectId": "slide_3col"},
        }
    }]
    out = exec_batch_update("deck_id_xyz", requests, post_state="summary")
    assert out["affected_slide_ids"] == ["slide_3col"]
    assert "slides" in out["post_state"]
    assert len(out["post_state"]["slides"]) == 1
    assert out["post_state"]["slides"][0]["slide_id"] == "slide_3col"


# ---- 403 OAuth wrapping --------------------------------------------


def test_exec_403_wraps_with_actionable_message(monkeypatch):
    def raise_403(*a, **kw):
        raise SlidesApiError("forbidden", status=403, reason="permissionDenied")

    monkeypatch.setattr("slides_mcp.slides_api.batch_update", raise_403)
    with pytest.raises(SlidesApiError, match="presentations.readonly"):
        exec_batch_update(
            "deck_id_xyz",
            [{"createSlide": {}}],
            post_state="none",
        )


# ---- DESTRUCTIVE_KINDS sanity --------------------------------------


def test_destructive_kinds_membership():
    """Slide-scoped destructive operations are flagged; pure creators are not."""
    assert "replaceAllText" in DESTRUCTIVE_KINDS
    assert "deleteObject" in DESTRUCTIVE_KINDS
    assert "deleteSlide" in DESTRUCTIVE_KINDS
    assert "replaceAllShapesWithImage" in DESTRUCTIVE_KINDS
    assert "createShape" not in DESTRUCTIVE_KINDS
    assert "createSlide" not in DESTRUCTIVE_KINDS
    assert "insertText" not in DESTRUCTIVE_KINDS
    assert "updateTextStyle" not in DESTRUCTIVE_KINDS


# ---- add_section_footers -------------------------------------------


def test_section_footers_builds_4_requests_per_slide(monkeypatch):
    captured: list[list[dict]] = []

    def fake_batch_update(deck_id, requests):
        captured.append(requests)
        return {"replies": [{} for _ in requests]}

    monkeypatch.setattr("slides_mcp.slides_api.batch_update", fake_batch_update)
    monkeypatch.setattr(
        "slides_mcp.slides_api.get_presentation",
        lambda *a, **kw: _three_slide_prez(),
    )
    out = add_section_footers(
        "deck_id_xyz",
        sections=[{"name": "Intro", "slide_ids": ["slide_3col"]}],
        post_state="none",
    )
    assert out["_proof_tool"] == "add_section_footers"
    assert out["sections_applied"] == 1
    assert out["footers_added"] == 1
    # 4 requests: createShape + insertText + updateShapeProperties + updateTextStyle
    assert len(captured[0]) == 4
    kinds = [next(iter(r.keys())) for r in captured[0]]
    assert kinds == [
        "createShape",
        "insertText",
        "updateShapeProperties",
        "updateTextStyle",
    ]
    # createShape pageObjectId points at slide_3col
    assert (
        captured[0][0]["createShape"]["elementProperties"]["pageObjectId"] == "slide_3col"
    )
    # autofit:NONE invariant (LOG-015) honored
    assert (
        captured[0][2]["updateShapeProperties"]["shapeProperties"]["autofit"][
            "autofitType"
        ]
        == "NONE"
    )


def test_section_footers_skips_existing_when_overwrite_false(monkeypatch):
    """Pre-existing footer + overwrite_existing=False → slide reported as skipped, no fire."""
    prez = _three_slide_prez()
    # Inject pre-existing footer on slide_3col (suffix matches sid[-12:])
    prez["slides"][0]["pageElements"].append({
        "objectId": "slides_mcp_footer_slide_3col",
        "size": {
            "width": {"magnitude": 100, "unit": "EMU"},
            "height": {"magnitude": 100, "unit": "EMU"},
        },
        "transform": {
            "scaleX": 1, "scaleY": 1,
            "translateX": 0, "translateY": 0, "unit": "EMU",
        },
        "shape": {"shapeType": "TEXT_BOX", "text": {"textElements": []}},
    })
    captured: list = []
    monkeypatch.setattr(
        "slides_mcp.slides_api.batch_update",
        lambda d, r: captured.append(r) or {"replies": [{} for _ in r]},
    )
    monkeypatch.setattr("slides_mcp.slides_api.get_presentation", lambda *a, **kw: prez)
    out = add_section_footers(
        "deck_id_xyz",
        sections=[{"name": "Intro", "slide_ids": ["slide_3col"]}],
        overwrite_existing=False,
        post_state="none",
    )
    assert out["skipped_slide_ids"] == ["slide_3col"]
    assert out["footers_added"] == 0
    assert captured == []  # no batch_update call


def test_section_footers_template_substitution(monkeypatch):
    """Per-slide footer text computes section_name + position + total + prev/next correctly."""
    captured: list[str] = []

    def fake_batch_update(deck_id, requests):
        for r in requests:
            if "insertText" in r:
                captured.append(r["insertText"]["text"])
        return {"replies": [{} for _ in requests]}

    monkeypatch.setattr("slides_mcp.slides_api.batch_update", fake_batch_update)
    monkeypatch.setattr(
        "slides_mcp.slides_api.get_presentation",
        lambda *a, **kw: _three_slide_prez(),
    )
    out = add_section_footers(
        "deck_id_xyz",
        sections=[
            {"name": "Discovery", "slide_ids": ["slide_3col"]},
            {"name": "Build", "slide_ids": ["slide_cover", "slide_x"]},
        ],
        template="{section_name} {position}/{total} prev:{prev_name} next:{next_name}",
        post_state="none",
    )
    assert out["footers_added"] == 3
    # Section 0 (Discovery): pos 1/1, prev empty, next Build
    assert captured[0] == "Discovery 1/1 prev: next:Build"
    # Section 1 (Build): pos 1/2 then 2/2; prev Discovery, next empty
    assert captured[1] == "Build 1/2 prev:Discovery next:"
    assert captured[2] == "Build 2/2 prev:Discovery next:"


def test_section_footers_empty_raises():
    with pytest.raises(ValueError, match="non-empty"):
        add_section_footers("deck_id_xyz", sections=[])


def test_section_footers_unknown_position_raises():
    with pytest.raises(ValueError, match="footer_position"):
        add_section_footers(
            "deck_id_xyz",
            sections=[{"name": "X", "slide_ids": ["slide_3col"]}],
            footer_position="top-left",  # type: ignore[arg-type]
        )
