"""Unit tests for Scope E: render_archetype_preview (PIL dry-run)."""
from __future__ import annotations

import io

from PIL import Image

from slides_mcp import swatch

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _teal_copper_brief() -> dict:
    return {
        "palette": {
            "surface": "#134E4A",
            "accent": "#B45309",
            "text": "#FFFFFF",
            "category_set": ["#B45309", "#134E4A", "#A16207"],
        },
        "shape_language": "sharp",
        "numbering_style": "outlined",
        "font_family": {"heading": "Fraunces", "body": "Inter"},
    }


class TestRenderArchetypePreview:
    def test_returns_valid_png(self):
        png = swatch.render_archetype_preview(
            "text_heavy_body",
            {"title": "Hi", "paragraphs": ["one para"]},
            _teal_copper_brief(),
        )
        assert png.startswith(PNG_MAGIC)

    def test_preview_dimensions_default(self):
        png = swatch.render_archetype_preview(
            "3col_pill_cards",
            {"title": "T", "columns": [{"pill": "A", "body": "b"}] * 3},
            _teal_copper_brief(),
        )
        img = Image.open(io.BytesIO(png))
        assert img.size == (swatch.PREVIEW_W, swatch.PREVIEW_H)

    def test_none_brief_uses_defaults(self):
        """Must render without brief — defaults kick in."""
        png = swatch.render_archetype_preview(
            "cover_with_hero",
            {"title": "Greenfield"},
            None,
        )
        assert png.startswith(PNG_MAGIC)

    def test_empty_brief_uses_defaults(self):
        png = swatch.render_archetype_preview(
            "cover_with_hero",
            {"title": "Greenfield"},
            {},
        )
        assert png.startswith(PNG_MAGIC)

    def test_all_supported_archetypes(self):
        brief = _teal_copper_brief()
        content_map = {
            "cover_with_hero": {"title": "Cover", "subtitle": "Sub"},
            "text_left_image_right": {"title": "TLIR", "body": "body text"},
            "3col_pill_cards": {
                "title": "3col", "columns": [
                    {"pill": "A", "body": "a body"},
                    {"pill": "B", "body": "b body"},
                    {"pill": "C", "body": "c body"},
                ],
            },
            "4col_numbered_flow": {
                "title": "4col", "columns": [
                    {"num": "1", "subtitle": "s1", "body": "b1"},
                    {"num": "2", "subtitle": "s2", "body": "b2"},
                    {"num": "3", "subtitle": "s3", "body": "b3"},
                    {"num": "4", "subtitle": "s4", "body": "b4"},
                ],
            },
            "text_heavy_body": {"title": "TH", "paragraphs": ["p1", "p2"]},
        }
        for arch, content in content_map.items():
            png = swatch.render_archetype_preview(arch, content, brief)
            assert png.startswith(PNG_MAGIC), f"PNG invalid for {arch}"

    def test_unknown_archetype_fallback_renders(self):
        png = swatch.render_archetype_preview(
            "nonexistent_archetype",
            {"title": "Unknown"},
            _teal_copper_brief(),
        )
        assert png.startswith(PNG_MAGIC)

    def test_preview_respects_brief_palette(self):
        """Different briefs should produce different pixels."""
        content = {"title": "Compare", "paragraphs": ["body"]}
        brief_a = _teal_copper_brief()
        brief_b = dict(brief_a)
        brief_b["palette"] = {
            "surface": "#FFFFFF",
            "accent": "#2563EB",
            "text": "#000000",
            "category_set": ["#2563EB"],
        }
        png_a = swatch.render_archetype_preview("text_heavy_body", content, brief_a)
        png_b = swatch.render_archetype_preview("text_heavy_body", content, brief_b)
        assert png_a != png_b, "different palettes must produce different pixels"

    def test_preview_respects_shape_language(self):
        content = {"title": "T", "columns": [{"pill": "A", "body": "b"}] * 3}
        brief_sharp = _teal_copper_brief()
        brief_round = dict(brief_sharp)
        brief_round["shape_language"] = "rounded"
        png_s = swatch.render_archetype_preview("3col_pill_cards", content, brief_sharp)
        png_r = swatch.render_archetype_preview("3col_pill_cards", content, brief_round)
        assert png_s != png_r, "shape_language should affect pill radius"

    def test_preview_handles_minimal_content(self):
        """Missing optional keys should not crash."""
        png = swatch.render_archetype_preview(
            "3col_pill_cards",
            {"title": "bare"},  # no columns
            None,
        )
        assert png.startswith(PNG_MAGIC)
