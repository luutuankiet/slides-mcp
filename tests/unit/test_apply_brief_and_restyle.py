"""Core sanity tests for apply_brief_and_restyle (Scope C)."""
from __future__ import annotations

import pytest

from slides_mcp import server as server_mod
from slides_mcp.server import apply_brief_and_restyle


def _brief() -> dict:
    return {
        "version": 1,
        "palette": {
            "surface": "#134E4A", "accent": "#B45309", "text": "#1F2937",
            "category_set": ["#B45309", "#134E4A"],
        },
        "shape_language": "sharp", "numbering_style": "outlined",
        "tone": "t", "image_prompt_style": "p",
    }


def test_validation_errors_no_network():
    # Both brief and delta
    with pytest.raises(ValueError, match="exactly one"):
        apply_brief_and_restyle("x", brief=_brief(), delta={}, confirm_destructive=True)
    # Missing confirm_destructive
    with pytest.raises(ValueError, match="confirm_destructive"):
        apply_brief_and_restyle("x", brief=_brief())
    # Invalid brief fails BEFORE any network call (early validate)
    with pytest.raises(ValueError, match="invalid brief"):
        apply_brief_and_restyle(
            "x", brief={"palette": {"accent": "bad"}}, confirm_destructive=True
        )


def test_orchestration_commits_and_restyles(monkeypatch):
    captured = {"batch": 0, "restyle": 0}

    monkeypatch.setattr(server_mod.slides_api, "deck_id_from_url", lambda url: "DECK")
    monkeypatch.setattr(server_mod, "_fetch_for_brief", lambda d: {"slides": []})
    monkeypatch.setattr(server_mod, "_deck_dimensions_in", lambda d: (13.33, 7.5))
    monkeypatch.setattr(
        server_mod.slides_api, "get_presentation",
        lambda d, fields=None: {"slides": [{"objectId": "s1"}]},
    )
    def _batch(deck, reqs):
        captured["batch"] += 1
        return {"replies": [{} for _ in reqs]}
    monkeypatch.setattr(server_mod.slides_api, "batch_update", _batch)

    def _fake_restyle(**kw):
        captured["restyle"] += 1
        return {"deck_id": "DECK", "restyled_slide_ids": [], "skipped_slide_ids": [],
                "total_rewrites": 0, "per_slide": {}, "applied_request_count": 0,
                "thumbnails": {}, "warnings": []}
    monkeypatch.setattr(server_mod, "restyle_slides", _fake_restyle)

    result = apply_brief_and_restyle("any", brief=_brief(), confirm_destructive=True)
    assert result["action"] == "created"
    assert captured["restyle"] == 1
    assert captured["batch"] >= 1
