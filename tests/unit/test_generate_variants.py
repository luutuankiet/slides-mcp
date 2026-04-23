"""Unit tests for generate_variants + lock_variant composition.

Mocks set_theme_brief + create_slide + delete_slide + slides_api so these stay
pure unit tests — no Google API, no deck mutation.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from slides_mcp import server


@pytest.fixture
def two_briefs():
    """Two valid briefs with distinct accents."""
    return [
        {
            "version": 1,
            "palette": {
                "surface": "#0F1A4A", "accent": "#E8612E", "text": "#000000",
                "category_set": ["#E8612E", "#0F1A4A"],
            },
            "shape_language": "sharp",
            "numbering_style": "bold",
            "tone": "editorial",
            "image_prompt_style": "docu",
        },
        {
            "version": 1,
            "palette": {
                "surface": "#134E4A", "accent": "#B45309", "text": "#1F2937",
                "category_set": ["#B45309", "#134E4A"],
            },
            "shape_language": "sharp",
            "numbering_style": "outlined",
            "tone": "enterprise",
            "image_prompt_style": "editorial",
        },
    ]


@pytest.fixture
def mock_deps(monkeypatch):
    """Mock slides_api + set_theme_brief + create_slide + delete_slide."""
    api = MagicMock()
    api.deck_id_from_url.return_value = "DECK_ID"
    monkeypatch.setattr(server, "slides_api", api)

    set_brief_mock = MagicMock(return_value={"action": "created"})
    monkeypatch.setattr(server, "set_theme_brief", set_brief_mock)

    # create_slide returns the slide_id it was passed
    def create_slide_side_effect(*, deck_url, archetype, content, slide_id=None, theme_brief=True):
        return {"slide_id": slide_id, "brief_applied": theme_brief}
    create_mock = MagicMock(side_effect=create_slide_side_effect)
    monkeypatch.setattr(server, "create_slide", create_mock)

    delete_mock = MagicMock(return_value={"status": "deleted"})
    monkeypatch.setattr(server, "delete_slide", delete_mock)

    return {
        "api": api,
        "set_theme_brief": set_brief_mock,
        "create_slide": create_mock,
        "delete_slide": delete_mock,
    }


# ---------------------------------------------------------------------
# generate_variants
# ---------------------------------------------------------------------

class TestGenerateVariantsValidation:
    def test_empty_content_list_rejected(self, mock_deps, two_briefs):
        with pytest.raises(ValueError, match="content_list must be a non-empty list"):
            server.generate_variants(
                deck_url="https://deck", content_list=[], briefs=two_briefs,
            )

    def test_empty_briefs_rejected(self, mock_deps):
        with pytest.raises(ValueError, match="briefs must be a non-empty list"):
            server.generate_variants(
                deck_url="https://deck",
                content_list=[{"archetype": "text_heavy_body", "content": {"title": "x"}}],
                briefs=[],
            )

    def test_invalid_brief_rejected_before_any_write(self, mock_deps):
        bad_briefs = [{"version": 1}]  # missing palette, etc.
        with pytest.raises(ValueError, match="invalid"):
            server.generate_variants(
                deck_url="https://deck",
                content_list=[{"archetype": "text_heavy_body", "content": {"title": "x"}}],
                briefs=bad_briefs,
            )
        # No create_slide fired before validation caught the bad brief
        mock_deps["create_slide"].assert_not_called()
        mock_deps["set_theme_brief"].assert_not_called()

    def test_content_item_missing_archetype(self, mock_deps, two_briefs):
        with pytest.raises(ValueError, match="must be a dict with 'archetype'"):
            server.generate_variants(
                deck_url="https://deck",
                content_list=[{"content": {"title": "x"}}],
                briefs=two_briefs,
            )


class TestGenerateVariantsManifest:
    def test_returns_variant_per_brief(self, mock_deps, two_briefs):
        result = server.generate_variants(
            deck_url="https://deck",
            content_list=[
                {"archetype": "text_heavy_body", "content": {"title": "A"}, "slide_id": "cover"},
                {"archetype": "text_heavy_body", "content": {"title": "B"}, "slide_id": "body"},
            ],
            briefs=two_briefs,
            variant_prefix="eval",
        )
        assert result["deck_id"] == "DECK_ID"
        assert result["variant_prefix"] == "eval"
        assert result["total_slides_created"] == 4
        assert len(result["variants"]) == 2
        assert result["variants"][0]["variant_id"] == "eval0"
        assert result["variants"][0]["slide_ids"] == ["eval0_cover", "eval0_body"]
        assert result["variants"][1]["variant_id"] == "eval1"
        assert result["variants"][1]["slide_ids"] == ["eval1_cover", "eval1_body"]

    def test_sets_brief_per_variant(self, mock_deps, two_briefs):
        server.generate_variants(
            deck_url="https://deck",
            content_list=[{"archetype": "text_heavy_body", "content": {"title": "x"}}],
            briefs=two_briefs,
        )
        # set_theme_brief called once per brief, in order
        calls = mock_deps["set_theme_brief"].call_args_list
        assert len(calls) == 2
        assert calls[0][0] == ("https://deck", two_briefs[0])
        assert calls[1][0] == ("https://deck", two_briefs[1])

    def test_auto_generates_slide_id_suffix(self, mock_deps, two_briefs):
        result = server.generate_variants(
            deck_url="https://deck",
            content_list=[
                {"archetype": "text_heavy_body", "content": {"title": "x"}},
                {"archetype": "text_heavy_body", "content": {"title": "y"}},
            ],
            briefs=[two_briefs[0]],  # just 1 brief
        )
        # Default suffixes: s0, s1
        assert result["variants"][0]["slide_ids"] == ["v0_s0", "v0_s1"]

    def test_default_variant_prefix(self, mock_deps, two_briefs):
        result = server.generate_variants(
            deck_url="https://deck",
            content_list=[{"archetype": "text_heavy_body", "content": {"title": "x"}, "slide_id": "cover"}],
            briefs=two_briefs,
        )
        assert result["variants"][0]["slide_ids"] == ["v0_cover"]
        assert result["variants"][1]["slide_ids"] == ["v1_cover"]


# ---------------------------------------------------------------------
# lock_variant
# ---------------------------------------------------------------------

class TestLockVariantValidation:
    def test_missing_variants_key(self, mock_deps):
        with pytest.raises(ValueError, match="must be a non-empty list"):
            server.lock_variant(
                deck_url="https://deck", variant_id="v0", variants_manifest={},
            )

    def test_empty_variants_list(self, mock_deps):
        with pytest.raises(ValueError, match="must be a non-empty list"):
            server.lock_variant(
                deck_url="https://deck", variant_id="v0",
                variants_manifest={"variants": []},
            )

    def test_variant_id_not_found(self, mock_deps):
        manifest = {"variants": [
            {"variant_id": "v0", "brief": {}, "slide_ids": ["v0_s0"]},
        ]}
        with pytest.raises(ValueError, match="not found in manifest; available:"):
            server.lock_variant(
                deck_url="https://deck", variant_id="missing",
                variants_manifest=manifest,
            )

    def test_winner_without_brief(self, mock_deps):
        manifest = {"variants": [
            {"variant_id": "v0", "slide_ids": ["v0_s0"]},  # no brief
        ]}
        with pytest.raises(ValueError, match="has no 'brief'"):
            server.lock_variant(
                deck_url="https://deck", variant_id="v0",
                variants_manifest=manifest,
            )


class TestLockVariantBehavior:
    def _manifest(self, two_briefs):
        return {
            "deck_id": "DECK_ID",
            "variant_prefix": "v",
            "variants": [
                {"variant_id": "v0", "brief": two_briefs[0],
                 "slide_ids": ["v0_cover", "v0_body"]},
                {"variant_id": "v1", "brief": two_briefs[1],
                 "slide_ids": ["v1_cover", "v1_body"]},
            ],
        }

    def test_winner_brief_promoted(self, mock_deps, two_briefs):
        manifest = self._manifest(two_briefs)
        result = server.lock_variant(
            deck_url="https://deck", variant_id="v1",
            variants_manifest=manifest,
        )
        mock_deps["set_theme_brief"].assert_called_once_with("https://deck", two_briefs[1])
        assert result["locked_variant_id"] == "v1"
        assert result["locked_brief"] == two_briefs[1]
        assert result["kept_slide_ids"] == ["v1_cover", "v1_body"]

    def test_losers_deleted(self, mock_deps, two_briefs):
        manifest = self._manifest(two_briefs)
        result = server.lock_variant(
            deck_url="https://deck", variant_id="v1",
            variants_manifest=manifest,
        )
        deleted = [
            call.kwargs["slide_id"] for call in mock_deps["delete_slide"].call_args_list
        ]
        assert deleted == ["v0_cover", "v0_body"]
        assert result["deleted_slide_count"] == 2
        assert result["deleted_slide_ids"] == ["v0_cover", "v0_body"]

    def test_delete_failure_captured_as_warning(self, mock_deps, two_briefs):
        mock_deps["delete_slide"].side_effect = [
            {"status": "deleted"},
            RuntimeError("boom"),
        ]
        manifest = self._manifest(two_briefs)
        result = server.lock_variant(
            deck_url="https://deck", variant_id="v1",
            variants_manifest=manifest,
        )
        assert result["deleted_slide_count"] == 1
        assert len(result["warnings"]) == 1
        assert "boom" in result["warnings"][0]

    def test_winner_with_no_slides_still_succeeds(self, mock_deps, two_briefs):
        manifest = {
            "variants": [
                {"variant_id": "v0", "brief": two_briefs[0], "slide_ids": []},
                {"variant_id": "v1", "brief": two_briefs[1], "slide_ids": ["v1_s0"]},
            ]
        }
        result = server.lock_variant(
            deck_url="https://deck", variant_id="v0",
            variants_manifest=manifest,
        )
        assert result["kept_slide_ids"] == []
        assert result["deleted_slide_ids"] == ["v1_s0"]
