"""Unit tests for plan_deck MCP tool.

Mocks slides_api + get_deck_outline + write_theme_brief so these stay pure
unit tests — no Google API.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from slides_mcp import server


@pytest.fixture
def mock_api(monkeypatch):
    api = MagicMock()
    api.deck_id_from_url.return_value = "DECK_ID"
    monkeypatch.setattr(server, "slides_api", api)
    return api


@pytest.fixture
def mock_outline(monkeypatch):
    outline_slides = [
        {"slide_id": "s1", "title": "Welcome", "archetype": "cover_with_hero"},
        {"slide_id": "s2", "title": "The problem", "archetype": "text_heavy_body"},
        {"slide_id": "s3", "title": "Metrics", "archetype": "3col_pill_cards"},
        {"slide_id": "s4", "title": "Part II", "archetype": "section_opener"},
        {"slide_id": "s5", "title": "Next steps", "archetype": "text_heavy_body"},
    ]
    mock_fn = MagicMock(return_value={"deck_id": "DECK_ID", "slides": outline_slides})
    monkeypatch.setattr(server, "get_deck_outline", mock_fn)
    return mock_fn


@pytest.fixture
def mock_write_brief(monkeypatch):
    mock_fn = MagicMock(return_value={"slide_id": "theme_brief_x", "action": "updated"})
    monkeypatch.setattr(server, "write_theme_brief", mock_fn)
    return mock_fn


# ---------------------------------------------------------------------------
# free_text source
# ---------------------------------------------------------------------------


class TestPlanDeckFreeText:
    def test_free_text_short_intent(self, mock_api, mock_write_brief):
        result = server.plan_deck(
            deck_url="https://deck", intent="Pitch Q2 board on revenue growth"
        )
        assert result["status"] == "proposed"
        assert result["proposal_source"] == "free_text"
        assert result["confidence"] == "low"
        assert result["plan"]["vision"] == "Pitch Q2 board on revenue growth"
        assert result["plan"]["arc"] == "Pitch Q2 board on revenue growth"
        assert result["plan"]["sections"] == []
        assert result["plan"]["slides"] == []

    def test_free_text_long_intent_truncates_arc(self, mock_api, mock_write_brief):
        long_intent = "a" * 120
        result = server.plan_deck(
            deck_url="https://deck", intent=long_intent
        )
        assert result["plan"]["vision"] == long_intent
        assert result["plan"]["arc"].endswith("...")
        assert len(result["plan"]["arc"]) == 83  # 80 chars + "..."

    def test_free_text_commit_without_meta_slide_warns(self, mock_api, monkeypatch):
        fail_mock = MagicMock(
            side_effect=FileNotFoundError("no meta slide")
        )
        monkeypatch.setattr(server, "write_theme_brief", fail_mock)
        result = server.plan_deck(
            deck_url="https://deck", intent="Pitch", commit=True
        )
        assert result["status"] == "proposed"
        assert any("no meta brief" in w for w in result["warnings"])

    def test_free_text_commit_happy_path(
        self, mock_api, mock_write_brief
    ):
        result = server.plan_deck(
            deck_url="https://deck", intent="Pitch", commit=True
        )
        assert result["status"] == "committed"
        mock_write_brief.assert_called_once()
        call_kwargs = mock_write_brief.call_args.kwargs
        assert call_kwargs["mode"] == "merge"
        assert call_kwargs["delta"]["plan"]["vision"] == "Pitch"


# ---------------------------------------------------------------------------
# brownfield_deck source
# ---------------------------------------------------------------------------


class TestPlanDeckBrownfield:
    def test_brownfield_deck_builds_sections(
        self, mock_api, mock_outline, mock_write_brief
    ):
        result = server.plan_deck(
            deck_url="https://deck", source="brownfield_deck"
        )
        assert result["status"] == "proposed"
        assert result["proposal_source"] == "brownfield_deck"
        assert result["confidence"] == "medium"
        plan = result["plan"]
        assert len(plan["slides"]) == 5
        assert plan["slides"][0] == {
            "id": "s1",
            "intent": "Welcome",
            "archetype_hint": "cover_with_hero",
        }
        # Two section openers (cover_with_hero + section_opener) → two sections
        assert len(plan["sections"]) == 2
        # First section contains cover + intervening text slides
        assert plan["sections"][0]["slide_ids"] == ["s1", "s2", "s3"]
        assert plan["sections"][1]["slide_ids"] == ["s4", "s5"]

    def test_brownfield_deck_with_intent_override(
        self, mock_api, mock_outline, mock_write_brief
    ):
        result = server.plan_deck(
            deck_url="https://deck",
            intent="Custom vision",
            source="brownfield_deck",
        )
        assert result["plan"]["vision"] == "Custom vision"

    def test_brownfield_empty_deck(self, mock_api, mock_write_brief, monkeypatch):
        monkeypatch.setattr(
            server, "get_deck_outline",
            MagicMock(return_value={"deck_id": "DECK_ID", "slides": []}),
        )
        result = server.plan_deck(
            deck_url="https://deck", source="brownfield_deck"
        )
        assert result["plan"]["slides"] == []
        assert result["plan"]["sections"] == []


# ---------------------------------------------------------------------------
# doc source
# ---------------------------------------------------------------------------


class TestPlanDeckDoc:
    def test_doc_source_parses_markdown_headers(
        self, tmp_path: Path, mock_api, mock_write_brief
    ):
        doc = tmp_path / "plan.md"
        doc.write_text(
            "# Board pitch\n"
            "\n"
            "## Problem\n"
            "Body text\n"
            "\n"
            "## Solution\n"
            "More body\n"
            "\n"
            "## Ask\n"
            "Final\n"
        )
        result = server.plan_deck(
            deck_url="https://deck",
            source="doc",
            doc_path=str(doc),
        )
        assert result["proposal_source"] == "doc"
        assert result["confidence"] == "medium"
        assert result["plan"]["vision"] == "Board pitch"
        assert len(result["plan"]["sections"]) == 3
        assert result["plan"]["slides"][0]["intent"] == "Problem"
        assert result["plan"]["slides"][1]["intent"] == "Solution"
        assert result["plan"]["slides"][2]["intent"] == "Ask"

    def test_doc_source_low_confidence_when_few_sections(
        self, tmp_path: Path, mock_api, mock_write_brief
    ):
        doc = tmp_path / "sparse.md"
        doc.write_text("# Vision only\n\n## Only one section\n")
        result = server.plan_deck(
            deck_url="https://deck", source="doc", doc_path=str(doc)
        )
        assert result["confidence"] == "low"

    def test_doc_source_missing_path_raises(
        self, mock_api, mock_write_brief
    ):
        with pytest.raises(FileNotFoundError):
            server.plan_deck(
                deck_url="https://deck",
                source="doc",
                doc_path="/does/not/exist.md",
            )

    def test_doc_source_requires_doc_path(self, mock_api, mock_write_brief):
        with pytest.raises(ValueError, match="doc_path"):
            server.plan_deck(deck_url="https://deck", source="doc")

    def test_doc_source_emits_warning_when_no_h2(
        self, tmp_path: Path, mock_api, mock_write_brief
    ):
        doc = tmp_path / "flat.md"
        doc.write_text("# Just a title\n\nSome body text.\n")
        result = server.plan_deck(
            deck_url="https://deck", source="doc", doc_path=str(doc)
        )
        assert any("no H2 headers" in w for w in result["warnings"])
        assert result["plan"]["sections"] == []


# ---------------------------------------------------------------------------
# Validation + commit-path
# ---------------------------------------------------------------------------


class TestPlanDeckValidation:
    def test_unknown_source_raises(self, mock_api, mock_write_brief):
        with pytest.raises(ValueError, match="source must be one of"):
            server.plan_deck(deck_url="https://deck", source="bogus")

    def test_commit_true_calls_write_theme_brief_with_plan_delta(
        self, mock_api, mock_outline, mock_write_brief
    ):
        server.plan_deck(
            deck_url="https://deck",
            source="brownfield_deck",
            commit=True,
        )
        mock_write_brief.assert_called_once()
        call_kwargs = mock_write_brief.call_args.kwargs
        assert call_kwargs["mode"] == "merge"
        assert "plan" in call_kwargs["delta"]
        assert call_kwargs["delta"]["plan"]["slides"]  # non-empty

    def test_return_shape_always_has_all_keys(
        self, mock_api, mock_write_brief
    ):
        result = server.plan_deck(deck_url="https://deck", intent="x")
        for key in (
            "deck_id",
            "status",
            "plan",
            "proposal_source",
            "confidence",
            "next_step_hint",
            "warnings",
        ):
            assert key in result
