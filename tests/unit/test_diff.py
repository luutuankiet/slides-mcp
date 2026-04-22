from __future__ import annotations

from slides_mcp.diff import diff_slide, geometry_changed


def _base_3col() -> dict:
    return {
        "id": "s01",
        "layout": "3col_pill_cards",
        "title": "Looker is the Heart of Business Analytics",
        "columns": [
            {"pill": "Semantic Layer", "body": "Trusted LookML models"},
            {"pill": "Empowered Users", "body": "Centralized and self-service"},
            {"pill": "Untapped Potential", "body": "Last-mile gap"},
        ],
        "notes": "walk through positioning",
    }


def test_diff_title_change():
    old = _base_3col()
    new = _base_3col()
    new["title"] = "Looker Is The Foundation"
    result = diff_slide(old, new, slide_id="s01")
    assert len(result.requests) == 1
    req = result.requests[0]
    assert "replaceAllText" in req
    assert req["replaceAllText"]["containsText"]["text"].startswith("Looker is the Heart")
    assert req["replaceAllText"]["replaceText"] == "Looker Is The Foundation"


def test_diff_column_pill_change():
    old = _base_3col()
    new = _base_3col()
    new["columns"][1]["pill"] = "Power Users"
    result = diff_slide(old, new, slide_id="s01")
    assert len(result.requests) == 1
    assert result.requests[0]["replaceAllText"]["replaceText"] == "Power Users"


def test_diff_no_change_empty_result():
    old = _base_3col()
    new = _base_3col()
    result = diff_slide(old, new, slide_id="s01")
    assert result.empty()
    assert not result.warnings


def test_diff_archetype_swap_warns():
    old = _base_3col()
    new = _base_3col()
    new["layout"] = "4col_numbered_flow"
    result = diff_slide(old, new, slide_id="s01")
    assert result.empty()
    assert any("archetype change" in w for w in result.warnings)


def test_diff_column_count_change_warns():
    old = _base_3col()
    new = _base_3col()
    new["columns"].append({"pill": "New", "body": "Extra"})
    result = diff_slide(old, new, slide_id="s01")
    assert any("column count changed" in w for w in result.warnings)


def test_diff_notes_change_emits_warning_but_is_summarized():
    old = _base_3col()
    new = _base_3col()
    new["notes"] = "different notes"
    result = diff_slide(old, new, slide_id="s01")
    # v1: notes change flagged, writer handles
    assert any("notes change" in w for w in result.warnings)
    assert any("notes change" in s for s in result.summary)


def test_diff_paragraph_change_in_text_heavy():
    old = {"id": "s01", "layout": "text_heavy_body", "title": "Context",
           "paragraphs": ["para one", "para two"]}
    new = {"id": "s01", "layout": "text_heavy_body", "title": "Context",
           "paragraphs": ["para one revised", "para two"]}
    result = diff_slide(old, new, slide_id="s01")
    assert len(result.requests) == 1
    assert result.requests[0]["replaceAllText"]["replaceText"] == "para one revised"


def test_geometry_changed_detection():
    old = _base_3col()
    new = _base_3col()
    assert not geometry_changed(old, new)
    new["layout"] = "4col_numbered_flow"
    assert geometry_changed(old, new)


# ---------- geometry write path: elements[].at diffs ----------

def _with_elements(base: dict, *elems: tuple[str, float, float, float, float]) -> dict:
    out = dict(base)
    out["elements"] = [
        {"id": eid, "at": [x, y, w, h]} for eid, x, y, w, h in elems
    ]
    return out


def test_diff_element_move_emits_updatePageElementTransform():
    old = _with_elements(_base_3col(), ("iconA", 1.0, 2.0, 0.5, 0.5))
    new = _with_elements(_base_3col(), ("iconA", 2.0, 2.0, 0.5, 0.5))  # +1 inch right
    result = diff_slide(old, new, slide_id="s01")
    assert len(result.requests) == 1
    req = result.requests[0]
    assert "updatePageElementTransform" in req
    t = req["updatePageElementTransform"]
    assert t["objectId"] == "iconA"
    assert t["applyMode"] == "RELATIVE"
    assert t["transform"]["scaleX"] == 1
    assert t["transform"]["scaleY"] == 1
    assert t["transform"]["translateX"] == 914400  # 1 inch in EMU
    assert t["transform"]["translateY"] == 0
    assert t["transform"]["unit"] == "EMU"
    assert not result.warnings


def test_diff_element_noop_when_identical():
    old = _with_elements(_base_3col(), ("iconA", 1.0, 2.0, 0.5, 0.5))
    new = _with_elements(_base_3col(), ("iconA", 1.0, 2.0, 0.5, 0.5))
    result = diff_slide(old, new, slide_id="s01")
    assert result.empty()
    assert not result.warnings


def test_diff_element_size_change_warns_no_request():
    old = _with_elements(_base_3col(), ("iconA", 1.0, 2.0, 0.5, 0.5))
    new = _with_elements(_base_3col(), ("iconA", 1.0, 2.0, 1.0, 1.0))  # resize only
    result = diff_slide(old, new, slide_id="s01")
    assert result.empty()  # no translation → no request
    assert any("size change" in w for w in result.warnings)


def test_diff_element_unknown_id_warns():
    old = _with_elements(_base_3col(), ("iconA", 1.0, 2.0, 0.5, 0.5))
    new = _with_elements(
        _base_3col(),
        ("iconA", 1.0, 2.0, 0.5, 0.5),
        ("iconZ", 3.0, 3.0, 0.5, 0.5),  # not in old
    )
    result = diff_slide(old, new, slide_id="s01")
    assert result.empty()
    assert any("iconZ" in w and "creating" in w for w in result.warnings)


def test_diff_element_removed_id_warns():
    old = _with_elements(
        _base_3col(),
        ("iconA", 1.0, 2.0, 0.5, 0.5),
        ("iconB", 3.0, 3.0, 0.5, 0.5),
    )
    new = _with_elements(_base_3col(), ("iconA", 1.0, 2.0, 0.5, 0.5))
    result = diff_slide(old, new, slide_id="s01")
    assert result.empty()
    assert any("iconB" in w and "deleting" in w for w in result.warnings)


def test_diff_element_sub_millimeter_ignored():
    """Sub-0.001-inch jitter should not produce a request."""
    old = _with_elements(_base_3col(), ("iconA", 1.0000, 2.0000, 0.5, 0.5))
    new = _with_elements(_base_3col(), ("iconA", 1.0005, 2.0003, 0.5, 0.5))
    result = diff_slide(old, new, slide_id="s01")
    assert result.empty()


def test_geometry_changed_detects_element_move():
    old = _with_elements(_base_3col(), ("iconA", 1.0, 2.0, 0.5, 0.5))
    new = _with_elements(_base_3col(), ("iconA", 2.0, 2.0, 0.5, 0.5))
    assert geometry_changed(old, new)


def test_geometry_changed_detects_element_add_remove():
    old = _with_elements(_base_3col(), ("iconA", 1.0, 2.0, 0.5, 0.5))
    new = _with_elements(
        _base_3col(),
        ("iconA", 1.0, 2.0, 0.5, 0.5),
        ("iconB", 3.0, 3.0, 0.5, 0.5),
    )
    assert geometry_changed(old, new)


# ---------- notes write emission (T2.5) ----------

def test_diff_notes_with_object_id_emits_delete_and_insert():
    old = _base_3col()
    new = _base_3col()
    new["notes"] = "updated speaker notes"
    result = diff_slide(
        old, new, slide_id="s01", notes_object_id="notesBodyObj_XYZ",
    )
    assert len(result.requests) == 2
    assert "deleteText" in result.requests[0]
    assert result.requests[0]["deleteText"]["objectId"] == "notesBodyObj_XYZ"
    assert "insertText" in result.requests[1]
    assert result.requests[1]["insertText"]["objectId"] == "notesBodyObj_XYZ"
    assert result.requests[1]["insertText"]["text"] == "updated speaker notes"
    assert not any("notes change" in w for w in result.warnings)
    assert any("notes change" in s for s in result.summary)


def test_diff_notes_without_object_id_still_warns():
    old = _base_3col()
    new = _base_3col()
    new["notes"] = "different"
    result = diff_slide(old, new, slide_id="s01")  # no notes_object_id
    assert result.empty()  # no emission without id
    assert any("no notes_object_id" in w for w in result.warnings)


def test_diff_notes_from_empty_only_emits_insert():
    old = _base_3col()
    old["notes"] = ""
    new = _base_3col()
    new["notes"] = "fresh notes"
    result = diff_slide(
        old, new, slide_id="s01", notes_object_id="notesBodyObj_XYZ",
    )
    assert len(result.requests) == 1
    assert "insertText" in result.requests[0]


# ---------- object-scoped text edits (T2.8) ----------

def test_diff_title_with_object_id_uses_delete_insert():
    old = _base_3col()
    old["_object_ids"] = {"title": "titleObj_1"}
    new = _base_3col()
    new["title"] = "Looker Is Everything"
    result = diff_slide(old, new, slide_id="s01")
    assert len(result.requests) == 2
    assert result.requests[0]["deleteText"]["objectId"] == "titleObj_1"
    assert result.requests[1]["insertText"]["objectId"] == "titleObj_1"
    assert result.requests[1]["insertText"]["text"] == "Looker Is Everything"
    assert any("object-scoped" in s for s in result.summary)


def test_diff_title_without_object_id_falls_back_to_replaceAllText():
    old = _base_3col()  # no _object_ids
    new = _base_3col()
    new["title"] = "Looker Is Everything"
    result = diff_slide(old, new, slide_id="s01")
    assert len(result.requests) == 1
    assert "replaceAllText" in result.requests[0]


def test_diff_paragraphs_with_ids_uses_object_scoped():
    old = {
        "id": "s01", "layout": "text_heavy_body", "title": "Context",
        "paragraphs": ["para one", "para two"],
        "_object_ids": {"paragraphs": ["pid_1", "pid_2"]},
    }
    new = {
        "id": "s01", "layout": "text_heavy_body", "title": "Context",
        "paragraphs": ["para one revised", "para two"],
    }
    result = diff_slide(old, new, slide_id="s01")
    # deleteText + insertText for paragraph[0] only (paragraph[1] unchanged)
    assert len(result.requests) == 2
    assert result.requests[0]["deleteText"]["objectId"] == "pid_1"
    assert result.requests[1]["insertText"]["objectId"] == "pid_1"
    assert result.requests[1]["insertText"]["text"] == "para one revised"
