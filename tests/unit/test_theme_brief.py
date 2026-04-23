"""Unit tests for theme_brief.py — Phase 2 meta-slide infrastructure."""
from __future__ import annotations

from slides_mcp import theme_brief as tb

# ---------------------------------------------------------------------------
# Serialization + parse roundtrip
# ---------------------------------------------------------------------------


def test_serialize_carries_warning_preamble():
    brief = dict(tb.DEFAULT_BRIEF)
    body = tb.serialize_brief(brief)
    assert body.startswith("⚠ slides-mcp metadata")
    assert "Google Slides version history" in body


def test_serialize_stamps_schema_version():
    brief = {"palette": {"surface": "#FFFFFF"}}  # version intentionally absent
    body = tb.serialize_brief(brief)
    parsed = tb.parse_brief_body(body)
    assert parsed is not None
    assert parsed["version"] == tb.SCHEMA_VERSION


def test_serialize_preserves_all_fields_roundtrip():
    brief = {
        "version": 1,
        "palette": {
            "surface": "#0F1A4A",
            "accent": "#E8612E",
            "text": "#000000",
            "category_set": ["#E8612E", "#0F1A4A", "#888888"],
        },
        "shape_language": "sharp",
        "numbering_style": "bold",
        "tone": "clean editorial",
        "image_prompt_style": "photography, warm light",
    }
    body = tb.serialize_brief(brief)
    parsed = tb.parse_brief_body(body)
    assert parsed == brief


def test_parse_brief_body_none_on_garbage():
    assert tb.parse_brief_body("") is None
    assert tb.parse_brief_body("just a note, not a brief") is None
    assert tb.parse_brief_body("---\nnot_our_root_key: 42\n") is None


def test_parse_brief_body_tolerates_missing_preamble():
    # Body without the warning — still parseable if YAML root key present.
    body = f"{tb.YAML_ROOT_KEY}:\n  version: 1\n  palette:\n    surface: '#112233'\n"
    parsed = tb.parse_brief_body(body)
    assert parsed is not None
    assert parsed["palette"]["surface"] == "#112233"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_validate_accepts_default_brief():
    ok, errors = tb.validate_brief(dict(tb.DEFAULT_BRIEF))
    assert ok, errors
    assert errors == []


def test_validate_rejects_non_hex():
    brief = {"palette": {"surface": "navy", "accent": "#E8612E", "text": "#000000"}}
    ok, errors = tb.validate_brief(brief)
    assert not ok
    assert any("surface" in e and "hex" in e for e in errors)


def test_validate_rejects_short_hex():
    brief = {"palette": {"surface": "#fff"}}
    ok, errors = tb.validate_brief(brief)
    assert not ok


def test_validate_rejects_bad_shape_language():
    brief = {"palette": {"surface": "#000000"}, "shape_language": "blobby"}
    ok, errors = tb.validate_brief(brief)
    assert not ok
    assert any("shape_language" in e for e in errors)


def test_validate_rejects_bad_numbering_style():
    brief = {"palette": {"surface": "#000000"}, "numbering_style": "italic"}
    ok, errors = tb.validate_brief(brief)
    assert not ok
    assert any("numbering_style" in e for e in errors)


def test_validate_rejects_bad_category_set_element():
    brief = {"palette": {"category_set": ["#FF0000", "red", "#00FF00"]}}
    ok, errors = tb.validate_brief(brief)
    assert not ok
    assert any("category_set[1]" in e for e in errors)


def test_validate_rejects_non_dict_brief():
    ok, errors = tb.validate_brief("not a brief")  # type: ignore[arg-type]
    assert not ok


def test_validate_warns_on_future_schema():
    brief = {"version": 99, "palette": {"surface": "#000000"}}
    ok, errors = tb.validate_brief(brief)
    assert not ok
    assert any("schema version" in e for e in errors)


# ---------------------------------------------------------------------------
# find_meta_slide
# ---------------------------------------------------------------------------


def _make_shape_element(object_id: str, text: str) -> dict:
    return {
        "objectId": object_id,
        "shape": {
            "shapeType": "TEXT_BOX",
            "text": {
                "textElements": [{"textRun": {"content": text}}]
            },
        },
    }


def test_find_meta_slide_locates_marker():
    brief = dict(tb.DEFAULT_BRIEF)
    body_text = tb.serialize_brief(brief)
    prez = {
        "slides": [
            {
                "objectId": "slide_1",
                "pageElements": [_make_shape_element("t1", "Ordinary slide")],
            },
            {
                "objectId": "theme_brief_xyz",
                "pageElements": [
                    _make_shape_element("tb_marker", tb.BRIEF_TITLE),
                    _make_shape_element("tb_body", body_text),
                ],
            },
        ]
    }
    result = tb.find_meta_slide(prez)
    assert result is not None
    assert result["slide_id"] == "theme_brief_xyz"
    assert result["marker_box_id"] == "tb_marker"
    assert result["body_box_id"] == "tb_body"
    assert result["body_text"] == body_text


def test_find_meta_slide_returns_none_when_absent():
    prez = {
        "slides": [
            {
                "objectId": "slide_1",
                "pageElements": [_make_shape_element("t1", "Nothing here")],
            }
        ]
    }
    assert tb.find_meta_slide(prez) is None


def test_find_meta_slide_tolerates_body_only():
    # Corrupted state: marker present but body was deleted. Should still find.
    prez = {
        "slides": [
            {
                "objectId": "theme_brief_x",
                "pageElements": [
                    _make_shape_element("tb_marker", tb.BRIEF_TITLE),
                ],
            }
        ]
    }
    result = tb.find_meta_slide(prez)
    assert result is not None
    assert result["marker_box_id"] == "tb_marker"
    assert result["body_box_id"] is None
    assert result["body_text"] == ""


# ---------------------------------------------------------------------------
# build_create_meta_slide_requests
# ---------------------------------------------------------------------------


def test_build_create_requests_shape():
    brief = dict(tb.DEFAULT_BRIEF)
    reqs = tb.build_create_meta_slide_requests(
        slide_id="theme_brief_abc",
        marker_box_id="tb_marker_1",
        body_box_id="tb_body_1",
        brief=brief,
        deck_width_in=10.0,
        deck_height_in=5.625,
        insertion_index=3,
    )
    kinds = [next(iter(r.keys())) for r in reqs]
    assert kinds == [
        "createSlide",
        "updateSlideProperties",
        "createShape",
        "insertText",
        "updateTextStyle",
        "createShape",
        "insertText",
        "updateTextStyle",
    ]


def test_build_create_requests_sets_is_skipped():
    reqs = tb.build_create_meta_slide_requests(
        slide_id="s1",
        marker_box_id="m1",
        body_box_id="b1",
        brief=dict(tb.DEFAULT_BRIEF),
        deck_width_in=10.0,
        deck_height_in=5.625,
        insertion_index=0,
    )
    update_props = reqs[1]["updateSlideProperties"]
    assert update_props["slideProperties"]["isSkipped"] is True
    assert update_props["fields"] == "isSkipped"


def test_build_create_requests_marker_text_carries_literal():
    reqs = tb.build_create_meta_slide_requests(
        slide_id="s1",
        marker_box_id="m1",
        body_box_id="b1",
        brief=dict(tb.DEFAULT_BRIEF),
        deck_width_in=10.0,
        deck_height_in=5.625,
        insertion_index=0,
    )
    marker_insert = reqs[3]["insertText"]
    assert marker_insert["objectId"] == "m1"
    assert marker_insert["text"].startswith(tb.BRIEF_TITLE_MARKER)
    assert "DO NOT DELETE" in marker_insert["text"]


def test_build_create_requests_body_carries_brief():
    brief = {
        "version": 1,
        "palette": {"surface": "#FF0000", "accent": "#00FF00", "text": "#0000FF"},
    }
    reqs = tb.build_create_meta_slide_requests(
        slide_id="s1",
        marker_box_id="m1",
        body_box_id="b1",
        brief=brief,
        deck_width_in=10.0,
        deck_height_in=5.625,
        insertion_index=0,
    )
    body_insert = reqs[6]["insertText"]
    assert body_insert["objectId"] == "b1"
    parsed = tb.parse_brief_body(body_insert["text"])
    assert parsed is not None
    assert parsed["palette"]["surface"] == "#FF0000"


def test_build_create_requests_scales_to_deck_size():
    # 10" × 5.625" deck → body height should be well under deck height
    reqs = tb.build_create_meta_slide_requests(
        slide_id="s1",
        marker_box_id="m1",
        body_box_id="b1",
        brief=dict(tb.DEFAULT_BRIEF),
        deck_width_in=10.0,
        deck_height_in=5.625,
        insertion_index=0,
    )
    body_props = reqs[5]["createShape"]["elementProperties"]
    body_h_emu = body_props["size"]["height"]["magnitude"]
    # Should be < deck height (5.625" = 5143500 EMU)
    assert 0 < body_h_emu < 5_143_500


# ---------------------------------------------------------------------------
# build_update_brief_requests
# ---------------------------------------------------------------------------


def test_update_requests_emit_delete_then_insert():
    reqs = tb.build_update_brief_requests("b1", dict(tb.DEFAULT_BRIEF))
    kinds = [next(iter(r.keys())) for r in reqs]
    assert kinds == ["deleteText", "insertText"]
    assert reqs[0]["deleteText"]["objectId"] == "b1"
    assert reqs[0]["deleteText"]["textRange"] == {"type": "ALL"}
    assert reqs[1]["insertText"]["objectId"] == "b1"


def test_update_requests_new_body_is_parseable():
    new_brief = {"palette": {"surface": "#AABBCC"}, "tone": "dark tech"}
    reqs = tb.build_update_brief_requests("b1", new_brief)
    body_text = reqs[1]["insertText"]["text"]
    parsed = tb.parse_brief_body(body_text)
    assert parsed is not None
    assert parsed["palette"]["surface"] == "#AABBCC"
    assert parsed["tone"] == "dark tech"


# ---------------------------------------------------------------------------
# merge_brief
# ---------------------------------------------------------------------------


def test_merge_brief_top_level_replace():
    existing = {"tone": "editorial", "shape_language": "sharp"}
    changes = {"tone": "warm tech"}
    out = tb.merge_brief(existing, changes)
    assert out == {"tone": "warm tech", "shape_language": "sharp"}


def test_merge_brief_nested_dict_deep_merge():
    existing = {"palette": {"surface": "#000000", "accent": "#FF0000"}}
    changes = {"palette": {"accent": "#00FF00"}}
    out = tb.merge_brief(existing, changes)
    assert out == {"palette": {"surface": "#000000", "accent": "#00FF00"}}


def test_merge_brief_list_replace_wholesale():
    existing = {"palette": {"category_set": ["#AAA111", "#BBB222"]}}
    changes = {"palette": {"category_set": ["#CCC333"]}}
    out = tb.merge_brief(existing, changes)
    # List replaced, not element-merged.
    assert out["palette"]["category_set"] == ["#CCC333"]


def test_merge_brief_none_drops_key():
    existing = {"tone": "editorial", "shape_language": "sharp"}
    changes = {"shape_language": None}
    out = tb.merge_brief(existing, changes)
    assert out == {"tone": "editorial"}


def test_merge_brief_doesnt_mutate_inputs():
    existing = {"palette": {"surface": "#000000"}}
    changes = {"palette": {"accent": "#FFFFFF"}}
    original = {"palette": {"surface": "#000000"}}
    _ = tb.merge_brief(existing, changes)
    assert existing == original


# ---------------------------------------------------------------------------
# Brownfield extraction (Phase 2C)
# ---------------------------------------------------------------------------


def _rgb_fracs(hex_value: str) -> dict:
    h = hex_value.lstrip("#").upper()
    return {
        "red": int(h[0:2], 16) / 255,
        "green": int(h[2:4], 16) / 255,
        "blue": int(h[4:6], 16) / 255,
    }


def _make_filled_shape(
    object_id: str,
    fill_hex: str,
    w_emu: int = 5_000_000,
    h_emu: int = 1_000_000,
    shape_type: str = "RECTANGLE",
) -> dict:
    return {
        "objectId": object_id,
        "size": {
            "width": {"magnitude": w_emu, "unit": "EMU"},
            "height": {"magnitude": h_emu, "unit": "EMU"},
        },
        "shape": {
            "shapeType": shape_type,
            "shapeProperties": {
                "shapeBackgroundFill": {
                    "solidFill": {"color": {"rgbColor": _rgb_fracs(fill_hex)}}
                }
            },
        },
    }


def _make_text_shape(object_id: str, text: str, fg_hex: str) -> dict:
    return {
        "objectId": object_id,
        "size": {
            "width": {"magnitude": 3_000_000, "unit": "EMU"},
            "height": {"magnitude": 500_000, "unit": "EMU"},
        },
        "shape": {
            "shapeType": "TEXT_BOX",
            "text": {
                "textElements": [
                    {
                        "textRun": {
                            "content": text,
                            "style": {
                                "foregroundColor": {
                                    "opaqueColor": {"rgbColor": _rgb_fracs(fg_hex)}
                                }
                            },
                        }
                    }
                ]
            },
        },
    }


def test_extract_brief_from_joon_like_prez():
    """Joon-style deck: navy header bar + orange accent text + black body."""
    prez = {
        "slides": [
            {
                "objectId": "s1",
                "pageElements": [
                    # Big navy header bar — should become palette.surface
                    _make_filled_shape("hdr1", "#0F1A4A", w_emu=16_000_000, h_emu=1_500_000),
                    # Orange title text — should become palette.accent
                    _make_text_shape("t1", "Intro", "#E8612E"),
                    # Black body text — should become palette.text
                    _make_text_shape("b1", "Body copy", "#000000"),
                ],
            },
            {
                "objectId": "s2",
                "pageElements": [
                    _make_filled_shape("hdr2", "#0F1A4A", w_emu=16_000_000, h_emu=1_500_000),
                    _make_text_shape("t2", "Point one", "#E8612E"),
                    _make_text_shape("b2", "More body", "#000000"),
                    # A chromatic fill — gives us category_set material
                    _make_filled_shape("cat1", "#5A6B9A", shape_type="RECTANGLE"),
                ],
            },
        ]
    }
    result = tb.extract_brief_from_prez(prez)
    brief = result["proposed_brief"]
    assert brief["palette"]["surface"] == "#0F1A4A"
    assert brief["palette"]["accent"] == "#E8612E"
    assert brief["palette"]["text"] == "#000000"
    assert "#E8612E" in brief["palette"]["category_set"]
    # Shape language: all RECTANGLE → "sharp"
    assert brief["shape_language"] == "sharp"


def test_extract_brief_detects_rounded_shape_language():
    """Deck with mostly ROUND_RECTANGLE → shape_language: rounded."""
    prez = {
        "slides": [
            {
                "objectId": "s1",
                "pageElements": [
                    _make_filled_shape(f"p{i}", "#FF0000", shape_type="ROUND_RECTANGLE")
                    for i in range(10)
                ]
                + [_make_filled_shape("r1", "#00FF00", shape_type="RECTANGLE")],
            }
        ]
    }
    result = tb.extract_brief_from_prez(prez)
    assert result["proposed_brief"]["shape_language"] == "rounded"


def test_extract_brief_detects_mixed_shape_language():
    """~50/50 rounded vs sharp → mixed."""
    prez = {
        "slides": [
            {
                "objectId": "s1",
                "pageElements": [
                    _make_filled_shape("p1", "#FF0000", shape_type="ROUND_RECTANGLE"),
                    _make_filled_shape("p2", "#00FF00", shape_type="ROUND_RECTANGLE"),
                    _make_filled_shape("r1", "#0000FF", shape_type="RECTANGLE"),
                    _make_filled_shape("r2", "#FFFF00", shape_type="RECTANGLE"),
                ],
            }
        ]
    }
    result = tb.extract_brief_from_prez(prez)
    assert result["proposed_brief"]["shape_language"] == "mixed"


def test_extract_brief_excludes_meta_slide_from_evidence():
    """Meta-slide should NOT pollute the extraction histograms."""
    brief = dict(tb.DEFAULT_BRIEF)
    meta_body = tb.serialize_brief(brief)
    prez = {
        "slides": [
            {
                "objectId": "real_slide",
                "pageElements": [
                    _make_filled_shape("f1", "#AA1111", w_emu=10_000_000, h_emu=2_000_000),
                    _make_text_shape("t1", "Body", "#000000"),
                ],
            },
            {
                "objectId": "theme_brief_xyz",
                "pageElements": [
                    _make_shape_element("marker", tb.BRIEF_TITLE),
                    _make_shape_element("body", meta_body),
                ],
            },
        ]
    }
    result = tb.extract_brief_from_prez(prez)
    # slides_walked is only 1 — meta slide excluded
    assert result["evidence"]["slides_walked"] == 1


def test_extract_brief_low_confidence_for_near_empty_deck():
    prez = {
        "slides": [
            {"objectId": "s1", "pageElements": [_make_text_shape("t1", "Hello", "#000000")]}
        ]
    }
    result = tb.extract_brief_from_prez(prez)
    assert result["confidence"] == "low"


def test_extract_brief_carries_evidence_histograms():
    prez = {
        "slides": [
            {
                "objectId": "s1",
                "pageElements": [
                    _make_filled_shape("f1", "#FF0000"),
                    _make_filled_shape("f2", "#00FF00"),
                    _make_text_shape("t1", "Hi", "#000000"),
                ],
            }
        ]
    }
    result = tb.extract_brief_from_prez(prez)
    assert "top_fills" in result["evidence"]
    assert "top_text_colors" in result["evidence"]
    assert "shape_types" in result["evidence"]
    assert result["evidence"]["distinct_fill_colors"] == 2


def test_extract_brief_empty_deck_returns_safety_defaults():
    """No slides at all → returns a brief with safety-net defaults."""
    prez = {"slides": []}
    result = tb.extract_brief_from_prez(prez)
    brief = result["proposed_brief"]
    # Safety-net palette values from the extractor
    assert brief["palette"]["surface"] == "#0F1A4A"
    assert brief["palette"]["accent"] == "#E8612E"
    assert result["confidence"] == "low"


def test_neutral_hex_helper():
    assert tb._neutral_hex("#000000") is True
    assert tb._neutral_hex("#1A1A1A") is True
    assert tb._neutral_hex("#FFFFFF") is True
    assert tb._neutral_hex("#F5F5F5") is True
    assert tb._neutral_hex("#808080") is True
    assert tb._neutral_hex("#E8612E") is False
    assert tb._neutral_hex("#0F1A4A") is False  # dark navy, chromatic blue


# ---------------------------------------------------------------------------
# Durability enhancements (v0.9.0): preamble rebuild text + speaker notes
# ---------------------------------------------------------------------------


def test_warning_preamble_references_rebuild_command():
    """WARNING_PREAMBLE now instructs readers how to rebuild the meta slide."""
    assert "scaffold_meta_brief" in tb.WARNING_PREAMBLE
    assert "auto_commit_if_high_confidence" in tb.WARNING_PREAMBLE


def test_warning_preamble_still_starts_with_warning_emoji():
    """Back-compat: existing callers assert the emoji prefix."""
    assert tb.WARNING_PREAMBLE.startswith("⚠ slides-mcp metadata")


def test_speaker_notes_text_constant_exists():
    assert hasattr(tb, "SPEAKER_NOTES_TEXT")
    assert isinstance(tb.SPEAKER_NOTES_TEXT, str)
    assert len(tb.SPEAKER_NOTES_TEXT) > 200  # non-trivially helpful


def test_speaker_notes_text_carries_key_phrases():
    """Speaker notes must give humans enough context to NOT delete the meta slide."""
    text = tb.SPEAKER_NOTES_TEXT
    assert "DO NOT DELETE" in text
    assert "theme brief" in text.lower()
    assert "scaffold_meta_brief" in text
    assert "extract_theme_brief" in text
    assert "set_theme_brief" in text
    assert "update_theme_brief" in text


def test_build_notes_populate_requests_shape():
    reqs = tb.build_notes_populate_requests("notes_body_1")
    assert len(reqs) == 1
    kinds = [next(iter(r.keys())) for r in reqs]
    assert kinds == ["insertText"]
    insert = reqs[0]["insertText"]
    assert insert["objectId"] == "notes_body_1"
    assert insert["insertionIndex"] == 0
    assert insert["text"] == tb.SPEAKER_NOTES_TEXT


def test_serialize_still_parses_after_preamble_expansion():
    """Roundtrip invariant must survive the preamble growing rebuild text."""
    brief = dict(tb.DEFAULT_BRIEF)
    body = tb.serialize_brief(brief)
    parsed = tb.parse_brief_body(body)
    assert parsed is not None
    # Schema version is stamped on serialize; compare ignoring any version drift.
    for k in ("palette", "shape_language", "numbering_style", "tone"):
        assert parsed.get(k) == brief.get(k)
