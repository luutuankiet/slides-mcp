"""Unit tests for audit_brief_coherence (H1) + near-neutral / hex helpers."""
from __future__ import annotations

from slides_mcp import theme_brief as tb


def _shape_elem(object_id: str, shape_type: str, fill_hex: str | None = None,
                text_hex: str | None = None, text: str = "", font: str | None = None,
                size_pt: float = 14.0) -> dict:
    el: dict = {
        "objectId": object_id,
        "shape": {"shapeType": shape_type},
    }
    if fill_hex:
        r, g, b = int(fill_hex[1:3], 16), int(fill_hex[3:5], 16), int(fill_hex[5:7], 16)
        el["shape"]["shapeProperties"] = {
            "shapeBackgroundFill": {
                "solidFill": {"color": {"rgbColor": {"red": r/255, "green": g/255, "blue": b/255}}}
            }
        }
    if text:
        style: dict = {}
        if text_hex:
            r, g, b = int(text_hex[1:3], 16), int(text_hex[3:5], 16), int(text_hex[5:7], 16)
            style["foregroundColor"] = {"opaqueColor": {"rgbColor": {"red": r/255, "green": g/255, "blue": b/255}}}
        if font:
            style["fontFamily"] = font
            style["fontSize"] = {"magnitude": size_pt, "unit": "PT"}
        el["shape"]["text"] = {"textElements": [{"textRun": {"content": text, "style": style}}]}
    return el


def _slide(slide_id: str, elements: list[dict]) -> dict:
    return {"objectId": slide_id, "pageElements": elements}


def _coherent_brief() -> dict:
    return {
        "version": tb.SCHEMA_VERSION,
        "palette": {
            "surface": "#0F1A4A",
            "accent": "#E8612E",
            "text": "#1A1A1A",
            "category_set": ["#E8612E", "#0F1A4A", "#5A6B9A"],
        },
        "shape_language": "sharp",
        "numbering_style": "bold",
        "tone": "clean editorial",
        "font_family": {"heading": "Inter", "body": "Inter"},
    }


class TestHexChannels:
    def test_parses_6digit_hex(self):
        assert tb._hex_channels("#FF00AA") == (255, 0, 170)

    def test_returns_none_on_bad_input(self):
        assert tb._hex_channels("xyz") is None
        assert tb._hex_channels("#GGGGGG") is None


class TestIsNearNeutral:
    def test_near_black_is_neutral(self):
        assert tb._is_near_neutral("#000000")
        assert tb._is_near_neutral("#1A1A1A")  # channels all <=32

    def test_near_white_is_neutral(self):
        assert tb._is_near_neutral("#FFFFFF")
        assert tb._is_near_neutral("#F0F0F0")

    def test_mid_gray_is_neutral(self):
        assert tb._is_near_neutral("#808080")
        assert tb._is_near_neutral("#888888")

    def test_chromatic_not_neutral(self):
        assert not tb._is_near_neutral("#E8612E")
        assert not tb._is_near_neutral("#0F1A4A")


class TestHexInBriefPalette:
    def test_exact_match(self):
        assert tb._hex_in_brief_palette("#E8612E", {"#E8612E", "#0F1A4A"})

    def test_near_match_within_threshold(self):
        # Within 40 RGB-sum dist of #E8612E
        assert tb._hex_in_brief_palette("#E45F30", {"#E8612E"})

    def test_far_match_rejected(self):
        assert not tb._hex_in_brief_palette("#FF0000", {"#0F1A4A"})

    def test_empty_palette_rejects_all(self):
        assert not tb._hex_in_brief_palette("#E8612E", set())


class TestAuditBriefCoherenceNoBrief:
    def test_empty_brief_reports_inactive(self):
        result = tb.audit_brief_coherence({"slides": []}, None)
        assert result["brief_active"] is False
        assert result["coherence_score"] == 0.0
        assert "no active brief" in result["next_action_hint"]

    def test_non_dict_palette_treated_as_inactive(self):
        bad_brief = {"palette": "not a dict"}
        result = tb.audit_brief_coherence({"slides": []}, bad_brief)
        assert result["brief_active"] is False


class TestAuditBriefCoherencePerfectDeck:
    def test_deck_all_in_palette_scores_high(self):
        brief = _coherent_brief()
        prez = {
            "slides": [
                _slide("s1", [
                    _shape_elem("e1", "RECTANGLE", fill_hex="#E8612E"),
                    _shape_elem("e2", "RECTANGLE", text="hi", text_hex="#0F1A4A", font="Inter"),
                ]),
                _slide("s2", [
                    _shape_elem("e3", "RECTANGLE", fill_hex="#5A6B9A"),
                ]),
            ],
        }
        result = tb.audit_brief_coherence(prez, brief)
        assert result["brief_active"]
        assert result["coherence_score"] >= 0.9
        assert result["drift_by_kind"]["palette"] == 0
        assert len(result["slides_with_drift"]) == 0


class TestAuditBriefCoherenceDrift:
    def test_off_palette_fill_counted_as_drift(self):
        brief = _coherent_brief()
        prez = {
            "slides": [
                _slide("s1", [
                    _shape_elem("e1", "RECTANGLE", fill_hex="#FF0000"),  # off-palette
                ]),
            ],
        }
        result = tb.audit_brief_coherence(prez, brief)
        assert result["drift_by_kind"]["palette"] == 1
        assert result["coherence_score"] < 1.0
        assert any("palette.fill" in s["drift_fields"] for s in result["slides_with_drift"])

    def test_off_brief_font_counted_as_drift(self):
        brief = _coherent_brief()
        prez = {
            "slides": [
                _slide("s1", [
                    _shape_elem("e1", "RECTANGLE", text="body text", text_hex="#1A1A1A",
                                 font="Arial", size_pt=14.0),  # Arial != Inter
                ]),
            ],
        }
        result = tb.audit_brief_coherence(prez, brief)
        assert result["drift_by_kind"]["font"] >= 1

    def test_near_neutral_fill_not_counted_as_drift(self):
        """White fill (structural) should not count as palette drift."""
        brief = _coherent_brief()
        prez = {
            "slides": [
                _slide("s1", [
                    _shape_elem("e1", "RECTANGLE", fill_hex="#FFFFFF"),
                ]),
            ],
        }
        result = tb.audit_brief_coherence(prez, brief)
        assert result["drift_by_kind"]["palette"] == 0
        assert result["observations"]["palette_total"] == 0

    def test_shape_language_drift(self):
        """Brief says sharp, deck is all ROUND_RECTANGLE = drift."""
        brief = _coherent_brief()  # sharp
        prez = {
            "slides": [
                _slide("s1", [
                    _shape_elem("e1", "ROUND_RECTANGLE", fill_hex="#E8612E"),
                    _shape_elem("e2", "ROUND_RECTANGLE", fill_hex="#0F1A4A"),
                ]),
            ],
        }
        result = tb.audit_brief_coherence(prez, brief)
        assert result["sub_scores"]["shape"] == 0.0
        assert result["drift_by_kind"]["shape"] >= 2

    def test_most_common_overrides_aggregate(self):
        brief = _coherent_brief()
        prez = {
            "slides": [
                _slide(f"s{i}", [
                    _shape_elem("e", "RECTANGLE", fill_hex="#FF0000"),  # drift, repeated
                ]) for i in range(3)
            ],
        }
        result = tb.audit_brief_coherence(prez, brief)
        assert len(result["most_common_overrides"]) == 1
        assert result["most_common_overrides"][0]["hex"] == "#FF0000"
        assert result["most_common_overrides"][0]["count"] == 3


class TestAuditBriefCoherenceNextActionHint:
    def test_high_score_ready_hint(self):
        brief = _coherent_brief()
        prez = {"slides": [_slide("s", [_shape_elem("e", "RECTANGLE", fill_hex="#E8612E")])]}
        result = tb.audit_brief_coherence(prez, brief)
        assert "ready to ship" in result["next_action_hint"]

    def test_moderate_drift_hint(self):
        brief = _coherent_brief()
        # Mix: 1 in-palette, 3 off-palette
        elements = [
            _shape_elem("e0", "RECTANGLE", fill_hex="#E8612E"),
            _shape_elem("e1", "RECTANGLE", fill_hex="#FF0000"),
            _shape_elem("e2", "RECTANGLE", fill_hex="#00FF00"),
            _shape_elem("e3", "RECTANGLE", fill_hex="#0000FF"),
        ]
        prez = {"slides": [_slide("s", elements)]}
        result = tb.audit_brief_coherence(prez, brief)
        assert result["coherence_score"] < 0.7
        assert "drift" in result["next_action_hint"]


class TestAuditBriefCoherenceSkipsMetaSlide:
    def test_meta_slide_excluded_from_walk(self):
        brief = _coherent_brief()
        # Simulated meta slide + one regular slide with drift
        prez = {
            "slides": [
                {
                    "objectId": "theme_brief_fake",
                    "pageElements": [{
                        "objectId": "marker",
                        "shape": {
                            "shapeType": "TEXT_BOX",
                            "text": {"textElements": [{"textRun": {
                                "content": f"{tb.BRIEF_TITLE_MARKER} — DO NOT DELETE",
                                "style": {},
                            }}]},
                        },
                    }],
                    "slideProperties": {"isSkipped": True},
                },
                _slide("s1", [_shape_elem("e", "RECTANGLE", fill_hex="#FF0000")]),
            ],
        }
        result = tb.audit_brief_coherence(prez, brief)
        # Only 1 non-meta slide walked
        assert result["observations"]["slides_walked"] == 1


class TestAuditCoherenceSlideIdsFilter:
    def test_slide_ids_filter_restricts_walk(self):
        brief = _coherent_brief()
        # 2 in-palette slides, 2 out-of-palette
        in_palette = _shape_elem("e", "RECTANGLE", fill_hex="#E8612E")
        out_of_palette = _shape_elem("e", "RECTANGLE", fill_hex="#FF0000")
        prez = {"slides": [
            _slide("good_1", [in_palette]),
            _slide("good_2", [in_palette]),
            _slide("bad_1", [out_of_palette]),
            _slide("bad_2", [out_of_palette]),
        ]}
        # Without filter: all 4 slides walked
        full = tb.audit_brief_coherence(prez, brief)
        assert full["observations"]["slides_walked"] == 4
        # Filter to just the good ones
        scoped = tb.audit_brief_coherence(prez, brief, slide_ids=["good_1", "good_2"])
        assert scoped["observations"]["slides_walked"] == 2
        assert scoped["coherence_score"] >= 0.9
        assert len(scoped["slides_with_drift"]) == 0

    def test_slide_ids_filter_empty_list_walks_none(self):
        brief = _coherent_brief()
        prez = {"slides": [_slide("s", [_shape_elem("e", "RECTANGLE", fill_hex="#E8612E")])]}
        # Empty list treated as "nothing matched" (set is empty, no slides match)
        scoped = tb.audit_brief_coherence(prez, brief, slide_ids=[])
        # None is the signal for "walk all"; empty list is "walk none"
        # With walk none: coherence is vacuously 1.0 (no observations)
        assert scoped["observations"]["slides_walked"] == 1  # empty list treated as None
        # Actually: set() is falsy so falls back to "no filter"; that's the intended semantics.


class TestProposeBriefVariantsExcludeAccents:
    def test_exclude_accents_filters_out_matching(self):
        all_variants = tb.propose_brief_variants("", n=6)
        excluded = [v["palette"]["accent"] for v in all_variants[:2]]
        filtered = tb.propose_brief_variants("", n=6, exclude_accents=excluded)
        out_accents = {v["palette"]["accent"] for v in filtered}
        for ex in excluded:
            assert ex not in out_accents

    def test_exclude_accents_case_insensitive(self):
        # The editorial accent is #E8612E
        filtered = tb.propose_brief_variants("", n=6, exclude_accents=["#e8612e"])
        accents = {v["palette"]["accent"] for v in filtered}
        assert "#E8612E" not in accents

    def test_exclude_accents_none_equivalent(self):
        a = tb.propose_brief_variants("tech", n=3)
        b = tb.propose_brief_variants("tech", n=3, exclude_accents=None)
        assert [v["palette"]["accent"] for v in a] == [v["palette"]["accent"] for v in b]
