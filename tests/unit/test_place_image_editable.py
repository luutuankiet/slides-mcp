"""place_image(editable=True): a supported subset of SVG becomes native shapes.

Called as an MCP client would, over the fake Slides API and the stdio state
store (editable output needs no image bucket). Each test feeds an SVG in and
asserts on the requests that reached the fake.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from slides_mcp import state_store
from slides_mcp.server import place_image
from slides_mcp.slides_api import SlidesApiError
from tests.fake_api import _box, _style, _text

DECK = "deck_fixture"
SLIDE = "slide_cases"
NS = 'xmlns="http://www.w3.org/2000/svg"'
# A 200 x 100 viewBox into a 300 x 150 pt box at (40, 60): scale 1.5, no letterbox.
BOX = {"x": 40, "y": 60, "width": 300, "height": 150}
MARKER = "{{DIAGRAM}}"
TWO_RECTS = '<rect width="100" height="100"/><rect x="100" width="100" height="100"/>'


@pytest.fixture(autouse=True)
def stdio_store(monkeypatch: pytest.MonkeyPatch) -> None:
    """The stdio store: any attempt to host an image would raise."""
    monkeypatch.setattr(state_store, "_current", state_store.MemoryStateStore())


def svg(body: str, view_box: str = "0 0 200 100") -> str:
    return f'<svg {NS} viewBox="{view_box}">{body}</svg>'


def split(out: Any) -> dict[str, Any]:
    return json.loads(out[0]) if isinstance(out, list) else out


async def place(fake: Any, body: str, **kw: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    target = kw.pop("target", {"box": BOX})
    reply = split(await place_image(DECK, slide_id=SLIDE, svg=svg(body, **kw),
                                    editable=True, **target))
    assert reply["isError"] is False, reply
    (batch,) = fake.batches
    return reply, batch


def of_kind(batch: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [r[kind] for r in batch if kind in r]


def ep(x: float, y: float, w: float, h: float, sx: float = 1, sy: float = 1) -> dict[str, Any]:
    return {"pageObjectId": SLIDE,
            "size": {"width": {"magnitude": w, "unit": "PT"},
                     "height": {"magnitude": h, "unit": "PT"}},
            "transform": {"scaleX": sx, "scaleY": sy, "translateX": x, "translateY": y,
                          "unit": "PT"}}


async def test_rect_becomes_a_rectangle_with_explicit_fill_and_no_outline(fake):
    _, batch = await place(fake, '<rect x="10" y="10" width="180" height="80" fill="#3366cc"/>')

    (create,) = of_kind(batch, "createShape")
    assert create["shapeType"] == "RECTANGLE"
    assert create["elementProperties"] == ep(55, 75, 270, 120)
    (props,) = of_kind(batch, "updateShapeProperties")
    assert props["objectId"] == create["objectId"]
    assert props["shapeProperties"] == {
        "shapeBackgroundFill": {"solidFill": {
            "color": {"rgbColor": {"red": 0x33 / 255, "green": 0x66 / 255, "blue": 0xCC / 255}},
            "alpha": 1.0}},
        "outline": {"propertyState": "NOT_RENDERED"},
    }
    assert set(props["fields"].split(",")) == {
        "shapeBackgroundFill.solidFill.color", "shapeBackgroundFill.solidFill.alpha",
        "outline.propertyState"}


async def test_rect_stroke_scales_its_width_and_maps_dash_and_opacity(fake):
    _, batch = await place(fake, (
        '<rect x="0" y="0" width="100" height="50" rx="8" fill="none" stroke="rgb(255, 0, 0)" '
        'stroke-width="2" stroke-dasharray="4 2" style="stroke-opacity: 0.5" opacity="0.8"/>'))

    (create,) = of_kind(batch, "createShape")
    assert create["shapeType"] == "ROUND_RECTANGLE"
    (props,) = of_kind(batch, "updateShapeProperties")
    assert props["shapeProperties"]["shapeBackgroundFill"] == {"propertyState": "NOT_RENDERED"}
    assert props["shapeProperties"]["outline"] == {
        "outlineFill": {"solidFill": {"color": {"rgbColor": {"red": 1.0, "green": 0.0,
                                                             "blue": 0.0}},
                                      "alpha": pytest.approx(0.4)}},
        "weight": {"magnitude": 3.0, "unit": "PT"},
        "dashStyle": "DASH"}
    assert set(props["fields"].split(",")) == {
        "shapeBackgroundFill.propertyState", "outline.outlineFill.solidFill.color",
        "outline.outlineFill.solidFill.alpha", "outline.weight", "outline.dashStyle"}


async def test_circle_becomes_an_ellipse_in_its_bounding_box(fake):
    _, batch = await place(fake, '<circle cx="50" cy="50" r="20" fill="black"/>')
    (create,) = of_kind(batch, "createShape")
    assert create["shapeType"] == "ELLIPSE"
    assert create["elementProperties"] == ep(85, 105, 60, 60)


async def test_ellipse_in_nested_groups_takes_their_transforms_and_presentation(fake):
    _, batch = await place(fake, (
        '<g transform="translate(10,0)" fill="#00ff00" stroke="black">'
        '<g transform="scale(2)" stroke-width="1">'
        '<ellipse cx="10" cy="10" rx="5" ry="3"/></g></g>'))

    (create,) = of_kind(batch, "createShape")
    assert create["shapeType"] == "ELLIPSE"
    # x: (10 + 2 * 5) * 1.5 + 40; width 2 * 10 * 1.5. y: 2 * 7 * 1.5 + 60; height 2 * 6 * 1.5.
    assert create["elementProperties"] == ep(70, 81, 30, 18)
    (props,) = of_kind(batch, "updateShapeProperties")
    assert props["shapeProperties"]["shapeBackgroundFill"]["solidFill"]["color"] == {
        "rgbColor": {"red": 0.0, "green": 1.0, "blue": 0.0}}
    assert props["shapeProperties"]["outline"]["weight"] == {"magnitude": 3.0, "unit": "PT"}


async def test_a_wider_view_box_is_centred_in_the_box(fake):
    # 100 x 100 into 300 x 150: scale 1.5, centred horizontally with 75 pt either side.
    _, batch = await place(fake, '<rect width="100" height="100"/>', view_box="0 0 100 100")
    (create,) = of_kind(batch, "createShape")
    assert create["elementProperties"] == ep(115, 60, 150, 150)


MARKERS = (
    '<defs>'
    '<marker id="tip"><path d="M0,0 L10,5 L0,10 Z" fill="#000"/></marker>'
    '<marker id="dot"><circle cx="5" cy="5" r="4" fill="none" stroke="#000"/></marker>'
    '<marker id="chevron"><polyline points="0,0 10,5 0,10" fill="none"/></marker>'
    '</defs>')


async def test_line_with_markers_becomes_a_straight_line_with_arrowheads(fake):
    _, batch = await place(fake, MARKERS + (
        '<line x1="10" y1="20" x2="110" y2="20" stroke="#000" stroke-width="2" '
        'marker-start="url(#dot)" marker-end="url(#tip)"/>'))

    (create,) = of_kind(batch, "createLine")
    assert create["category"] == "STRAIGHT"
    assert create["elementProperties"] == ep(55, 90, 150, 0)
    (props,) = of_kind(batch, "updateLineProperties")
    assert props["objectId"] == create["objectId"]
    assert props["lineProperties"] == {
        "lineFill": {"solidFill": {"color": {"rgbColor": {"red": 0.0, "green": 0.0,
                                                          "blue": 0.0}}, "alpha": 1.0}},
        "weight": {"magnitude": 3.0, "unit": "PT"},
        "dashStyle": "SOLID",
        "startArrow": "OPEN_CIRCLE",
        "endArrow": "FILL_ARROW"}
    assert set(props["fields"].split(",")) == {
        "lineFill.solidFill.color", "lineFill.solidFill.alpha", "weight", "dashStyle",
        "startArrow", "endArrow"}


async def test_line_drawn_backwards_flips_so_the_arrow_stays_at_its_end(fake):
    _, batch = await place(fake, MARKERS + (
        '<line x1="110" y1="80" x2="10" y2="20" stroke="red" marker-end="url(#chevron)"/>'))
    (create,) = of_kind(batch, "createLine")
    assert create["elementProperties"] == ep(205, 180, 150, 90, sx=-1, sy=-1)
    (props,) = of_kind(batch, "updateLineProperties")
    assert props["lineProperties"]["endArrow"] == "OPEN_ARROW"
    assert props["lineProperties"]["startArrow"] == "NONE"


async def test_line_without_stroke_draws_nothing_as_in_svg(fake):
    _, batch = await place(fake, '<rect width="10" height="10"/><line x1="0" y1="0" x2="5" y2="5"/>')
    assert of_kind(batch, "createLine") == []
    assert len(of_kind(batch, "createShape")) == 1


def text_requests(batch: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    (create,) = [c for c in of_kind(batch, "createShape") if c["shapeType"] == "TEXT_BOX"]
    oid = create["objectId"]
    out = {"create": create}
    for kind in ("insertText", "updateTextStyle", "updateParagraphStyle"):
        (out[kind],) = [r for r in of_kind(batch, kind) if r["objectId"] == oid]
    return out


async def test_text_becomes_a_text_box_whose_baseline_lands_on_the_svg_baseline(fake):
    _, batch = await place(fake, (
        '<text x="20" y="40" font-family="sans-serif" font-size="12" font-weight="bold" '
        'font-style="italic" fill="#ff0000">  Plan   ahead </text>'))

    t = text_requests(batch)
    props = t["create"]["elementProperties"]
    # Page baseline: 60 + 1.5 * 40 = 120. Size 18 pt sits 6.75 + 0.9625 * 18 = 24.075
    # below the box top, measured live; the 7.2 pt left inset puts the box left of x = 70.
    assert props["transform"]["translateY"] == pytest.approx(95.925)
    assert props["transform"]["translateX"] == pytest.approx(62.8)
    # Wide enough not to wrap: at least the text's width at 0.6 em per character.
    assert props["size"]["width"]["magnitude"] > 0.6 * 18 * len("Plan ahead") + 2 * 7.2
    assert t["insertText"]["text"] == "Plan ahead"
    assert t["updateTextStyle"]["style"] == {
        "fontFamily": "Arial",
        "fontSize": {"magnitude": 18.0, "unit": "PT"},
        "foregroundColor": {"opaqueColor": {"rgbColor": {"red": 1.0, "green": 0.0,
                                                         "blue": 0.0}}},
        "bold": True,
        "italic": True}
    assert set(t["updateTextStyle"]["fields"].split(",")) == {
        "fontFamily", "fontSize", "foregroundColor", "bold", "italic"}
    assert t["updateParagraphStyle"]["style"] == {"alignment": "START"}


@pytest.mark.parametrize("anchor, alignment", [("middle", "CENTER"), ("end", "END")])
async def test_text_anchor_sets_alignment_and_which_box_edge_meets_x(fake, anchor, alignment):
    _, batch = await place(fake, f'<text x="100" y="50" text-anchor="{anchor}">Label</text>')
    t = text_requests(batch)
    props = t["create"]["elementProperties"]
    left, width = props["transform"]["translateX"], props["size"]["width"]["magnitude"]
    x = 40 + 1.5 * 100
    if anchor == "middle":
        assert left + width / 2 == pytest.approx(x)
    else:
        assert left + width == pytest.approx(x + 7.2)
    assert t["updateParagraphStyle"]["style"] == {"alignment": alignment}


@pytest.mark.parametrize("baseline, offset_em", [("middle", 0.35), ("central", 0.35),
                                                  ("hanging", 0.75)])
async def test_dominant_baseline_moves_the_baseline_down_from_y(fake, baseline, offset_em):
    _, batch = await place(fake, (f'<text x="0" y="40" font-size="12" '
                                  f'dominant-baseline="{baseline}">A</text>'))
    props = text_requests(batch)["create"]["elementProperties"]
    assert props["transform"]["translateY"] == pytest.approx(
        120 + offset_em * 18 - (6.75 + 0.9625 * 18))


@pytest.mark.parametrize("family, sent", [
    ("sans-serif", "Arial"),
    ("serif", "Times New Roman"),
    ("monospace", "Courier New"),
    ("'Roboto', Arial, sans-serif", "Roboto"),
    ("&quot;Open Sans&quot;", "Open Sans"),
])
async def test_font_family_sends_one_real_family_never_a_generic_name(fake, family, sent):
    _, batch = await place(fake, f'<text x="0" y="40" font-family="{family}">A</text>')
    assert text_requests(batch)["updateTextStyle"]["style"]["fontFamily"] == sent


async def test_text_defaults_to_black_arial_16px(fake):
    _, batch = await place(fake, '<text x="0" y="40">A</text>')
    style = text_requests(batch)["updateTextStyle"]["style"]
    assert style["fontFamily"] == "Arial"
    assert style["fontSize"] == {"magnitude": 24.0, "unit": "PT"}
    assert style["foregroundColor"]["opaqueColor"]["rgbColor"] == {
        "red": 0.0, "green": 0.0, "blue": 0.0}


async def test_created_elements_are_grouped_and_the_group_is_reported(fake):
    reply, batch = await place(fake, MARKERS + (
        '<rect width="50" height="20"/><circle cx="80" cy="50" r="5"/>'
        '<line x1="0" y1="90" x2="100" y2="90" stroke="#000" marker-end="url(#tip)"/>'
        '<text x="10" y="70">Label</text>'))

    created = ([c["objectId"] for c in of_kind(batch, "createShape")]
               + [c["objectId"] for c in of_kind(batch, "createLine")])
    assert len(created) == 4 and all(len(oid) >= 5 for oid in created)
    assert "groupObjects" in batch[-1]
    group = batch[-1]["groupObjects"]
    assert sorted(group["childrenObjectIds"]) == sorted(created)
    assert len(group["groupObjectId"]) >= 5
    assert reply["placed"] == {"slide_id": SLIDE, "object_ids": [group["groupObjectId"]],
                               "source": "svg", "target": "box", "editable": True}


async def test_a_single_element_is_not_grouped(fake):
    reply, batch = await place(fake, '<rect width="50" height="20"/>')
    assert of_kind(batch, "groupObjects") == []
    (create,) = of_kind(batch, "createShape")
    assert reply["placed"]["object_ids"] == [create["objectId"]]


async def test_two_placements_use_different_object_ids(fake):
    body = '<rect width="50" height="20"/><rect x="60" width="50" height="20"/>'
    await place_image(DECK, slide_id=SLIDE, svg=svg(body), box=BOX, editable=True)
    await place_image(DECK, slide_id=SLIDE, svg=svg(body), box=BOX, editable=True)
    first, second = ({c["objectId"] for c in of_kind(b, "createShape")} for b in fake.batches)
    assert not first & second


async def test_reply_carries_a_receipt_of_the_slide(fake):
    out = await place_image(DECK, slide_id=SLIDE, svg=svg('<rect width="5" height="5"/>'),
                            box=BOX, editable=True)
    assert isinstance(out, list) and len(out) == 2
    assert fake.thumbnails == [(SLIDE, "MEDIUM")]


async def test_google_rejecting_the_batch_passes_its_reason_on(fake):
    fake.fail = SlidesApiError(
        "Slides API error 400: Invalid requests[3].groupObjects: All page elements should "
        "be on the same page.", status=400, reason="x")
    reply = await place_image(DECK, slide_id=SLIDE, svg=svg(TWO_RECTS), box=BOX, editable=True)
    assert reply["error"] == {"kind": "google",
                              "message": "All page elements should be on the same page."}


# ---- placeholder target ---------------------------------------------------


def add_marker_box(fake: Any, oid: str, x_in: float, y_in: float, w_in: float,
                   h_in: float) -> None:
    slide = next(s for s in fake.deck["slides"] if s["objectId"] == SLIDE)
    slide["pageElements"].append(_box(oid, x_in, y_in, w_in, h_in, shape={
        "shapeType": "RECTANGLE", "text": _text([(MARKER + "\n", _style())])}))


async def test_placeholder_is_deleted_and_the_drawing_fills_its_box_in_one_batch(fake):
    add_marker_box(fake, "diagram_box", 1, 1, 4, 2)  # 72, 72, 288 x 144 pt
    reply, batch = await place(fake, TWO_RECTS, target={
        "placeholder": MARKER, "confirm_destructive": True})

    assert batch[0] == {"deleteObject": {"objectId": "diagram_box"}}
    left, right = of_kind(batch, "createShape")
    # 200 x 100 into 288 x 144: scale 1.44, exact fit.
    assert left["elementProperties"] == ep(72, 72, pytest.approx(144), pytest.approx(144))
    assert right["elementProperties"]["transform"]["translateX"] == pytest.approx(216)
    (group,) = of_kind(batch, "groupObjects")
    assert reply["placed"] == {"slide_id": SLIDE, "object_ids": [group["groupObjectId"]],
                               "source": "svg", "target": "placeholder", "editable": True}


async def test_every_marker_box_on_the_slide_gets_its_own_drawing(fake):
    add_marker_box(fake, "box_one", 1, 1, 4, 2)
    add_marker_box(fake, "box_two", 5, 3, 2, 1)
    reply, batch = await place(fake, TWO_RECTS, target={
        "placeholder": MARKER, "confirm_destructive": True})

    deleted = [r["objectId"] for r in of_kind(batch, "deleteObject")]
    assert deleted == ["box_one", "box_two"]
    groups = of_kind(batch, "groupObjects")
    assert len(groups) == 2
    assert reply["placed"]["object_ids"] == [g["groupObjectId"] for g in groups]
    ids = [c["objectId"] for c in of_kind(batch, "createShape")]
    assert len(set(ids)) == 4


async def test_placeholder_needs_confirm_destructive(fake):
    add_marker_box(fake, "diagram_box", 1, 1, 4, 2)
    reply = await place_image(DECK, slide_id=SLIDE, svg=svg(TWO_RECTS), placeholder=MARKER,
                              editable=True)
    assert reply["isError"] is True
    assert "deleteObject" in reply["warnings"][0]
    assert fake.batches == []


async def test_missing_marker_is_reported_before_any_write(fake):
    reply = await place_image(DECK, slide_id=SLIDE, svg=svg(TWO_RECTS), placeholder="{{NOPE}}",
                              editable=True, confirm_destructive=True)
    assert reply["error"]["kind"] == "marker"
    assert fake.batches == []


async def test_unknown_slide_is_a_target_error(fake):
    reply = await place_image(DECK, slide_id="no_such_slide", svg=svg(TWO_RECTS), box=BOX,
                              editable=True)
    assert reply["error"]["kind"] == "target"
    assert fake.batches == []


@pytest.mark.parametrize("kwargs, message", [
    ({"image_url": "https://example.com/a.png", "box": BOX}, "needs `svg`"),
    ({"svg": "<svg/>", "placeholder": MARKER, "fit": "cover"}, "fit"),
])
async def test_bad_editable_arguments_are_refused(fake, kwargs, message):
    with pytest.raises(ValueError, match=message):
        await place_image(DECK, slide_id=SLIDE, editable=True, **kwargs)
    assert fake.batches == []


# ---- rejection ------------------------------------------------------------


async def refused(fake: Any, body: str, **kw: Any) -> str:
    reply = await place_image(DECK, slide_id=SLIDE, svg=svg(body, **kw), box=BOX, editable=True)
    assert reply["isError"] is True, reply
    assert reply["error"]["kind"] == "svg"
    assert fake.batches == []
    return reply["error"]["message"]


async def test_path_and_gradient_are_both_listed_and_nothing_is_written(fake):
    message = await refused(fake, (
        '<defs><linearGradient id="lg"><stop offset="0" stop-color="#fff"/></linearGradient>'
        '</defs><rect width="10" height="10" fill="url(#lg)"/>'
        '<path id="blob" d="M10 100 C 40 10, 65 10, 95 100"/>'))
    assert "<linearGradient>" in message
    assert "<rect> fill=url(#lg)" in message
    assert message.count("url(#lg)") == 1  # one line per problem
    assert "<path> id=blob" in message
    assert "editable=false" in message


async def test_every_unsupported_feature_is_listed_in_one_error(fake):
    message = await refused(fake, (
        '<polyline points="0,0 10,10 20,0"/>'
        '<polygon points="0,0 10,10 20,0"/>'
        '<g transform="rotate(30)"><rect x="20" y="20" width="4" height="4"/></g>'
        '<rect width="4" height="4" transform="matrix(1 0 0 1 0 0)"/>'
        '<text x="10" y="30">Two <tspan font-weight="bold">runs</tspan></text>'
        '<image href="data:image/png;base64,AAAA" width="10" height="10"/>'
        '<use href="#x"/>'
        '<rect width="4" height="4" filter="url(#f)"/>'
        '<rect width="4" height="4" clip-path="url(#c)"/>'
        '<rect width="4" height="4" style="mask: url(#m)"/>'
        '<rect width="4" height="4" fill="hsl(0, 50%, 50%)"/>'))
    for expected in ("<polyline>", "<polygon>", "rotate()", "matrix()", "<tspan>", "<image>",
                     "<use>", "filter", "clip-path", "mask", "hsl(0, 50%, 50%)"):
        assert expected in message, expected


async def test_zero_size_elements_are_refused_because_slides_rejects_them_mid_batch(fake):
    message = await refused(fake, (
        '<rect width="0" height="10"/><circle cx="5" cy="5" r="0"/>'
        '<line x1="3" y1="3" x2="3" y2="3" stroke="#000"/>'))
    assert message.count("zero") == 3


async def test_marker_that_is_not_one_simple_child_is_refused(fake):
    message = await refused(fake, (
        '<defs><marker id="two"><circle r="1"/><circle r="2"/></marker></defs>'
        '<line x1="0" y1="0" x2="10" y2="0" stroke="#000" marker-end="url(#two)"/>'))
    assert "#two" in message


@pytest.mark.parametrize("bad, says", [
    ("<svg", "not well-formed"),
    ('<svg xmlns="http://www.w3.org/2000/svg"><rect width="1" height="1"/></svg>', "viewBox"),
    ('<svg viewBox="0 0 1 1"><rect width="1" height="1"/></svg>', "xmlns"),
    (f'<svg {NS} viewBox="0 0 10 10"><title>t</title></svg>', "draws nothing"),
])
async def test_svg_that_cannot_be_read_is_an_svg_error(fake, bad, says):
    reply = await place_image(DECK, slide_id=SLIDE, svg=bad, box=BOX, editable=True)
    assert reply["error"]["kind"] == "svg"
    assert says in reply["error"]["message"]
    assert fake.batches == []


async def test_width_and_height_stand_in_for_a_missing_view_box(fake):
    reply = split(await place_image(
        DECK, slide_id=SLIDE, editable=True, box=BOX,
        svg=f'<svg {NS} width="200" height="100"><rect width="200" height="100"/></svg>'))
    assert reply["isError"] is False
    (create,) = of_kind(fake.batches[0], "createShape")
    assert create["elementProperties"] == ep(40, 60, 300, 150)


@pytest.mark.parametrize("colour, rgb", [
    ("#fff", {"red": 1.0, "green": 1.0, "blue": 1.0}),
    ("white", {"red": 1.0, "green": 1.0, "blue": 1.0}),
    ("Navy", {"red": 0.0, "green": 0.0, "blue": 128 / 255}),
])
async def test_short_hex_and_named_colours_are_understood(fake, colour, rgb):
    _, batch = await place(fake, f'<rect width="10" height="10" fill="{colour}"/>')
    (props,) = of_kind(batch, "updateShapeProperties")
    assert props["shapeProperties"]["shapeBackgroundFill"]["solidFill"]["color"] == {
        "rgbColor": rgb}
