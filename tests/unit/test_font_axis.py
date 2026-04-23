"""Unit tests for Scope B: font axis in brief + list_font_pairings + builder overlay."""
from __future__ import annotations

from slides_mcp import create as create_mod
from slides_mcp import theme as theme_mod
from slides_mcp import theme_brief as theme_brief_mod


def _sub_with_fonts() -> theme_mod.SubTheme:
    return theme_mod.SubTheme(
        name="test",
        palette={"brand_accent": "#E8612E", "text_primary": "#000000"},
        fonts={
            "display": theme_mod.FontSpec(family="Arial", size_pt=36.0, weight=700),
            "pill_header": theme_mod.FontSpec(family="Arial", size_pt=20.0, weight=600),
            "body": theme_mod.FontSpec(family="Georgia", size_pt=14.0, weight=400),
            "body_small": theme_mod.FontSpec(family="Georgia", size_pt=12.0, weight=400),
            "caption": theme_mod.FontSpec(family="Georgia", size_pt=10.0, weight=400),
        },
    )


class TestValidateBriefFontFamily:
    def test_valid_font_family_accepted(self):
        brief = {
            "version": theme_brief_mod.SCHEMA_VERSION,
            "palette": {"surface": "#000000", "accent": "#FF0000"},
            "font_family": {"heading": "Fraunces", "body": "Inter"},
        }
        ok, errors = theme_brief_mod.validate_brief(brief)
        assert ok, errors

    def test_font_family_absent_ok(self):
        brief = {
            "version": theme_brief_mod.SCHEMA_VERSION,
            "palette": {"surface": "#000000", "accent": "#FF0000"},
        }
        ok, errors = theme_brief_mod.validate_brief(brief)
        assert ok, errors

    def test_font_family_partial_axis_ok(self):
        """Only heading axis present — body axis can be absent."""
        brief = {
            "palette": {"surface": "#000000", "accent": "#FF0000"},
            "font_family": {"heading": "Fraunces"},
        }
        ok, errors = theme_brief_mod.validate_brief(brief)
        assert ok, errors

    def test_font_family_wrong_type_rejected(self):
        brief = {
            "palette": {"surface": "#000000", "accent": "#FF0000"},
            "font_family": "Inter",  # string, not dict
        }
        ok, errors = theme_brief_mod.validate_brief(brief)
        assert not ok
        assert any("font_family must be a dict" in e for e in errors)

    def test_font_family_axis_non_string_rejected(self):
        brief = {
            "palette": {"surface": "#000000", "accent": "#FF0000"},
            "font_family": {"heading": 42, "body": "Inter"},
        }
        ok, errors = theme_brief_mod.validate_brief(brief)
        assert not ok
        assert any("font_family.heading must be a string" in e for e in errors)

    def test_font_family_empty_string_rejected(self):
        brief = {
            "palette": {"surface": "#000000", "accent": "#FF0000"},
            "font_family": {"heading": "  ", "body": "Inter"},
        }
        ok, errors = theme_brief_mod.validate_brief(brief)
        assert not ok
        assert any("non-empty" in e for e in errors)


class TestListFontPairings:
    def test_returns_all_when_no_mood(self):
        pairings = theme_brief_mod.list_font_pairings()
        assert len(pairings) >= 10, "expected ≈12 curated pairings"
        assert all("id" in p and "heading" in p and "body" in p for p in pairings)

    def test_mood_filter_case_insensitive(self):
        lower = theme_brief_mod.list_font_pairings("tech")
        upper = theme_brief_mod.list_font_pairings("TECH")
        assert len(lower) == len(upper)

    def test_mood_filter_narrows_results(self):
        all_pairings = theme_brief_mod.list_font_pairings()
        tech_only = theme_brief_mod.list_font_pairings("tech")
        assert len(tech_only) >= 1
        assert len(tech_only) < len(all_pairings)

    def test_mood_filter_empty_string_returns_all(self):
        pairings = theme_brief_mod.list_font_pairings("")
        assert len(pairings) == len(FONT_PAIRINGS := theme_brief_mod.FONT_PAIRINGS)
        assert len(pairings) == len(FONT_PAIRINGS)

    def test_mood_filter_no_match_returns_empty(self):
        # "zzz_nothing_here" won't substring-match any known mood tag
        pairings = theme_brief_mod.list_font_pairings("zzz_nothing_here")
        assert pairings == []

    def test_returned_dicts_are_fresh_copies(self):
        a = theme_brief_mod.list_font_pairings()
        b = theme_brief_mod.list_font_pairings()
        a[0]["heading"] = "MUTATED"
        assert b[0]["heading"] != "MUTATED"

    def test_every_pairing_has_mood_tags(self):
        for p in theme_brief_mod.FONT_PAIRINGS:
            assert isinstance(p["mood"], list)
            assert len(p["mood"]) >= 1
            assert all(isinstance(m, str) for m in p["mood"])

    def test_pairing_ids_are_unique(self):
        ids = [p["id"] for p in theme_brief_mod.FONT_PAIRINGS]
        assert len(set(ids)) == len(ids), "pairing ids must be unique"


class TestProposeBriefVariantsCarryFontFamily:
    def test_variants_include_font_family(self):
        briefs = theme_brief_mod.propose_brief_variants(
            "tech startup AI dashboard", n=3
        )
        assert len(briefs) == 3
        for b in briefs:
            assert "font_family" in b, "variant must carry font_family axis"
            assert "heading" in b["font_family"]
            assert "body" in b["font_family"]

    def test_variants_validate_with_font_family(self):
        briefs = theme_brief_mod.propose_brief_variants("enterprise QBR board", n=3)
        for b in briefs:
            ok, errors = theme_brief_mod.validate_brief(b)
            assert ok, f"variant failed validation: {errors}"

    def test_variants_have_distinct_headings(self):
        briefs = theme_brief_mod.propose_brief_variants("any generic intent", n=3)
        heading_fonts = {b["font_family"]["heading"] for b in briefs}
        # May legitimately overlap if only 3 moods match, but with a
        # generic intent we should see >=2 distinct heading fonts across 3 variants.
        assert len(heading_fonts) >= 2


class TestIsHeadingRole:
    def test_display_is_heading(self):
        assert create_mod._is_heading_role("display")

    def test_title_is_heading(self):
        assert create_mod._is_heading_role("title")

    def test_pill_header_is_heading(self):
        assert create_mod._is_heading_role("pill_header")

    def test_body_is_not_heading(self):
        assert not create_mod._is_heading_role("body")
        assert not create_mod._is_heading_role("body_small")
        assert not create_mod._is_heading_role("caption")

    def test_none_is_not_heading(self):
        assert not create_mod._is_heading_role(None)
        assert not create_mod._is_heading_role("")


class TestApplyBriefFontsToSub:
    def test_none_brief_passes_through(self):
        sub = _sub_with_fonts()
        out = create_mod._apply_brief_fonts_to_sub(sub, None)
        assert out is sub

    def test_brief_without_font_family_passes_through(self):
        sub = _sub_with_fonts()
        brief = {"palette": {"accent": "#FF0000"}}
        out = create_mod._apply_brief_fonts_to_sub(sub, brief)
        assert out is sub

    def test_brief_with_empty_font_family_passes_through(self):
        sub = _sub_with_fonts()
        brief = {"font_family": {}}
        out = create_mod._apply_brief_fonts_to_sub(sub, brief)
        assert out is sub

    def test_heading_axis_overrides_display_role(self):
        sub = _sub_with_fonts()
        brief = {"font_family": {"heading": "Fraunces", "body": "Inter"}}
        out = create_mod._apply_brief_fonts_to_sub(sub, brief)
        # display is heading-class
        assert out.fonts["display"].family == "Fraunces"
        # size + weight preserved
        assert out.fonts["display"].size_pt == 36.0
        assert out.fonts["display"].weight == 700

    def test_body_axis_overrides_body_role(self):
        sub = _sub_with_fonts()
        brief = {"font_family": {"heading": "Fraunces", "body": "Inter"}}
        out = create_mod._apply_brief_fonts_to_sub(sub, brief)
        assert out.fonts["body"].family == "Inter"
        assert out.fonts["body_small"].family == "Inter"
        assert out.fonts["caption"].family == "Inter"
        # size preserved per role
        assert out.fonts["body"].size_pt == 14.0
        assert out.fonts["body_small"].size_pt == 12.0
        assert out.fonts["caption"].size_pt == 10.0

    def test_pill_header_routes_to_heading_axis(self):
        sub = _sub_with_fonts()
        brief = {"font_family": {"heading": "Fraunces", "body": "Inter"}}
        out = create_mod._apply_brief_fonts_to_sub(sub, brief)
        # pill_header contains "pill" — heading-class
        assert out.fonts["pill_header"].family == "Fraunces"

    def test_only_heading_axis_leaves_body_alone(self):
        sub = _sub_with_fonts()
        brief = {"font_family": {"heading": "Fraunces"}}
        out = create_mod._apply_brief_fonts_to_sub(sub, brief)
        assert out.fonts["display"].family == "Fraunces"
        # body axis absent — preserved from theme
        assert out.fonts["body"].family == "Georgia"

    def test_original_sub_not_mutated(self):
        sub = _sub_with_fonts()
        original_display_family = sub.fonts["display"].family
        brief = {"font_family": {"heading": "Fraunces", "body": "Inter"}}
        create_mod._apply_brief_fonts_to_sub(sub, brief)
        assert sub.fonts["display"].family == original_display_family

    def test_returns_new_subtheme_instance(self):
        sub = _sub_with_fonts()
        brief = {"font_family": {"heading": "Fraunces"}}
        out = create_mod._apply_brief_fonts_to_sub(sub, brief)
        assert out is not sub
        # fonts dict is a distinct object
        assert out.fonts is not sub.fonts
