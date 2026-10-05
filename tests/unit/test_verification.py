"""Checks an agent can read in text: the inch guard, the units note on raw
reads, layout warnings after writes, the "cannot see it" note, tool hints
and create_deck. Called as an MCP client would, over the fake Slides API.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from slides_mcp import layout
from slides_mcp.server import (
    create_deck,
    exec_batch_update,
    mcp,
    read_slides,
    render_thumbnail,
    run_deck_script,
)

DECK = "deck_fixture"
EMU = 914400


def text_box(oid: str, x: float, y: float, w: float, h: float, *, unit: str = "EMU",
             slide: str = "slide_cases") -> dict[str, Any]:
    return {"createShape": {"objectId": oid, "shapeType": "TEXT_BOX", "elementProperties": {
        "pageObjectId": slide,
        "size": {"width": {"magnitude": w, "unit": unit}, "height": {"magnitude": h, "unit": unit}},
        "transform": {"scaleX": 1, "scaleY": 1, "translateX": x, "translateY": y, "unit": "EMU"},
    }}}


def body(out: Any) -> dict[str, Any]:
    return json.loads(out[0]) if isinstance(out, list) else out


# ---- inch guard ------------------------------------------------------------------

@pytest.mark.parametrize("dry_run", [True, False])
def test_inches_passed_as_emu_are_refused_and_nothing_is_sent(fake, dry_run):
    out = exec_batch_update(DECK, [text_box("tb", 1, 2, 3, 0.5)], dry_run=dry_run)
    assert out["isError"] is True
    assert out["applied_request_count"] == 0
    assert fake.batches == []
    text = " ".join(out["warnings"])
    assert "request #0 (createShape tb)" in text
    assert "width 3 EMU is under 1 pt" in text and "Did you pass inches?" in text
    assert "1 in = 914400 EMU" in text


def test_sub_point_size_in_pt_is_refused(fake):
    out = exec_batch_update(DECK, [text_box("tb", 0, 0, 0.5, 20, unit="PT")])
    assert out["isError"] is True
    assert "width 0.5 PT is under 1 pt" in out["warnings"][0]


def test_missing_magnitude_is_refused_as_not_a_number(fake):
    req = text_box("tb", 0, 0, EMU, EMU)
    del req["createShape"]["elementProperties"]["size"]["height"]["magnitude"]
    out = exec_batch_update(DECK, [req])
    assert out["isError"] is True
    assert "height is None, not a number" in out["warnings"][0]


def test_a_zero_height_line_is_allowed(fake):
    line = {"createLine": {"objectId": "rule", "lineCategory": "STRAIGHT", "elementProperties": {
        "pageObjectId": "slide_cases",
        "size": {"width": {"magnitude": 2 * EMU, "unit": "EMU"}, "height": {"magnitude": 0, "unit": "EMU"}},
        "transform": {"scaleX": 1, "scaleY": 1, "translateX": EMU, "translateY": EMU, "unit": "EMU"},
    }}}
    out = body(exec_batch_update(DECK, [line], receipt="off"))
    assert out["isError"] is False
    assert len(fake.batches) == 1


async def test_text_box_helper_throws_at_the_line_that_passed_inches(fake):
    out = await run_deck_script(deck_url=DECK, receipt="off", script="""
      const s = deck.slides[0];
      emit(textBox(s, {text: "hi", x: 1, y: 2, w: 3, h: 0.5}));
    """)
    assert out["isError"] is True
    assert "textBox: w 3 is under 1 pt. Did you pass inches?" in out["error"]["message"]
    assert out["error"].get("line") == 3
    assert fake.batches == []


async def test_text_box_helper_names_wrong_keys(fake):
    out = await run_deck_script(deck_url=DECK, receipt="off", script="""
      emit(textBox(deck.slides[0], {text: "hi", left: 1, top: 2, width: 3, height: 1}));
    """)
    assert out["isError"] is True
    assert "textBox: x is undefined, not a number" in out["error"]["message"]


async def test_resize_helper_rejects_inches(fake):
    out = await run_deck_script(deck_url=DECK, receipt="off", script="""
      const el = deck.slides[0].elements[0];
      emit(resize(el, {w: 4}));
    """)
    assert out["isError"] is True
    assert "resize: w 4 is under 1 pt" in out["error"]["message"]


async def test_raw_inch_create_in_a_script_fails_validation_in_dry_run(fake):
    out = await run_deck_script(deck_url=DECK, receipt="off", script=f"""
      emit({json.dumps(text_box("tb", 1, 2, 3, 0.5))});
    """)
    assert out["isError"] is True
    assert out["error"]["kind"] == "validation"
    assert "under 1 pt" in out["error"]["message"]


async def test_text_box_helper_with_inch_conversion_applies(fake):
    out = await run_deck_script(deck_url=DECK, receipt="off", dry_run=False, script="""
      emit(textBox(deck.slides[0], {id: "ok", text: "hi",
                                    x: inch(1), y: inch(1), w: inch(3), h: inch(0.5)}));
    """)
    assert out["isError"] is False, out
    assert len(fake.batches) == 1
    assert not [w for w in out["warnings"] if "ok " in w]


# ---- units note on raw reads -------------------------------------------------------

def test_raw_read_says_at_is_inches_and_other_levels_do_not(fake):
    raw = read_slides(DECK, detail="raw")
    assert "inches" in raw["units"] and "914400" in raw["units"]
    assert "units" not in read_slides(DECK, detail="full")


# ---- layout warnings ----------------------------------------------------------------

def _deck(*elements: dict[str, Any]) -> dict[str, Any]:
    return {"pageSize": {"width": {"magnitude": 9144000, "unit": "EMU"},
                         "height": {"magnitude": 5143500, "unit": "EMU"}},
            "slides": [{"objectId": "s1", "pageElements": list(elements)}]}


def _el(oid: str, x: float, y: float, w: float, h: float) -> dict[str, Any]:
    """An element whose size Google stored as given, at (x, y), in inches."""
    return {"objectId": oid, "shape": {"shapeType": "TEXT_BOX"},
            "size": {"width": {"magnitude": w * EMU, "unit": "EMU"},
                     "height": {"magnitude": h * EMU, "unit": "EMU"}},
            "transform": {"scaleX": 1, "scaleY": 1, "translateX": x * EMU,
                          "translateY": y * EMU, "unit": "EMU"}}


def test_layout_flags_googles_default_square():
    default = _el("tb", 0, 0, 1, 1)
    default["size"] = {"width": {"magnitude": 3000000, "unit": "EMU"},
                       "height": {"magnitude": 3000000, "unit": "EMU"}}
    (w,) = layout.check(["tb"], _deck(default))
    assert "tb is a 3.281 in square, Google's default size" in w
    assert "at [0.00, 0.00, 3.28, 3.28] in" in w


def test_layout_flags_three_stacked_boxes_once():
    deck = _deck(*(_el(f"b{i}", 1, 1, 2, 0.5) for i in range(4)))
    (w,) = layout.check(["b0", "b1", "b2", "b3"], deck)
    assert w.startswith("4 new elements on slide s1 sit exactly on top of each other")


def test_layout_flags_partly_and_fully_off_page():
    deck = _deck(_el("half", 9, 1, 2, 1), _el("gone", 11, 1, 1, 1))
    warnings = layout.check(["half", "gone"], deck)
    assert any(w.startswith("half is partly off the 10.00 x 5.62 in page") for w in warnings)
    assert any(w.startswith("gone is fully off") for w in warnings)


def test_layout_ignores_elements_the_write_did_not_create():
    deck = _deck(_el("old", 11, 1, 1, 1), _el("new", 1, 1, 2, 1))
    assert layout.check(["new"], deck) == []


def test_write_reply_warns_about_its_own_off_page_box(fake):
    out = body(exec_batch_update(DECK, [text_box("far", 14 * EMU, EMU, EMU, EMU)], receipt="off"))
    assert out["isError"] is False
    assert any(w.startswith("far is fully off") for w in out["warnings"])


def test_write_without_a_reread_says_the_layout_check_was_skipped(fake):
    out = body(exec_batch_update(DECK, [text_box("tb", EMU, EMU, EMU, EMU)],
                                 post_state="none", receipt="off"))
    assert any(w.startswith("layout check skipped") for w in out["warnings"])


async def test_script_apply_warns_about_stacked_boxes(fake):
    out = await run_deck_script(deck_url=DECK, receipt="off", dry_run=False, script="""
      for (const i of [1, 2, 3])
        emit(textBox(deck.slides[0], {id: "st" + i, text: "x",
                                      x: inch(1), y: inch(1), w: inch(2), h: inch(1)}));
    """)
    assert out["isError"] is False, out
    assert any("3 new elements" in w and "st1, st2, st3" in w for w in out["warnings"])


# ---- the "cannot see it" note -------------------------------------------------------

def test_receipt_carries_the_cannot_see_note(fake):
    out = exec_batch_update(DECK, [text_box("tb", EMU, EMU, EMU, EMU)])
    assert isinstance(out, list)
    assert json.loads(out[0])["thumbnails"]["note"].startswith("If you cannot see the image(s)")


def test_render_thumbnail_sends_the_note_before_the_image(fake):
    out = render_thumbnail(DECK, "slide_cases")
    assert out[0].startswith("If you cannot see the image(s) below, say so.")
    assert len(out) == 2


# ---- tool hints ---------------------------------------------------------------------

READ = (True, False, True)
EXPECTED = {
    "auth_status": READ, "install_skill": READ, "get_deck_outline": READ,
    "read_slides": READ, "search_deck": READ, "render_thumbnail": READ,
    "create_deck": (False, False, False), "place_image": (False, False, False),
    "add_section_footers": (False, False, False),
    "write_speaker_notes": (False, True, True),
    "exec_batch_update": (False, True, False), "run_deck_script": (False, True, False),
}


async def test_every_tool_declares_its_hints():
    tools = {t.name: t for t in await mcp.list_tools()}
    assert set(tools) == set(EXPECTED), "a new tool needs an entry here and hints in server.py"
    for name, (ro, destructive, idem) in EXPECTED.items():
        a = tools[name].annotations
        assert (a.readOnlyHint, a.destructiveHint, a.idempotentHint, a.openWorldHint) == \
            (ro, destructive, idem, True), name


# ---- create_deck --------------------------------------------------------------------

def test_create_deck_returns_ids_url_and_outline(fake):
    out = create_deck("  Q3 review  ")
    assert fake.created == ["Q3 review"]
    assert out["deck_id"] == "new_deck_1"
    assert out["deck_url"] == "https://docs.google.com/presentation/d/new_deck_1/edit"
    assert out["first_slide_id"] == "p"
    assert out["deck_outline"]["slide_count"] == 1


def test_create_deck_rejects_an_empty_title(fake):
    with pytest.raises(ValueError, match="non-empty"):
        create_deck("   ")
    assert fake.created == []
