"""Unit tests for propose_brief_variants (pure function in theme_brief)."""
from __future__ import annotations

import pytest

from slides_mcp import theme_brief as tb


class TestBasicBehavior:
    def test_default_n_returns_3(self):
        result = tb.propose_brief_variants("some generic intent")
        assert len(result) == 3

    def test_n_zero_returns_empty(self):
        assert tb.propose_brief_variants("any", n=0) == []

    def test_n_negative_returns_empty(self):
        assert tb.propose_brief_variants("any", n=-1) == []

    def test_n_capped_at_pool_size(self):
        result = tb.propose_brief_variants("any", n=100)
        assert len(result) == len(tb._MOOD_TEMPLATES)

    def test_empty_intent_still_returns_n(self):
        result = tb.propose_brief_variants("", n=3)
        assert len(result) == 3


class TestBriefShape:
    def test_every_brief_is_valid(self):
        result = tb.propose_brief_variants("enterprise brief", n=5)
        for brief in result:
            ok, errors = tb.validate_brief(brief)
            assert ok, f"brief failed validation: {errors}\nbrief={brief}"

    def test_brief_has_expected_keys(self):
        result = tb.propose_brief_variants("x", n=1)
        brief = result[0]
        assert "version" in brief
        assert "palette" in brief
        assert set(brief["palette"].keys()) >= {"surface", "accent", "text", "category_set"}
        assert "shape_language" in brief
        assert "numbering_style" in brief
        assert "tone" in brief
        assert "image_prompt_style" in brief


class TestDistinctness:
    def test_accents_are_unique(self):
        result = tb.propose_brief_variants("mixed intent", n=5)
        accents = [b["palette"]["accent"] for b in result]
        assert len(accents) == len(set(accents)), f"accents not unique: {accents}"

    def test_accents_unique_with_max_n(self):
        result = tb.propose_brief_variants("any", n=100)
        accents = [b["palette"]["accent"] for b in result]
        assert len(accents) == len(set(accents))


class TestDeterminism:
    def test_same_intent_same_result(self):
        r1 = tb.propose_brief_variants("test intent", n=3)
        r2 = tb.propose_brief_variants("test intent", n=3)
        assert r1 == r2

    def test_different_intent_can_give_different_order(self):
        # 'enterprise' should bias enterprise template; plain text shouldn't.
        enterprise = tb.propose_brief_variants("b2b enterprise confident", n=3)
        plain = tb.propose_brief_variants("", n=3)
        # Top pick under strong enterprise keywords should be the enterprise template
        assert enterprise[0]["palette"]["accent"] == "#B45309"
        # Plain falls back to first-template (editorial)
        assert plain[0]["palette"]["accent"] == "#E8612E"

    def test_mutating_result_does_not_poison_next_call(self):
        r1 = tb.propose_brief_variants("x", n=1)
        r1[0]["palette"]["accent"] = "#MUTATED"
        r2 = tb.propose_brief_variants("x", n=1)
        assert r2[0]["palette"]["accent"] != "#MUTATED"


class TestKeywordScoring:
    @pytest.mark.parametrize("intent,expected_accent", [
        ("tech platform data", "#2563EB"),   # minimalist technical
        ("warm human story", "#7C2D12"),     # organic (but 'story' also hits editorial)
        ("bold creative pitch", "#F59E0B"),  # bold magazine
        ("elegant luxury heritage", "#78350F"),  # elegant serif
        ("corporate qbr executive", "#B45309"),  # confident enterprise
    ])
    def test_keyword_biases_top_pick(self, intent, expected_accent):
        result = tb.propose_brief_variants(intent, n=1)
        assert result[0]["palette"]["accent"] == expected_accent

    def test_case_insensitive_matching(self):
        upper = tb.propose_brief_variants("ENTERPRISE B2B", n=1)
        lower = tb.propose_brief_variants("enterprise b2b", n=1)
        assert upper == lower
        assert upper[0]["palette"]["accent"] == "#B45309"
