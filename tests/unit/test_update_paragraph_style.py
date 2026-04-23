"""Unit tests for update_paragraph_style MCP tool.

Mocks slides_api; validates request emission (updateParagraphStyle kind),
paragraph-style normalization, range resolution (via shared _resolve_text_range),
thumbnail contract, and error paths.
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


class TestUpdateParagraphStyleAll:
    def test_all_range_alignment(self, mock_slides_api):
        result = server.update_paragraph_style(
            deck_url="https://deck",
            slide_id="slide_1",
            object_id="body_id",
            style={"alignment": "CENTER"},
            range="all",
        )
        assert result["range_resolved"] == {"type": "ALL"}
        assert result["fields"] == ["alignment"]
        req = mock_slides_api.batch_update.call_args[0][1][0]
        assert "updateParagraphStyle" in req
        assert req["updateParagraphStyle"]["objectId"] == "body_id"
        assert req["updateParagraphStyle"]["style"] == {"alignment": "CENTER"}
        assert req["updateParagraphStyle"]["textRange"] == {"type": "ALL"}
        assert req["updateParagraphStyle"]["fields"] == "alignment"

    def test_all_range_skips_get_slide(self, mock_slides_api):
        server.update_paragraph_style(
            deck_url="https://deck",
            slide_id="slide_1",
            object_id="body_id",
            style={"alignment": "CENTER"},
            range="all",
        )
        mock_slides_api.get_slide.assert_not_called()

    def test_none_same_as_all(self, mock_slides_api):
        result = server.update_paragraph_style(
            deck_url="https://deck",
            slide_id="slide_1",
            object_id="body_id",
            style={"alignment": "CENTER"},
            range=None,
        )
        assert result["range_resolved"] == {"type": "ALL"}


class TestUpdateParagraphStyleScoped:
    def test_paragraph_range_resolves(self, mock_slides_api):
        mock_slides_api.get_slide.return_value = _page_with_shape(
            "body_id", ["First paragraph.\n", "Second paragraph.\n"]
        )
        result = server.update_paragraph_style(
            deck_url="https://deck",
            slide_id="slide_1",
            object_id="body_id",
            style={"lineSpacing": 150},
            range={"paragraph": 1},
        )
        # 'First paragraph.' (16 chars) + '\n' (17) + 'Second paragraph.' (17..34)
        assert result["range_resolved"] == {
            "type": "FIXED_RANGE",
            "startIndex": 17,
            "endIndex": 34,
        }

    def test_match_range_resolves(self, mock_slides_api):
        mock_slides_api.get_slide.return_value = _page_with_shape(
            "body_id", ["The quote lives here.\n"]
        )
        result = server.update_paragraph_style(
            deck_url="https://deck",
            slide_id="slide_1",
            object_id="body_id",
            style={"alignment": "CENTER", "indentStart": 24},
            range={"match": "quote"},
        )
        assert result["range_resolved"]["type"] == "FIXED_RANGE"
        assert result["range_resolved"]["startIndex"] == 4
        assert result["range_resolved"]["endIndex"] == 9
        assert set(result["fields"]) == {"alignment", "indentStart"}


class TestUpdateParagraphStyleSubset:
    def test_multiple_style_props(self, mock_slides_api):
        server.update_paragraph_style(
            deck_url="https://deck",
            slide_id="slide_1",
            object_id="body_id",
            style={
                "alignment": "JUSTIFIED",
                "lineSpacing": 150,
                "spaceAbove": 12,
                "spaceBelow": 12,
                "indentStart": 36,
            },
        )
        req = mock_slides_api.batch_update.call_args[0][1][0]["updateParagraphStyle"]
        assert set(req["fields"].split(",")) == {
            "alignment", "lineSpacing", "spaceAbove", "spaceBelow", "indentStart",
        }
        assert req["style"]["alignment"] == "JUSTIFIED"
        assert req["style"]["lineSpacing"] == 150.0
        assert req["style"]["spaceAbove"] == {"magnitude": 12.0, "unit": "PT"}
        assert req["style"]["indentStart"] == {"magnitude": 36.0, "unit": "PT"}

    def test_rejects_character_style_keys(self, mock_slides_api):
        # fontSize / bold are character-scope, not paragraph-scope
        with pytest.raises(ValueError, match="unknown paragraph style key"):
            server.update_paragraph_style(
                deck_url="https://deck",
                slide_id="slide_1",
                object_id="body_id",
                style={"bold": True},
            )

    def test_rejects_invalid_alignment(self, mock_slides_api):
        with pytest.raises(ValueError, match="alignment must be one of"):
            server.update_paragraph_style(
                deck_url="https://deck",
                slide_id="slide_1",
                object_id="body_id",
                style={"alignment": "DIAGONAL"},
            )


class TestUpdateParagraphStyleVerify:
    def test_verify_auto_fires_thumbnail(self, mock_slides_api):
        result = server.update_paragraph_style(
            deck_url="https://deck",
            slide_id="slide_1",
            object_id="body_id",
            style={"alignment": "CENTER"},
        )
        assert result["thumbnail_url"] == "https://thumbnail.example/x"

    def test_verify_never_skips(self, mock_slides_api):
        result = server.update_paragraph_style(
            deck_url="https://deck",
            slide_id="slide_1",
            object_id="body_id",
            style={"alignment": "CENTER"},
            verify="never",
        )
        assert "thumbnail_url" not in result
        mock_slides_api.get_thumbnail.assert_not_called()


class TestUpdateParagraphStyleValidation:
    def test_missing_object_id(self, mock_slides_api):
        with pytest.raises(ValueError, match="object_id required"):
            server.update_paragraph_style(
                deck_url="https://deck",
                slide_id="slide_1",
                object_id="",
                style={"alignment": "CENTER"},
            )

    def test_empty_style_rejected(self, mock_slides_api):
        with pytest.raises(ValueError, match="non-empty dict"):
            server.update_paragraph_style(
                deck_url="https://deck",
                slide_id="slide_1",
                object_id="body_id",
                style={},
            )
