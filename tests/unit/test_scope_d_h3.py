"""Unit tests for Scope D (restyle_slides normalize_fonts) + H3 (brief_fields_used lineage)."""
from __future__ import annotations

from slides_mcp import server as server_mod


class TestInferBriefFieldsUsed:
    def test_no_brief_returns_empty(self):
        assert server_mod._infer_brief_fields_used({}, None) == []
        assert server_mod._infer_brief_fields_used({"title": "Hi"}, None) == []

    def test_palette_accent_used_when_no_override(self):
        brief = {"palette": {"accent": "#E8612E"}}
        used = server_mod._infer_brief_fields_used({"title": "Hi"}, brief)
        assert "palette.accent" in used

    def test_palette_accent_not_claimed_when_overridden(self):
        brief = {"palette": {"accent": "#E8612E"}}
        used = server_mod._infer_brief_fields_used(
            {"title": "Hi", "title_accent_hex": "#FF0000"}, brief
        )
        assert "palette.accent" not in used

    def test_palette_text_used_when_no_override(self):
        brief = {"palette": {"text": "#000000"}}
        used = server_mod._infer_brief_fields_used({"title": "Hi"}, brief)
        assert "palette.text" in used

    def test_palette_text_not_claimed_with_body_text_override(self):
        brief = {"palette": {"text": "#000000"}}
        used = server_mod._infer_brief_fields_used(
            {"title": "Hi", "body_text_color_hex": "#FF0000"}, brief
        )
        assert "palette.text" not in used

    def test_category_set_used_when_no_pill_palette(self):
        brief = {"palette": {"category_set": ["#E8612E", "#0F1A4A", "#5A6B9A"]}}
        used = server_mod._infer_brief_fields_used({"columns": [{"pill": "A", "body": "b"}]}, brief)
        assert "palette.category_set" in used

    def test_category_set_not_claimed_when_pill_palette_passed(self):
        brief = {"palette": {"category_set": ["#E8612E", "#0F1A4A"]}}
        used = server_mod._infer_brief_fields_used(
            {"pill_palette": ["#F00", "#0F0"], "columns": [{"pill": "A", "body": "b"}]},
            brief,
        )
        assert "palette.category_set" not in used

    def test_category_set_not_claimed_with_per_col_pill_hex(self):
        brief = {"palette": {"category_set": ["#E8612E"]}}
        used = server_mod._infer_brief_fields_used(
            {"columns": [{"pill": "A", "body": "b", "pill_hex": "#FF0000"}]},
            brief,
        )
        assert "palette.category_set" not in used

    def test_font_family_heading_and_body_listed(self):
        brief = {"font_family": {"heading": "Fraunces", "body": "Inter"}}
        used = server_mod._infer_brief_fields_used({}, brief)
        assert "font_family.heading" in used
        assert "font_family.body" in used

    def test_font_family_partial_axis_listed(self):
        brief = {"font_family": {"heading": "Fraunces"}}
        used = server_mod._infer_brief_fields_used({}, brief)
        assert used == ["font_family.heading"]

    def test_font_family_empty_string_not_listed(self):
        brief = {"font_family": {"heading": "  ", "body": "Inter"}}
        used = server_mod._infer_brief_fields_used({}, brief)
        assert "font_family.heading" not in used
        assert "font_family.body" in used

    def test_surface_not_claimed_yet(self):
        """surface is carried-but-unused today; don't falsely claim it."""
        brief = {"palette": {"surface": "#0F1A4A"}}
        used = server_mod._infer_brief_fields_used({}, brief)
        assert "palette.surface" not in used

    def test_combined_brief_lists_multiple_paths(self):
        brief = {
            "palette": {
                "accent": "#E8612E",
                "text": "#000000",
                "category_set": ["#E8612E", "#0F1A4A"],
            },
            "font_family": {"heading": "Fraunces", "body": "Inter"},
        }
        used = server_mod._infer_brief_fields_used({}, brief)
        assert set(used) >= {
            "palette.accent", "palette.text", "palette.category_set",
            "font_family.heading", "font_family.body",
        }


class TestRestyleSlidesNormalizeFontsSignature:
    """Smoke-level: the flag param exists and is defaulted False."""

    def test_normalize_fonts_default_false(self):
        import inspect
        sig = inspect.signature(server_mod.restyle_slides)
        assert "normalize_fonts" in sig.parameters
        assert sig.parameters["normalize_fonts"].default is False
