"""Unit tests for restyle_slides + _should_rewrite_run_color."""
from __future__ import annotations

import pytest

from slides_mcp import server as server_mod
from slides_mcp.server import _should_rewrite_run_color
from tests.fixtures import hex_to_rgb
from tests.fixtures import size as _size
from tests.fixtures import transform as _transform

# -----------------------------------------------------------------
# _should_rewrite_run_color — pure decision function
# -----------------------------------------------------------------

def test_should_rewrite_near_black_skipped():
    # Near-black = body text; never rewrite
    brief = {"accent": "#E8612E", "text": "#111111"}
    rewrite, target = _should_rewrite_run_color("#000000", brief)
    assert rewrite is False
    assert target is None


def test_should_rewrite_near_white_skipped():
    brief = {"accent": "#E8612E", "text": "#111111"}
    rewrite, target = _should_rewrite_run_color("#FEFEFE", brief)
    assert rewrite is False
    assert target is None


def test_should_rewrite_already_in_brief_skipped():
    brief = {"accent": "#E8612E"}
    rewrite, target = _should_rewrite_run_color("#E8612E", brief)
    assert rewrite is False
    assert target is None


def test_should_rewrite_chromatic_drift_snaps_to_nearest():
    # Input magenta, brief accent orange, brief text black
    brief = {"accent": "#E8612E", "text": "#000000"}
    rewrite, target = _should_rewrite_run_color("#FF00FF", brief)
    assert rewrite is True
    # Magenta is closer to orange than to black on RGB-sum
    assert target == "#E8612E"


def test_should_rewrite_close_to_brief_skipped():
    # distance 30 from brief accent — under the 60 threshold, keep it.
    brief = {"accent": "#E8612E"}
    # Craft a near-match
    rewrite, target = _should_rewrite_run_color("#E8622E", brief)
    assert rewrite is False
    assert target is None


# -----------------------------------------------------------------
# restyle_slides tool — integration via mocked slides_api
# -----------------------------------------------------------------

@pytest.fixture
def fake_slides_api(monkeypatch: pytest.MonkeyPatch):
    """Fake out slides_api so tests don't make real calls.

    Returns a mutable captured_requests list that accumulates everything
    restyle_slides passes to batch_update.
    """
    captured: dict[str, object] = {"requests": [], "thumbnails": 0}

    def _deck_id_from_url(url: str) -> str:
        return "DECK_TEST"

    def _get_presentation(deck_id: str, fields: str | None = None) -> dict:
        return fake_slides_api.prez  # injected by the test

    def _get_slide(deck_id: str, slide_id: str) -> dict:
        for s in fake_slides_api.prez.get("slides", []):
            if s["objectId"] == slide_id:
                return s
        raise KeyError(slide_id)

    def _batch_update(deck_id: str, requests: list) -> dict:
        captured["requests"].extend(requests)
        return {"replies": [{"ok": True} for _ in requests]}

    def _get_thumbnail(deck_id: str, slide_id: str, size: str = "MEDIUM") -> str:
        captured["thumbnails"] += 1
        return f"https://fake/{deck_id}/{slide_id}/{size}.png"

    monkeypatch.setattr(server_mod.slides_api, "deck_id_from_url", _deck_id_from_url)
    monkeypatch.setattr(server_mod.slides_api, "get_presentation", _get_presentation)
    monkeypatch.setattr(server_mod.slides_api, "get_slide", _get_slide)
    monkeypatch.setattr(server_mod.slides_api, "batch_update", _batch_update)
    monkeypatch.setattr(server_mod.slides_api, "get_thumbnail", _get_thumbnail)
    # Silence audit-log writes to disk
    monkeypatch.setattr(server_mod, "_append_audit", lambda *a, **k: None)

    fake_slides_api.captured = captured
    return fake_slides_api


def _make_text_shape(obj_id: str, text: str, color_hex: str | None = None,
                    fill_hex: str | None = None) -> dict:
    text_el: dict = {"textRun": {"content": text, "style": {}}}
    if color_hex:
        text_el["textRun"]["style"]["foregroundColor"] = {
            "opaqueColor": {"rgbColor": hex_to_rgb(color_hex)}
        }
    shape_props: dict = {}
    if fill_hex:
        shape_props["shapeBackgroundFill"] = {
            "solidFill": {"color": {"rgbColor": hex_to_rgb(fill_hex)}}
        }
    return {
        "objectId": obj_id,
        "size": _size(5, 1),
        "transform": _transform(1, 1),
        "shape": {
            "shapeType": "TEXT_BOX",
            "shapeProperties": shape_props,
            "text": {"textElements": [text_el]},
        },
    }


def _prez_with_brief(slides: list, brief_yaml: str | None = None) -> dict:
    """Build a minimal presentation payload with a theme-brief meta-slide."""
    all_slides = list(slides)
    if brief_yaml is not None:
        # Meta-slide: marker title box + body YAML box
        meta = {
            "objectId": "meta_brief_slide",
            "slideProperties": {"isSkipped": True},
            "pageElements": [
                {
                    "objectId": "marker_box",
                    "size": _size(5, 0.5),
                    "transform": _transform(0, 0),
                    "shape": {
                        "shapeType": "TEXT_BOX",
                        "text": {"textElements": [{
                            "textRun": {"content": "__SLIDES_MCP_THEME_BRIEF__ — DO NOT DELETE",
                                        "style": {}}
                        }]},
                    },
                },
                {
                    "objectId": "body_box",
                    "size": _size(5, 4),
                    "transform": _transform(0, 1),
                    "shape": {
                        "shapeType": "TEXT_BOX",
                        "text": {"textElements": [{
                            "textRun": {"content": brief_yaml, "style": {}}
                        }]},
                    },
                },
            ],
        }
        all_slides.append(meta)
    return {"slides": all_slides}


BRIEF_YAML = """\
__slides_mcp_theme_brief:
  version: 1
  palette:
    surface: "#0F1A4A"
    accent: "#E8612E"
    text: "#000000"
    category_set: ["#E8612E", "#0F1A4A", "#888888"]
  shape_language: sharp
  numbering_style: bold
  tone: clean editorial
  image_prompt_style: documentary
"""


def test_restyle_refuses_without_confirm(fake_slides_api):
    fake_slides_api.prez = _prez_with_brief([
        {"objectId": "s1", "pageElements": []},
    ], BRIEF_YAML)
    with pytest.raises(ValueError, match="confirm_destructive=True"):
        server_mod.restyle_slides(
            deck_url="https://fake", slide_ids="all", confirm_destructive=False,
        )


def test_restyle_errors_without_brief(fake_slides_api):
    # No brief, no overrides → explicit error
    fake_slides_api.prez = _prez_with_brief([
        {"objectId": "s1", "pageElements": []},
    ], brief_yaml=None)
    with pytest.raises(ValueError, match="no theme brief"):
        server_mod.restyle_slides(
            deck_url="https://fake", slide_ids="all", confirm_destructive=True,
        )


def test_restyle_rewrites_drifted_fill(fake_slides_api):
    # A slide with a bright red fill; brief accent is orange. Should rewrite.
    slide = {
        "objectId": "s1",
        "pageElements": [_make_text_shape("card", "", fill_hex="#FF0000")],
    }
    fake_slides_api.prez = _prez_with_brief([slide], BRIEF_YAML)
    out = server_mod.restyle_slides(
        deck_url="https://fake", slide_ids="all", confirm_destructive=True,
    )
    assert out["total_rewrites"] >= 1
    reqs = fake_slides_api.captured["requests"]
    fill_updates = [r for r in reqs if "updateShapeProperties" in r]
    assert len(fill_updates) == 1
    assert fill_updates[0]["updateShapeProperties"]["objectId"] == "card"


def test_restyle_rewrites_drifted_text_color(fake_slides_api):
    # A slide with a magenta text run; brief accent orange. Should rewrite.
    slide = {
        "objectId": "s1",
        "pageElements": [_make_text_shape("title", "Hello", color_hex="#FF00FF")],
    }
    fake_slides_api.prez = _prez_with_brief([slide], BRIEF_YAML)
    out = server_mod.restyle_slides(
        deck_url="https://fake", slide_ids="all", confirm_destructive=True,
    )
    assert out["total_rewrites"] >= 1
    reqs = fake_slides_api.captured["requests"]
    text_updates = [r for r in reqs if "updateTextStyle" in r]
    assert len(text_updates) == 1
    assert text_updates[0]["updateTextStyle"]["objectId"] == "title"
    assert text_updates[0]["updateTextStyle"]["textRange"] == {"type": "ALL"}


def test_restyle_skips_near_black_text(fake_slides_api):
    slide = {
        "objectId": "s1",
        "pageElements": [_make_text_shape("body", "plain", color_hex="#111111")],
    }
    fake_slides_api.prez = _prez_with_brief([slide], BRIEF_YAML)
    out = server_mod.restyle_slides(
        deck_url="https://fake", slide_ids="all", confirm_destructive=True,
    )
    assert out["total_rewrites"] == 0


def test_restyle_skips_meta_slide(fake_slides_api):
    # Only the meta-slide is present; it must NOT be rewritten.
    fake_slides_api.prez = _prez_with_brief([], BRIEF_YAML)
    out = server_mod.restyle_slides(
        deck_url="https://fake", slide_ids="all", confirm_destructive=True,
    )
    assert out["restyled_slide_ids"] == []
    assert out["total_rewrites"] == 0


def test_restyle_unknown_slide_id_warns(fake_slides_api):
    fake_slides_api.prez = _prez_with_brief([
        {"objectId": "s1", "pageElements": []},
    ], BRIEF_YAML)
    out = server_mod.restyle_slides(
        deck_url="https://fake",
        slide_ids=["s1", "ghost_slide"],
        confirm_destructive=True,
    )
    assert any("ghost_slide" in w for w in out["warnings"])


def test_restyle_brief_overrides_merged(fake_slides_api):
    # Base brief has orange accent. Override with green.
    slide = {
        "objectId": "s1",
        "pageElements": [_make_text_shape("box", "", fill_hex="#FF00FF")],
    }
    fake_slides_api.prez = _prez_with_brief([slide], BRIEF_YAML)
    out = server_mod.restyle_slides(
        deck_url="https://fake",
        slide_ids="all",
        brief_overrides={"palette": {"accent": "#00FF00"}},
        confirm_destructive=True,
    )
    # Accent override should propagate to the resolved brief
    assert out["brief_applied"]["palette"]["accent"] == "#00FF00"
    # Magenta fill is closer to green than orange (both chromatic non-black/white)
    reqs = fake_slides_api.captured["requests"]
    fill_updates = [r for r in reqs if "updateShapeProperties" in r]
    assert len(fill_updates) == 1


def test_restyle_per_slide_shape_counts(fake_slides_api):
    # Two slides, each one drifted shape — verify counts attribute correctly.
    slides = [
        {"objectId": "s1",
         "pageElements": [_make_text_shape("card1", "", fill_hex="#FF0000")]},
        {"objectId": "s2",
         "pageElements": [_make_text_shape("title2", "Hello", color_hex="#FF00FF")]},
    ]
    fake_slides_api.prez = _prez_with_brief(slides, BRIEF_YAML)
    out = server_mod.restyle_slides(
        deck_url="https://fake", slide_ids="all", confirm_destructive=True,
    )
    assert out["per_slide"]["s1"]["fill_rewrites"] == 1
    assert out["per_slide"]["s1"]["text_rewrites"] == 0
    assert out["per_slide"]["s2"]["fill_rewrites"] == 0
    assert out["per_slide"]["s2"]["text_rewrites"] == 1
    assert set(out["restyled_slide_ids"]) == {"s1", "s2"}
