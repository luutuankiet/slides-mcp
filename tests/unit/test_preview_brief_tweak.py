"""Core sanity tests for preview_brief_tweak (Scope B rework)."""
from __future__ import annotations

import pytest
import yaml

from slides_mcp import server as server_mod
from slides_mcp.server import preview_brief_tweak


def _current() -> dict:
    return {
        "version": 1,
        "palette": {
            "surface": "#134E4A", "accent": "#B45309", "text": "#1F2937",
            "category_set": ["#B45309", "#134E4A"],
        },
        "shape_language": "sharp", "numbering_style": "outlined",
        "tone": "t", "image_prompt_style": "p",
    }


def _candidate() -> dict:
    b = _current()
    b["palette"]["accent"] = "#D97706"
    return b


def test_compare_writes_both_and_restores_meta(monkeypatch):
    captured = {"gen": [], "set": []}

    monkeypatch.setattr(server_mod.slides_api, "deck_id_from_url", lambda u: "DECK")
    monkeypatch.setattr(server_mod.slides_api, "get_thumbnail", lambda d, s, size="MEDIUM": f"url/{s}")
    monkeypatch.setattr(server_mod, "_fetch_for_brief", lambda d: {"slides": []})
    monkeypatch.setattr(
        server_mod.theme_brief_mod, "find_meta_slide",
        lambda prez: {
            "slide_id": "meta", "marker_box_id": "m", "body_box_id": "b",
            "body_text": "\n---\n" + yaml.safe_dump(
                {"__slides_mcp_theme_brief": _current()}, sort_keys=False
            ),
        },
    )
    def _fake_gv(**kw):
        captured["gen"].append(kw)
        variants = [
            {"variant_id": f"tweak_preview{i}", "brief": b,
             "slide_ids": [f"tweak_preview{i}_cover", f"tweak_preview{i}_pills"]}
            for i, b in enumerate(kw["briefs"])
        ]
        return {"deck_id": "DECK", "variants": variants, "variant_prefix": "tweak_preview",
                "total_slides_created": sum(len(v["slide_ids"]) for v in variants)}
    monkeypatch.setattr(server_mod, "generate_variants", _fake_gv)
    monkeypatch.setattr(
        server_mod, "set_theme_brief",
        lambda url, b: captured["set"].append(b) or {"action": "updated", "brief": b,
                                                     "deck_id": "DECK", "slide_id": "meta"},
    )

    result = preview_brief_tweak(deck_url="any", candidate_brief=_candidate())
    # generate_variants was called with [current, candidate] in order
    briefs_passed = captured["gen"][0]["briefs"]
    assert briefs_passed[0] == _current() and briefs_passed[1] == _candidate()
    # meta restored to current after preview
    assert result["meta_restored"] is True
    assert captured["set"][-1] == _current()
    # Human-facing outputs: thumbnails for every preview slide, manifest for lock_variant
    assert result["thumbnails"]
    assert "variants_manifest" in result
    assert result["preview_slide_ids_candidate"]


def test_invalid_candidate_raises():
    with pytest.raises(ValueError, match="candidate_brief failed validation"):
        preview_brief_tweak("any", candidate_brief={"palette": {"accent": "nope"}})
