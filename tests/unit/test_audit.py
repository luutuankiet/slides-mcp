from __future__ import annotations

from pathlib import Path

import pytest

from slides_mcp.audit import (
    audit,
    promote_color_to_theme,
    promote_font_to_theme,
)
from slides_mcp.normalize import normalize_page
from slides_mcp.theme import load_theme
from tests.fixtures import slide_3col_pill_cards


def test_audit_flags_non_theme_color():
    # Example theme has brand_accent=#3366CC. Our fixture uses #3366CC for pills so OK.
    # But card fill is #F3F3F3 which IS in example theme (surface_card). So no drift.
    shapes = normalize_page(slide_3col_pill_cards())
    sub = load_theme("example").sub("primary")
    report = audit(shapes, sub, theme_name="example")
    # All colors in this fixture are intentionally in-theme.
    assert report.total_text_runs > 0
    # No drift expected for a well-behaved fixture:
    assert not report.color_drifts


def test_audit_flags_drift_from_mismatched_theme():
    # Switch to alt_palette which has #CC6633 brand; fixture uses #3366CC.
    shapes = normalize_page(slide_3col_pill_cards())
    sub = load_theme("example").sub("alt_palette")
    report = audit(shapes, sub, theme_name="example")
    assert any(d.hex_value == "#3366CC" for d in report.color_drifts)


def test_promote_color_creates_user_theme(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SLIDES_MCP_THEMES_DIR", str(tmp_path))
    # Ensure the lru_cache is bypassed by giving a fresh theme name
    out = promote_color_to_theme(
        theme_name="custom", sub_theme="primary",
        role_name="new_accent", hex_value="#123456",
    )
    assert out.exists()
    content = out.read_text()
    assert "new_accent" in content
    assert "#123456" in content


def test_promote_font_to_existing_theme(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SLIDES_MCP_THEMES_DIR", str(tmp_path))
    # First call creates, second appends
    promote_color_to_theme("mytheme", "primary", "accent", "#AABBCC")
    promote_font_to_theme(
        "mytheme", "primary", role_name="display",
        family="Inter", size_pt=36, weight=700,
    )
    out = (tmp_path / "mytheme.yaml").read_text()
    assert "Inter" in out
    assert "accent" in out
