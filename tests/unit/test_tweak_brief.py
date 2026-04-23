"""Core sanity tests for compute_directive_delta (Scope A)."""
from __future__ import annotations

from slides_mcp import theme_brief as tb


def _base() -> dict:
    return {
        "version": 1,
        "palette": {
            "surface": "#134E4A", "accent": "#B45309", "text": "#1F2937",
            "category_set": ["#B45309", "#134E4A", "#A16207"],
        },
        "shape_language": "sharp", "numbering_style": "outlined",
        "tone": "t", "image_prompt_style": "p",
        "font_family": {"heading": "DM Serif Display", "body": "IBM Plex Sans"},
    }


def test_empty_directive_low_confidence():
    out = tb.compute_directive_delta(_base(), "")
    assert out["delta"] == {} and out["confidence"] == "low"


def test_multi_axis_and_candidate_validates():
    out = tb.compute_directive_delta(_base(), "warmer and more editorial and rounder")
    assert "accent_warmer" in out["matched_axes"]
    assert "font_editorial" in out["matched_axes"]
    assert "shape_rounded" in out["matched_axes"]
    ok, errs = tb.validate_brief(out["candidate_brief"])
    assert ok, errs
    # delta merges cleanly back to candidate
    assert tb.merge_brief(_base(), out["delta"]) == out["candidate_brief"]


def test_unknown_bubble_to_unresolved_terms():
    out = tb.compute_directive_delta(_base(), "warmer and xyzzy")
    assert "accent_warmer" in out["matched_axes"]
    assert "xyzzy" in out["unresolved_terms"]
    assert out["confidence"] == "medium"
