"""Unit tests for update_text_style MCP tool.

Mocks slides_api; validates request emission + range resolution + thumbnail
contract without hitting Google's API.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from slides_mcp import server


@pytest.fixture
def mock_slides_api(monkeypatch):
    api = MagicMock()
    api.deck_id_from_url.return_value = "DECK_ID"
    api.batch_update.return_value = {"replies": [{}]}
    api.get_thumbnail.return_value = "https://thumbnail.example/x"
    api.get_slide.return_value = {"pageElements": []}
    monkeypatch.setattr(server, "slides_api", api)
    return api


def _page_with_shape(object_id: str, runs: list[str]) -> dict:
    text_elements = [{"textRun": {"content": c}} for c in runs]
    return {"pageElements": [{
        "objectId": object_id,
        "shape": {"text": {"textElements": text_elements}},
    }]}


class TestUpdateTextStyleAllRange:
    def test_all_range_basic_bold(self, mock_slides_api):
        result = server.update_text_style(
            deck_url="https://deck",
            slide_id="slide_1",
            object_id="shape_a",
            style={"bold": True},
            range="all",
        )
        assert result["deck_id"] == "DECK_ID"
        assert result["slide_id"] == "slide_1"
        assert result["object_id"] == "shape_a"
        assert result["range_resolved"] == {"type": "ALL"}
        assert result["fields"] == ["bold"]
        assert result["applied_request_count"] == 1

    def test_all_range_emits_correct_request(self, mock_slides_api):
        server.update_text_style(
            deck_url="https://deck",
            slide_id="slide_1",
            object_id="shape_a",
            style={"bold": True},
            range="all",
        )
        args, _ = mock_slides_api.batch_update.call_args
        deck_id, requests = args
        assert deck_id == "DECK_ID"
        assert len(requests) == 1
        req = requests[0]["updateTextStyle"]
        assert req["objectId"] == "shape_a"
        assert req["style"] == {"bold": True}
        assert req["textRange"] == {"type": "ALL"}
        assert req["fields"] == "bold"

    def test_range_none_treated_as_all(self, mock_slides_api):
        result = server.update_text_style(
            deck_url="https://deck",
            slide_id="slide_1",
            object_id="shape_a",
            style={"italic": True},
            range=None,
        )
        assert result["range_resolved"] == {"type": "ALL"}
        # range=all / None skips the slide fetch (faster + fewer API calls)
        mock_slides_api.get_slide.assert_not_called()


class TestUpdateTextStyleScopedRange:
    def test_match_unique_fetches_slide_and_resolves(self, mock_slides_api):
        mock_slides_api.get_slide.return_value = _page_with_shape(
            "shape_a", ["Hello World\n"]
        )
        result = server.update_text_style(
            deck_url="https://deck",
            slide_id="slide_1",
            object_id="shape_a",
            style={"bold": True},
            range={"match": "World"},
        )
        assert result["range_resolved"] == {
            "type": "FIXED_RANGE",
            "startIndex": 6,
            "endIndex": 11,
        }
        mock_slides_api.get_slide.assert_called_once_with("DECK_ID", "slide_1")

    def test_match_ambiguous_raises(self, mock_slides_api):
        mock_slides_api.get_slide.return_value = _page_with_shape(
            "shape_a", ["foo bar foo"]
        )
        with pytest.raises(ValueError, match="ambiguous"):
            server.update_text_style(
                deck_url="https://deck",
                slide_id="slide_1",
                object_id="shape_a",
                style={"bold": True},
                range={"match": "foo"},
            )
        # no batch_update fires on failed range resolution
        mock_slides_api.batch_update.assert_not_called()

    def test_match_not_found_raises(self, mock_slides_api):
        mock_slides_api.get_slide.return_value = _page_with_shape(
            "shape_a", ["Hello World"]
        )
        with pytest.raises(ValueError, match="not found in shape text"):
            server.update_text_style(
                deck_url="https://deck",
                slide_id="slide_1",
                object_id="shape_a",
                style={"bold": True},
                range={"match": "Missing"},
            )

    def test_paragraph_resolves_indices(self, mock_slides_api):
        mock_slides_api.get_slide.return_value = _page_with_shape(
            "shape_a", ["First\n", "Second\n"]
        )
        result = server.update_text_style(
            deck_url="https://deck",
            slide_id="slide_1",
            object_id="shape_a",
            style={"fontSize": 24},
            range={"paragraph": 1},
        )
        # "First\nSecond\n" → paragraph 1 = "Second" at 6..12
        assert result["range_resolved"] == {
            "type": "FIXED_RANGE",
            "startIndex": 6,
            "endIndex": 12,
        }

    def test_chars_validated_against_real_text(self, mock_slides_api):
        mock_slides_api.get_slide.return_value = _page_with_shape(
            "shape_a", ["0123456789\n"]
        )
        result = server.update_text_style(
            deck_url="https://deck",
            slide_id="slide_1",
            object_id="shape_a",
            style={"bold": True},
            range={"chars": [2, 5]},
        )
        assert result["range_resolved"] == {
            "type": "FIXED_RANGE",
            "startIndex": 2,
            "endIndex": 5,
        }

    def test_chars_out_of_bounds_raises(self, mock_slides_api):
        mock_slides_api.get_slide.return_value = _page_with_shape(
            "shape_a", ["abc"]
        )
        with pytest.raises(ValueError, match="chars end 99 exceeds text length"):
            server.update_text_style(
                deck_url="https://deck",
                slide_id="slide_1",
                object_id="shape_a",
                style={"bold": True},
                range={"chars": [0, 99]},
            )

    def test_unknown_object_id_raises(self, mock_slides_api):
        mock_slides_api.get_slide.return_value = _page_with_shape(
            "other_shape", ["x"]
        )
        with pytest.raises(ValueError, match="not found on slide"):
            server.update_text_style(
                deck_url="https://deck",
                slide_id="slide_1",
                object_id="missing_shape",
                style={"bold": True},
                range={"match": "x"},
            )


class TestUpdateTextStyleVerify:
    def test_verify_auto_fires_thumbnail(self, mock_slides_api):
        result = server.update_text_style(
            deck_url="https://deck",
            slide_id="slide_1",
            object_id="shape_a",
            style={"bold": True},
            verify="auto",
        )
        assert result["thumbnail_url"] == "https://thumbnail.example/x"
        mock_slides_api.get_thumbnail.assert_called_once_with(
            "DECK_ID", "slide_1", size="MEDIUM"
        )

    def test_verify_always_fires_thumbnail(self, mock_slides_api):
        result = server.update_text_style(
            deck_url="https://deck",
            slide_id="slide_1",
            object_id="shape_a",
            style={"bold": True},
            verify="always",
        )
        assert "thumbnail_url" in result

    def test_verify_never_skips_thumbnail(self, mock_slides_api):
        result = server.update_text_style(
            deck_url="https://deck",
            slide_id="slide_1",
            object_id="shape_a",
            style={"bold": True},
            verify="never",
        )
        assert "thumbnail_url" not in result
        mock_slides_api.get_thumbnail.assert_not_called()


class TestUpdateTextStyleValidation:
    def test_missing_object_id(self, mock_slides_api):
        with pytest.raises(ValueError, match="object_id required"):
            server.update_text_style(
                deck_url="https://deck",
                slide_id="slide_1",
                object_id="",
                style={"bold": True},
            )
        mock_slides_api.batch_update.assert_not_called()

    def test_unknown_style_key_propagates(self, mock_slides_api):
        with pytest.raises(ValueError, match="unknown text style key"):
            server.update_text_style(
                deck_url="https://deck",
                slide_id="slide_1",
                object_id="shape_a",
                style={"flashing": True},
            )
        mock_slides_api.batch_update.assert_not_called()

    def test_empty_style_rejected(self, mock_slides_api):
        with pytest.raises(ValueError, match="non-empty dict"):
            server.update_text_style(
                deck_url="https://deck",
                slide_id="slide_1",
                object_id="shape_a",
                style={},
            )


class TestUpdateTextStyleCompound:
    def test_compound_style_fields_joined(self, mock_slides_api):
        server.update_text_style(
            deck_url="https://deck",
            slide_id="slide_1",
            object_id="shape_a",
            style={"bold": True, "fontSize": 36, "foregroundColor": "#FF0000"},
        )
        args, _ = mock_slides_api.batch_update.call_args
        req = args[1][0]["updateTextStyle"]
        assert set(req["fields"].split(",")) == {"bold", "fontSize", "foregroundColor"}
        assert req["style"]["bold"] is True
        assert req["style"]["fontSize"] == {"magnitude": 36.0, "unit": "PT"}
        assert req["style"]["foregroundColor"] == {
            "opaqueColor": {"rgbColor": {"red": 1.0, "green": 0.0, "blue": 0.0}}
        }

    def test_font_family_and_weight(self, mock_slides_api):
        server.update_text_style(
            deck_url="https://deck",
            slide_id="slide_1",
            object_id="shape_a",
            style={"weightedFontFamily": {"fontFamily": "Inter", "weight": 700}},
        )
        req = mock_slides_api.batch_update.call_args[0][1][0]["updateTextStyle"]
        assert req["style"]["weightedFontFamily"] == {
            "fontFamily": "Inter",
            "weight": 700,
        }
