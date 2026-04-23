"""Unit tests for text_range — pure range resolution + style normalization."""
from __future__ import annotations

import pytest

from slides_mcp import text_range as tr

# ---------------------------------------------------------------------
# resolve_range
# ---------------------------------------------------------------------

class TestResolveRange:
    def test_none_returns_all(self):
        assert tr.resolve_range("any text", None) == {"type": "ALL"}

    def test_string_all_returns_all(self):
        assert tr.resolve_range("any text", "all") == {"type": "ALL"}

    def test_string_other_rejects(self):
        with pytest.raises(ValueError, match="range must be 'all'"):
            tr.resolve_range("x", "unknown")

    def test_rejects_non_dict_spec(self):
        with pytest.raises(ValueError, match="range must be"):
            tr.resolve_range("x", 42)

    def test_rejects_empty_dict(self):
        with pytest.raises(ValueError, match="exactly one key"):
            tr.resolve_range("x", {})

    def test_rejects_multi_key_dict(self):
        with pytest.raises(ValueError, match="exactly one key"):
            tr.resolve_range("x", {"paragraph": 0, "chars": [0, 1]})

    def test_rejects_unknown_key(self):
        with pytest.raises(ValueError, match="unknown range key"):
            tr.resolve_range("x", {"line": 3})

    # paragraph mode
    def test_paragraph_first(self):
        # 3 paragraphs: "Hello" (0-5), "World" (6-11), "Foo" (12-15)
        result = tr.resolve_range("Hello\nWorld\nFoo", {"paragraph": 0})
        assert result == {"type": "FIXED_RANGE", "startIndex": 0, "endIndex": 5}

    def test_paragraph_middle(self):
        result = tr.resolve_range("Hello\nWorld\nFoo", {"paragraph": 1})
        assert result == {"type": "FIXED_RANGE", "startIndex": 6, "endIndex": 11}

    def test_paragraph_last(self):
        result = tr.resolve_range("Hello\nWorld\nFoo", {"paragraph": 2})
        assert result == {"type": "FIXED_RANGE", "startIndex": 12, "endIndex": 15}

    def test_paragraph_trailing_newline(self):
        # Slides API text typically has trailing \n — no phantom empty paragraph.
        result = tr.resolve_range("Hello\n", {"paragraph": 0})
        assert result == {"type": "FIXED_RANGE", "startIndex": 0, "endIndex": 5}

    def test_paragraph_skips_blank_separator(self):
        # Blank lines between visible paragraphs are NOT counted.
        # 'A\n\nB' has 2 visible paragraphs: A (0..1), B (3..4).
        result = tr.resolve_range("A\n\nB", {"paragraph": 1})
        assert result == {"type": "FIXED_RANGE", "startIndex": 3, "endIndex": 4}

    def test_paragraph_multi_blank_separator(self):
        # Multiple blank separators collapse to one logical boundary.
        # 'first\n\n\nsecond' → paragraph 0 = 'first' (0..5), paragraph 1 = 'second' (8..14).
        result = tr.resolve_range("first\n\n\nsecond", {"paragraph": 1})
        assert result == {"type": "FIXED_RANGE", "startIndex": 8, "endIndex": 14}

    def test_paragraph_empty_text_rejects(self):
        # No visible paragraphs → paragraph 0 is out of range (not a zero-length range).
        # This is the Slides-API-rejects-zero-length-range guard.
        with pytest.raises(ValueError, match="out of range"):
            tr.resolve_range("", {"paragraph": 0})

    def test_paragraph_after_trailing_newline_rejects(self):
        # 'Hello\n' has 1 visible paragraph — paragraph 1 is out of range.
        with pytest.raises(ValueError, match="out of range"):
            tr.resolve_range("Hello\n", {"paragraph": 1})

    def test_paragraph_out_of_range(self):
        with pytest.raises(ValueError, match="paragraph index 5 out of range"):
            tr.resolve_range("Hello\nWorld", {"paragraph": 5})

    def test_paragraph_negative(self):
        with pytest.raises(ValueError, match="paragraph index must be >= 0"):
            tr.resolve_range("x", {"paragraph": -1})

    # chars mode
    def test_chars_basic(self):
        result = tr.resolve_range("Hello World", {"chars": [6, 11]})
        assert result == {"type": "FIXED_RANGE", "startIndex": 6, "endIndex": 11}

    def test_chars_tuple_accepted(self):
        result = tr.resolve_range("Hello World", {"chars": (0, 5)})
        assert result == {"type": "FIXED_RANGE", "startIndex": 0, "endIndex": 5}

    def test_chars_reject_non_pair(self):
        with pytest.raises(ValueError, match="chars must be"):
            tr.resolve_range("x", {"chars": [1]})
        with pytest.raises(ValueError, match="chars must be"):
            tr.resolve_range("x", {"chars": [1, 2, 3]})

    def test_chars_negative(self):
        with pytest.raises(ValueError, match="chars indices must be >= 0"):
            tr.resolve_range("xxxxx", {"chars": [-1, 3]})

    def test_chars_start_ge_end(self):
        with pytest.raises(ValueError, match="chars start must be < end"):
            tr.resolve_range("xxxxx", {"chars": [3, 3]})

    def test_chars_end_out_of_bounds(self):
        with pytest.raises(ValueError, match="chars end 10 exceeds text length 5"):
            tr.resolve_range("xxxxx", {"chars": [0, 10]})

    def test_chars_no_text_bound_check_when_text_empty(self):
        # empty text disables bound check (for chars-mode callers that pass "" as text)
        result = tr.resolve_range("", {"chars": [0, 5]})
        assert result == {"type": "FIXED_RANGE", "startIndex": 0, "endIndex": 5}

    # match mode
    def test_match_unique(self):
        result = tr.resolve_range("Hello World", {"match": "World"})
        assert result == {"type": "FIXED_RANGE", "startIndex": 6, "endIndex": 11}

    def test_match_at_start(self):
        result = tr.resolve_range("Hello World", {"match": "Hello"})
        assert result == {"type": "FIXED_RANGE", "startIndex": 0, "endIndex": 5}

    def test_match_not_found(self):
        with pytest.raises(ValueError, match="not found in shape text"):
            tr.resolve_range("Hello World", {"match": "Missing"})

    def test_match_ambiguous(self):
        with pytest.raises(ValueError, match="ambiguous: found 3 occurrences"):
            tr.resolve_range("foo bar foo baz foo", {"match": "foo"})

    def test_match_empty_needle(self):
        with pytest.raises(ValueError, match="must be a non-empty string"):
            tr.resolve_range("x", {"match": ""})

    def test_match_non_string(self):
        with pytest.raises(ValueError, match="must be a non-empty string"):
            tr.resolve_range("x", {"match": 123})


# ---------------------------------------------------------------------
# _hex_to_rgb_fracs
# ---------------------------------------------------------------------

class TestHexToRgbFracs:
    def test_with_hash(self):
        assert tr._hex_to_rgb_fracs("#FF0000") == {"red": 1.0, "green": 0.0, "blue": 0.0}

    def test_without_hash(self):
        assert tr._hex_to_rgb_fracs("00FF00") == {"red": 0.0, "green": 1.0, "blue": 0.0}

    def test_lowercase(self):
        assert tr._hex_to_rgb_fracs("#0000ff") == {"red": 0.0, "green": 0.0, "blue": 1.0}

    def test_mid_value(self):
        # #808080 → 128/255 ≈ 0.501961
        r = tr._hex_to_rgb_fracs("#808080")
        assert r == {"red": 0.501961, "green": 0.501961, "blue": 0.501961}

    def test_invalid_length(self):
        with pytest.raises(ValueError, match="expected 6-digit hex"):
            tr._hex_to_rgb_fracs("#F00")

    def test_invalid_digits(self):
        with pytest.raises(ValueError, match="invalid hex digits"):
            tr._hex_to_rgb_fracs("#GGHHII")


# ---------------------------------------------------------------------
# normalize_text_style
# ---------------------------------------------------------------------

class TestNormalizeTextStyle:
    def test_empty_rejected(self):
        with pytest.raises(ValueError, match="non-empty dict"):
            tr.normalize_text_style({})

    def test_unknown_key_rejected(self):
        with pytest.raises(ValueError, match="unknown text style key"):
            tr.normalize_text_style({"flashing": True})

    def test_bold_only(self):
        api, fields = tr.normalize_text_style({"bold": True})
        assert api == {"bold": True}
        assert fields == ["bold"]

    def test_italic_false(self):
        api, fields = tr.normalize_text_style({"italic": False})
        assert api == {"italic": False}
        assert fields == ["italic"]

    def test_font_family(self):
        api, fields = tr.normalize_text_style({"fontFamily": "Inter"})
        assert api == {"fontFamily": "Inter"}
        assert fields == ["fontFamily"]

    def test_font_size_integer(self):
        api, fields = tr.normalize_text_style({"fontSize": 24})
        assert api == {"fontSize": {"magnitude": 24.0, "unit": "PT"}}
        assert fields == ["fontSize"]

    def test_font_size_float(self):
        api, _ = tr.normalize_text_style({"fontSize": 18.5})
        assert api["fontSize"]["magnitude"] == 18.5

    def test_foreground_color_hex(self):
        api, fields = tr.normalize_text_style({"foregroundColor": "#E8612E"})
        assert api == {
            "foregroundColor": {
                "opaqueColor": {"rgbColor": tr._hex_to_rgb_fracs("#E8612E")}
            }
        }
        assert fields == ["foregroundColor"]

    def test_background_color_hex_no_hash(self):
        api, _ = tr.normalize_text_style({"backgroundColor": "FFFFFF"})
        assert api["backgroundColor"] == {
            "opaqueColor": {"rgbColor": {"red": 1.0, "green": 1.0, "blue": 1.0}}
        }

    def test_foreground_color_dict_passthrough(self):
        raw = {"opaqueColor": {"themeColor": "ACCENT1"}}
        api, _ = tr.normalize_text_style({"foregroundColor": raw})
        assert api["foregroundColor"] is raw

    def test_foreground_color_wrong_type(self):
        with pytest.raises(ValueError, match="must be a hex string or a Color dict"):
            tr.normalize_text_style({"foregroundColor": 42})

    def test_baseline_offset_valid(self):
        api, _ = tr.normalize_text_style({"baselineOffset": "superscript"})
        assert api == {"baselineOffset": "SUPERSCRIPT"}

    def test_baseline_offset_invalid(self):
        with pytest.raises(ValueError, match="baselineOffset must be one of"):
            tr.normalize_text_style({"baselineOffset": "middle"})

    def test_weighted_font_family(self):
        api, _ = tr.normalize_text_style({"weightedFontFamily": {"fontFamily": "Inter", "weight": 700}})
        assert api == {"weightedFontFamily": {"fontFamily": "Inter", "weight": 700}}

    def test_weighted_font_family_default_weight(self):
        api, _ = tr.normalize_text_style({"weightedFontFamily": {"fontFamily": "Inter"}})
        assert api["weightedFontFamily"]["weight"] == 400

    def test_weighted_font_family_missing_family(self):
        with pytest.raises(ValueError, match="weightedFontFamily must be"):
            tr.normalize_text_style({"weightedFontFamily": {"weight": 700}})

    def test_compound_style(self):
        api, fields = tr.normalize_text_style({
            "bold": True,
            "fontSize": 36,
            "foregroundColor": "#FF0000",
        })
        assert api["bold"] is True
        assert api["fontSize"] == {"magnitude": 36.0, "unit": "PT"}
        assert api["foregroundColor"] == {
            "opaqueColor": {"rgbColor": {"red": 1.0, "green": 0.0, "blue": 0.0}}
        }
        assert set(fields) == {"bold", "fontSize", "foregroundColor"}


# ---------------------------------------------------------------------
# normalize_paragraph_style
# ---------------------------------------------------------------------

class TestNormalizeParagraphStyle:
    def test_empty_rejected(self):
        with pytest.raises(ValueError, match="non-empty dict"):
            tr.normalize_paragraph_style({})

    def test_unknown_key_rejected(self):
        with pytest.raises(ValueError, match="unknown paragraph style key"):
            tr.normalize_paragraph_style({"slant": 5})

    def test_alignment(self):
        api, fields = tr.normalize_paragraph_style({"alignment": "center"})
        assert api == {"alignment": "CENTER"}
        assert fields == ["alignment"]

    def test_alignment_invalid(self):
        with pytest.raises(ValueError, match="alignment must be one of"):
            tr.normalize_paragraph_style({"alignment": "diagonal"})

    def test_direction(self):
        api, _ = tr.normalize_paragraph_style({"direction": "right_to_left"})
        assert api == {"direction": "RIGHT_TO_LEFT"}

    def test_direction_invalid(self):
        with pytest.raises(ValueError, match="direction must be one of"):
            tr.normalize_paragraph_style({"direction": "diagonal"})

    def test_spacing_mode(self):
        api, _ = tr.normalize_paragraph_style({"spacingMode": "NEVER_COLLAPSE"})
        assert api == {"spacingMode": "NEVER_COLLAPSE"}

    def test_indent_pt(self):
        api, _ = tr.normalize_paragraph_style({"indentStart": 18})
        assert api == {"indentStart": {"magnitude": 18.0, "unit": "PT"}}

    def test_space_above_below(self):
        api, fields = tr.normalize_paragraph_style({
            "spaceAbove": 12,
            "spaceBelow": 6,
        })
        assert api["spaceAbove"] == {"magnitude": 12.0, "unit": "PT"}
        assert api["spaceBelow"] == {"magnitude": 6.0, "unit": "PT"}
        assert set(fields) == {"spaceAbove", "spaceBelow"}

    def test_line_spacing(self):
        api, _ = tr.normalize_paragraph_style({"lineSpacing": 150})
        assert api == {"lineSpacing": 150.0}


# ---------------------------------------------------------------------
# extract_shape_text
# ---------------------------------------------------------------------

class TestExtractShapeText:
    def _page(self, elements):
        return {"pageElements": elements}

    def _shape(self, object_id, runs):
        text_elements = [{"textRun": {"content": c}} for c in runs]
        return {
            "objectId": object_id,
            "shape": {"text": {"textElements": text_elements}},
        }

    def test_single_run(self):
        page = self._page([self._shape("s_1", ["Hello\n"])])
        assert tr.extract_shape_text(page, "s_1") == "Hello\n"

    def test_multiple_runs(self):
        page = self._page([self._shape("s_1", ["Hello ", "World\n"])])
        assert tr.extract_shape_text(page, "s_1") == "Hello World\n"

    def test_runs_with_non_run_elements(self):
        # paragraphMarker/autoText elements should be skipped silently
        page = {"pageElements": [{
            "objectId": "s_1",
            "shape": {"text": {"textElements": [
                {"paragraphMarker": {}},
                {"textRun": {"content": "A"}},
                {"paragraphMarker": {}},
                {"textRun": {"content": "B"}},
            ]}},
        }]}
        assert tr.extract_shape_text(page, "s_1") == "AB"

    def test_missing_shape_raises(self):
        page = self._page([self._shape("s_1", ["x"])])
        with pytest.raises(KeyError, match="not found on slide"):
            tr.extract_shape_text(page, "s_missing")

    def test_empty_text(self):
        page = {"pageElements": [{
            "objectId": "s_1",
            "shape": {"text": {"textElements": []}},
        }]}
        assert tr.extract_shape_text(page, "s_1") == ""

    def test_no_shape_on_element(self):
        page = {"pageElements": [{
            "objectId": "s_1",
            "image": {"contentUrl": "https://x"},
        }]}
        assert tr.extract_shape_text(page, "s_1") == ""

    def test_empty_page_elements(self):
        with pytest.raises(KeyError):
            tr.extract_shape_text({"pageElements": []}, "s_1")

    def test_none_page_elements(self):
        with pytest.raises(KeyError):
            tr.extract_shape_text({}, "s_1")
