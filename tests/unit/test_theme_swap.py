"""Unit tests for theme_swap MCP tool.

Mocks slides_api + clone_deck + apply_brief_and_restyle + write_theme_brief +
theme_brief_mod helpers so these stay pure unit tests.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from slides_mcp import server


@pytest.fixture
def source_brief_with_assets():
    return {
        "version": 1,
        "palette": {
            "surface": "#0F1A4A",
            "accent": "#E8612E",
            "text": "#000000",
            "category_set": ["#E8612E", "#0F1A4A"],
        },
        "shape_language": "sharp",
        "brand_assets": [
            {
                "id": "client_name",
                "type": "text",
                "match": "Joon Solutions",
                "role": "client",
            },
            {
                "id": "date",
                "type": "text",
                "match": "January 2026",
                "role": "date",
            },
            {
                "id": "client_logo",
                "type": "image",
                "match": "logo_shape_id_1",
                "role": "client",
            },
        ],
    }


@pytest.fixture
def mock_deps(monkeypatch, source_brief_with_assets):
    api = MagicMock()
    api.deck_id_from_url.side_effect = lambda url: (
        "SRC_ID" if "source" in url else "NEW_ID"
    )
    api.batch_update = MagicMock(return_value={"replies": []})
    monkeypatch.setattr(server, "slides_api", api)

    # _fetch_for_brief returns a prez shape that theme_brief_mod.find_meta_slide
    # can parse — we short-circuit by patching the helpers directly
    monkeypatch.setattr(
        server, "_fetch_for_brief",
        MagicMock(return_value={"slides": []}),
    )

    theme_brief_mod = MagicMock()
    theme_brief_mod.find_meta_slide = MagicMock(
        return_value={
            "slide_id": "meta",
            "body_box_id": "body",
            "body_text": "stub",
            "marker_box_id": "marker",
        }
    )
    theme_brief_mod.parse_brief_body = MagicMock(
        return_value=source_brief_with_assets
    )
    monkeypatch.setattr(server, "theme_brief_mod", theme_brief_mod)

    clone_mock = MagicMock(
        return_value={
            "src_deck_id": "SRC_ID",
            "new_deck_id": "NEW_ID",
            "new_deck_url": "https://docs.google.com/presentation/d/NEW_ID/edit",
            "replacements_applied": [],
        }
    )
    monkeypatch.setattr(server, "clone_deck", clone_mock)

    apply_mock = MagicMock(
        return_value={"deck_id": "NEW_ID", "action": "updated"}
    )
    monkeypatch.setattr(server, "apply_brief_and_restyle", apply_mock)

    write_brief_mock = MagicMock(
        return_value={"slide_id": "meta", "action": "updated"}
    )
    monkeypatch.setattr(server, "write_theme_brief", write_brief_mock)

    return {
        "api": api,
        "clone": clone_mock,
        "apply": apply_mock,
        "write_brief": write_brief_mock,
        "theme_brief_mod": theme_brief_mod,
    }


# ---------------------------------------------------------------------------
# Guard rails
# ---------------------------------------------------------------------------


class TestThemeSwapGuards:
    def test_confirm_destructive_required(self, mock_deps):
        with pytest.raises(ValueError, match="confirm_destructive"):
            server.theme_swap(
                source_deck_url="https://source",
                new_title="New",
                confirm_destructive=False,
            )

    def test_cannot_pass_both_target_brief_and_delta(self, mock_deps):
        with pytest.raises(ValueError, match="at most one"):
            server.theme_swap(
                source_deck_url="https://source",
                new_title="New",
                target_brief={"palette": {}},
                target_brief_delta={"palette": {}},
                confirm_destructive=True,
            )

    def test_source_without_meta_slide_raises(self, mock_deps):
        mock_deps["theme_brief_mod"].find_meta_slide.return_value = None
        with pytest.raises(ValueError, match="no brief"):
            server.theme_swap(
                source_deck_url="https://source",
                new_title="New",
                confirm_destructive=True,
            )

    def test_source_without_brand_assets_raises(self, mock_deps):
        mock_deps["theme_brief_mod"].parse_brief_body.return_value = {
            "version": 1,
            "palette": {"surface": "#000000"},
        }
        with pytest.raises(ValueError, match="brand_assets"):
            server.theme_swap(
                source_deck_url="https://source",
                new_title="New",
                confirm_destructive=True,
            )


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


class TestThemeSwapBehavior:
    def test_text_swap_emits_replace_all_text(self, mock_deps):
        result = server.theme_swap(
            source_deck_url="https://source",
            new_title="Client X pitch",
            asset_overrides={
                "client_name": "Acme Corp",
                "date": "April 2026",
            },
            confirm_destructive=True,
        )
        # Clone happened
        mock_deps["clone"].assert_called_once()
        # apply_brief_and_restyle NOT called (no target_brief or delta)
        mock_deps["apply"].assert_not_called()
        # batch_update called with replaceAllText on new deck
        call_args = mock_deps["api"].batch_update.call_args_list
        # Expect one batch for text swaps + one potential brief merge
        assert len(call_args) >= 1
        text_call = call_args[0]
        deck_arg = text_call.args[0]
        assert deck_arg == "NEW_ID"
        requests = text_call.args[1]
        assert all("replaceAllText" in r for r in requests)
        assert result["new_deck_id"] == "NEW_ID"
        assert len(result["assets_swapped"]) == 2
        assert result["restyle_applied"] is False

    def test_image_swap_emits_replace_image(self, mock_deps):
        result = server.theme_swap(
            source_deck_url="https://source",
            new_title="Client logo swap",
            asset_overrides={
                "client_logo": "https://example.com/new_logo.png",
            },
            confirm_destructive=True,
        )
        call_args = mock_deps["api"].batch_update.call_args_list
        # image_requests list only — no text
        assert len(call_args) == 1
        img_requests = call_args[0].args[1]
        assert all("replaceImage" in r for r in img_requests)
        assert img_requests[0]["replaceImage"]["imageObjectId"] == "logo_shape_id_1"
        assert img_requests[0]["replaceImage"]["imageReplaceMethod"] == "CENTER_INSIDE"
        assert len(result["assets_swapped"]) == 1
        assert result["assets_swapped"][0]["type"] == "image"

    def test_unknown_asset_id_warns_not_raises(self, mock_deps):
        result = server.theme_swap(
            source_deck_url="https://source",
            new_title="x",
            asset_overrides={
                "client_name": "Acme",
                "nonexistent": "whatever",
            },
            confirm_destructive=True,
        )
        assert len(result["assets_swapped"]) == 1
        assert any(
            "nonexistent" in w and "skipped" in w for w in result["warnings"]
        )

    def test_target_brief_triggers_apply_brief_and_restyle(self, mock_deps):
        target = {"palette": {"surface": "#FFFFFF", "accent": "#2563EB"}}
        result = server.theme_swap(
            source_deck_url="https://source",
            new_title="Retheme",
            target_brief=target,
            confirm_destructive=True,
        )
        mock_deps["apply"].assert_called_once()
        call_kwargs = mock_deps["apply"].call_args.kwargs
        assert call_kwargs["brief"] == target
        assert call_kwargs["confirm_destructive"] is True
        assert result["restyle_applied"] is True

    def test_target_brief_delta_merged_on_clone(self, mock_deps):
        delta = {"shape_language": "rounded"}
        server.theme_swap(
            source_deck_url="https://source",
            new_title="x",
            target_brief_delta=delta,
            confirm_destructive=True,
        )
        mock_deps["apply"].assert_called_once()
        call_kwargs = mock_deps["apply"].call_args.kwargs
        assert call_kwargs["delta"] == delta

    def test_brand_assets_updated_on_new_deck_brief(self, mock_deps):
        server.theme_swap(
            source_deck_url="https://source",
            new_title="x",
            asset_overrides={"client_name": "Acme Corp"},
            confirm_destructive=True,
        )
        # write_theme_brief invoked to refresh brand_assets.match
        assert mock_deps["write_brief"].called
        refresh_call = mock_deps["write_brief"].call_args
        assert refresh_call.kwargs["mode"] == "merge"
        updated_assets = refresh_call.kwargs["delta"]["brand_assets"]
        client_entry = next(a for a in updated_assets if a["id"] == "client_name")
        assert client_entry["match"] == "Acme Corp"

    def test_asset_swap_only_no_restyle(self, mock_deps):
        result = server.theme_swap(
            source_deck_url="https://source",
            new_title="x",
            asset_overrides={"client_name": "Acme"},
            confirm_destructive=True,
        )
        assert result["restyle_applied"] is False
        mock_deps["apply"].assert_not_called()

    def test_empty_asset_overrides_still_clones(self, mock_deps):
        result = server.theme_swap(
            source_deck_url="https://source",
            new_title="x",
            confirm_destructive=True,
        )
        mock_deps["clone"].assert_called_once()
        assert result["assets_swapped"] == []
