"""SVG to native Slides shapes for place_image(editable=True).

Turns a small subset of SVG into `batchUpdate` requests that draw the same
picture with Slides' own shapes, lines and text boxes, so the user can edit
it. Anything outside the subset is refused as a whole, every problem listed,
before a single request is sent: no partial drawing, no mix with raster.

The constants below were measured against live Slides; the subset and the
measurements are described in docs/architecture/write-wedge.md.
"""
from __future__ import annotations

import re
import secrets
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any

from slides_mcp.svg_raster import SvgError

_NS = "{http://www.w3.org/2000/svg}"

# Presentation attributes read from the element, its `style` and its ancestors.
_PRESENTATION = ("fill", "stroke", "stroke-width", "stroke-dasharray", "fill-opacity",
                 "stroke-opacity", "opacity", "font-family", "font-size", "font-weight",
                 "font-style", "text-anchor", "dominant-baseline")
_NOT_INHERITED = ("opacity",)  # an element's own opacity multiplies into its colours only
_COMMON = {*_PRESENTATION, "style", "id", "class", "transform", "role"}
_ALLOWED = {
    "svg": {"viewBox", "width", "height", "version", "preserveAspectRatio", "style", "id",
            "class", "role", *_PRESENTATION},
    "g": _COMMON,
    "rect": _COMMON | {"x", "y", "width", "height", "rx", "ry"},
    "circle": _COMMON | {"cx", "cy", "r"},
    "ellipse": _COMMON | {"cx", "cy", "rx", "ry"},
    "line": _COMMON | {"x1", "y1", "x2", "y2", "marker-start", "marker-end"},
    "text": _COMMON,
}
_TEXT_ONLY = {"x", "y"}
_SKIPPED = {"title", "desc", "metadata"}  # draw nothing
_MARKER_CHILDREN = {"path", "polygon", "polyline", "circle"}

_NAMED = {
    "black": "000000", "white": "ffffff", "red": "ff0000", "green": "008000",
    "blue": "0000ff", "yellow": "ffff00", "orange": "ffa500", "purple": "800080",
    "gray": "808080", "grey": "808080", "silver": "c0c0c0", "navy": "000080",
    "teal": "008080", "maroon": "800000", "olive": "808000", "lime": "00ff00",
    "aqua": "00ffff", "cyan": "00ffff", "fuchsia": "ff00ff", "magenta": "ff00ff",
}
_HEX3 = re.compile(r"#([0-9a-f]{3})")
_HEX6 = re.compile(r"#([0-9a-f]{6})")
_RGB = re.compile(r"rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)")
_NUMBER = re.compile(r"\s*(-?(?:\d+\.?\d*|\.\d+)(?:e-?\d+)?)\s*(?:px)?\s*", re.IGNORECASE)
_FN_RE = re.compile(r"([A-Za-z]+)\s*\(([^)]*)\)")
_SPLIT = re.compile(r"[\s,]+")
_MARKER_REF = re.compile(r"url\(\s*#([^)\s]+)\s*\)")

# Text placement, measured on live Slides text boxes. The inner margin cannot
# be changed through the API (`textInsets` is refused), so the box is offset.
TEXT_INSET_PT = 7.2
BASELINE_TOP_PT = 6.75     # baseline below the box top: 6.75 + 0.9625 x size,
BASELINE_PER_PT = 0.9625   # the same for Arial, Roboto, Times New Roman, Courier New
DEFAULT_FONT_SIZE = 16.0   # SVG's `medium`
_BASELINE_SHIFT_EM = {"middle": 0.35, "central": 0.35, "hanging": 0.75,
                      "text-before-edge": 0.75}
_ALIGNMENT = {"start": "START", "middle": "CENTER", "end": "END"}
_BOLD = {"bold", "bolder", "600", "700", "800", "900"}
# Slides draws any family it does not know, generic CSS names included, in a
# serif fallback with no error (docs/traps/generic-font-family-renders-as-serif.md).
GENERIC_FAMILIES = {"sans-serif": "Arial", "serif": "Times New Roman",
                    "monospace": "Courier New", "system-ui": "Arial", "ui-sans-serif": "Arial",
                    "ui-serif": "Times New Roman", "ui-monospace": "Courier New"}

# (sx, sy, tx, ty): x' = tx + sx * x, y' = ty + sy * y
Transform = tuple[float, float, float, float]


class Unsupported(SvgError):
    """The SVG uses features with no native equivalent; `problems` lists each one."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        listed = "\n".join(f"  - {p}" for p in problems)
        super().__init__(
            f"editable=true cannot draw this SVG as native shapes; nothing was written. "
            f"Unsupported:\n{listed}\nRemove or replace these, or place the SVG as an image "
            f"with editable=false.")


@dataclass(frozen=True)
class Drawing:
    """Requests that draw one SVG on a slide, and the ids of the top-level elements."""
    requests: list[dict[str, Any]]
    object_ids: list[str]


def text_box_top(baseline: float, size: float) -> float:
    """Top of a TOP-aligned Slides text box whose first baseline is at `baseline`."""
    return baseline - (BASELINE_TOP_PT + BASELINE_PER_PT * size)


def text_box_width(text: str, size: float) -> float:
    """A box wide enough that the label never wraps: Slides wraps silently."""
    return 1.6 * 0.6 * size * len(text) + 2 * TEXT_INSET_PT + 20


def font_family(css: str) -> str:
    """The first family of a CSS list, with generic names made concrete."""
    first = css.split(",")[0].strip().strip("'\"").strip()
    return GENERIC_FAMILIES.get(first.lower(), first) if first else "Arial"


def _tag(el: ET.Element) -> str:
    return el.tag.replace(_NS, "")


def _label(el: ET.Element) -> str:
    return f"<{_tag(el)}>" + (f" id={el.get('id')}" if el.get("id") else "")


def _ignored_attribute(name: str) -> bool:
    """Attributes that never change the drawing: other namespaces, data-*, aria-*."""
    return name.startswith(("{", "data-", "aria-"))


def _num(value: str | None, default: float = 0.0) -> float:
    if value is None:
        return default
    m = _NUMBER.fullmatch(value)
    if not m:
        raise SvgError(f"length {value!r} (plain numbers or px only)")
    return float(m.group(1))


def _colour(value: str | None) -> dict[str, float] | None:
    """{red, green, blue} in 0..1, or None for `none` / absent."""
    if value is None:
        return None
    v = value.strip().lower()
    if v == "none":
        return None
    v = "#" + _NAMED[v] if v in _NAMED else v
    if m := _HEX3.fullmatch(v):
        v = "#" + "".join(c * 2 for c in m.group(1))
    if m := _HEX6.fullmatch(v):
        h = m.group(1)
        return {"red": int(h[0:2], 16) / 255, "green": int(h[2:4], 16) / 255,
                "blue": int(h[4:6], 16) / 255}
    if m := _RGB.fullmatch(v):
        r, g, b = (int(x) / 255 for x in m.groups())
        return {"red": r, "green": g, "blue": b}
    raise SvgError(f"colour {value!r} (hex, rgb() or a basic colour name only)")


def _declarations(el: ET.Element) -> dict[str, str]:
    out: dict[str, str] = {}
    for decl in (el.get("style") or "").split(";"):
        if ":" in decl:
            k, v = decl.split(":", 1)
            out[k.strip()] = v.strip()
    return out


def _style(el: ET.Element, inherited: dict[str, str]) -> dict[str, str]:
    st = {k: v for k, v in inherited.items() if k not in _NOT_INHERITED}
    for k in _PRESENTATION:
        if el.get(k) is not None:
            st[k] = el.get(k, "")
    st.update(_declarations(el))
    return st


def _parse_transform(value: str | None) -> Transform:
    """translate() and scale() lists; any other function raises SvgError."""
    sx, sy, tx, ty = 1.0, 1.0, 0.0, 0.0
    for fn, args in _FN_RE.findall(value or ""):
        a = [float(x) for x in _SPLIT.split(args.strip()) if x]
        if fn == "translate" and 1 <= len(a) <= 2:
            tx, ty = tx + sx * a[0], ty + sy * (a[1] if len(a) > 1 else 0.0)
        elif fn == "scale" and 1 <= len(a) <= 2:
            sx, sy = sx * a[0], sy * (a[1] if len(a) > 1 else a[0])
        else:
            raise SvgError(f"transform {fn}() (translate and scale only)")
    return sx, sy, tx, ty


def _compose(outer: Transform, inner: Transform) -> Transform:
    osx, osy, otx, oty = outer
    isx, isy, itx, ity = inner
    return osx * isx, osy * isy, otx + osx * itx, oty + osy * ity


def _element_properties(page_id: str, x: float, y: float, w: float, h: float,
                        sx: float = 1, sy: float = 1) -> dict[str, Any]:
    return {"pageObjectId": page_id,
            "size": {"width": {"magnitude": w, "unit": "PT"},
                     "height": {"magnitude": h, "unit": "PT"}},
            "transform": {"scaleX": sx, "scaleY": sy, "translateX": x, "translateY": y,
                          "unit": "PT"}}


def _dash(st: dict[str, str]) -> str:
    """Slides has fixed dash styles only; any dash array becomes DASH."""
    return "SOLID" if st.get("stroke-dasharray", "none").strip() == "none" else "DASH"


def _shape_properties(st: dict[str, str], scale: float) -> dict[str, Any]:
    """Fill and outline, both always written: a bare shape takes the theme's."""
    opacity = float(st.get("opacity", 1))
    props: dict[str, Any] = {}
    fields: list[str] = []
    fill = _colour(st.get("fill", "black"))
    if fill is None:
        props["shapeBackgroundFill"] = {"propertyState": "NOT_RENDERED"}
        fields.append("shapeBackgroundFill.propertyState")
    else:
        alpha = float(st.get("fill-opacity", 1)) * opacity
        props["shapeBackgroundFill"] = {"solidFill": {"color": {"rgbColor": fill},
                                                      "alpha": alpha}}
        fields += ["shapeBackgroundFill.solidFill.color", "shapeBackgroundFill.solidFill.alpha"]
    stroke = _colour(st.get("stroke"))
    if stroke is None:
        props["outline"] = {"propertyState": "NOT_RENDERED"}
        fields.append("outline.propertyState")
    else:
        alpha = float(st.get("stroke-opacity", 1)) * opacity
        props["outline"] = {
            "outlineFill": {"solidFill": {"color": {"rgbColor": stroke}, "alpha": alpha}},
            "weight": {"magnitude": _num(st.get("stroke-width"), 1) * scale, "unit": "PT"},
            "dashStyle": _dash(st)}
        fields += ["outline.outlineFill.solidFill.color", "outline.outlineFill.solidFill.alpha",
                   "outline.weight", "outline.dashStyle"]
    return {"shapeProperties": props, "fields": ",".join(fields)}


def _arrow(marker: ET.Element) -> str:
    """The Slides arrow style closest to what a one-child marker draws."""
    (child,) = list(marker)
    kind = _tag(child)
    filled = child.get("fill", "black").strip() != "none"
    if kind == "circle":
        return "FILL_CIRCLE" if filled else "OPEN_CIRCLE"
    closed = kind == "polygon" or (kind == "path" and "z" in child.get("d", "").lower())
    return "FILL_ARROW" if closed and filled else "OPEN_ARROW"


class _Builder:
    """One walk over the SVG: builds requests and collects every problem."""

    def __init__(self, root: ET.Element, page_id: str):
        self.page_id = page_id
        self.prefix = "svg" + secrets.token_hex(3)  # object ids need 5+ characters
        self.requests: list[dict[str, Any]] = []
        self.ids: list[str] = []
        self.problems: list[str] = []
        self.markers: dict[str, str] = {}  # id -> arrow style, for usable markers
        self.bad_markers: set[str] = set()

    def problem(self, text: str) -> None:
        if text not in self.problems:
            self.problems.append(text)

    def new_id(self) -> str:
        oid = f"{self.prefix}_{len(self.ids):03d}"
        self.ids.append(oid)
        return oid

    def defs(self, el: ET.Element) -> None:
        for child in el:
            if _tag(child) in _SKIPPED:
                continue
            if _tag(child) != "marker":
                self.problem(f"{_label(child)} in <defs> (only arrow markers are supported)")
                continue
            mid = child.get("id", "")
            kids = list(child)
            if len(kids) == 1 and _tag(kids[0]) in _MARKER_CHILDREN:
                self.markers[mid] = _arrow(child)
            else:
                self.bad_markers.add(mid)

    def arrow(self, el: ET.Element, attr: str) -> str:
        ref = el.get(attr)
        if not ref or ref.strip() == "none":
            return "NONE"
        m = _MARKER_REF.fullmatch(ref.strip())
        if m and m.group(1) in self.markers:
            return self.markers[m.group(1)]
        self.problem(f"<line> {attr}={ref} (a marker needs exactly one path, polygon, "
                     f"polyline or circle child, in <defs>)")
        return "NONE"

    def check_attributes(self, el: ET.Element, st: dict[str, str]) -> bool:
        """Lists what the element uses that is unsupported; False if it cannot be built."""
        t = _tag(el)
        before = len(self.problems)
        allowed = _ALLOWED[t] | (_TEXT_ONLY if t == "text" else set())
        for name in el.attrib:
            if name not in allowed and not _ignored_attribute(name):
                self.problem(f"<{t}> attribute {name}")
        for name in _declarations(el):
            if name not in _PRESENTATION:
                self.problem(f"<{t}> style {name}")
        for name in ("fill", "stroke"):
            value = st.get(name, "")
            if value.strip().startswith("url("):
                self.problem(f"<{t}> {name}={value} (gradient or pattern)")
            else:
                try:
                    _colour(value or None)
                except SvgError as e:
                    self.problem(f"<{t}> {name}: {e}")
        return len(self.problems) == before

    def walk(self, el: ET.Element, tf: Transform, inherited: dict[str, str],
             top: bool = False) -> None:
        t = _tag(el)
        if t in _SKIPPED:
            return
        if t == "defs":
            return  # read up front by convert()
        if t not in _ALLOWED or (t == "svg" and not top):
            self.problem(_label(el))
            return
        st = _style(el, inherited)
        buildable = self.check_attributes(el, st)
        try:
            tf = _compose(tf, _parse_transform(el.get("transform")))
        except SvgError as e:
            self.problem(f"<{t}> {e}")
            return
        try:
            if t in ("svg", "g"):
                for child in el:
                    self.walk(child, tf, st)
            elif not buildable:
                return
            elif t == "text":
                self.text(el, tf, st)
            elif t == "line":
                self.line(el, tf, st)
            else:
                self.shape_element(el, tf, st)
        except (SvgError, ValueError) as e:
            self.problem(f"{_label(el)}: {e}")

    def shape_element(self, el: ET.Element, tf: Transform, st: dict[str, str]) -> None:
        t = _tag(el)
        if t == "rect":
            x, y = _num(el.get("x")), _num(el.get("y"))
            w, h = _num(el.get("width")), _num(el.get("height"))
            rounded = _num(el.get("rx") or el.get("ry")) > 0
            kind, corners = "ROUND_RECTANGLE" if rounded else "RECTANGLE", (x, y, x + w, y + h)
        else:
            cx, cy = _num(el.get("cx")), _num(el.get("cy"))
            rx = _num(el.get("r") if t == "circle" else el.get("rx"))
            ry = _num(el.get("r") if t == "circle" else el.get("ry"))
            kind, corners = "ELLIPSE", (cx - rx, cy - ry, cx + rx, cy + ry)
        sx, sy, tx, ty = tf
        x0, y0 = tx + sx * corners[0], ty + sy * corners[1]
        x1, y1 = tx + sx * corners[2], ty + sy * corners[3]
        if x0 == x1 or y0 == y1:
            self.problem(f"{_label(el)} has zero width or height (Slides refuses it)")
            return
        props = _shape_properties(st, (abs(sx) + abs(sy)) / 2)
        oid = self.new_id()
        self.requests.append({"createShape": {
            "objectId": oid, "shapeType": kind,
            "elementProperties": _element_properties(
                self.page_id, min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0))}})
        self.requests.append({"updateShapeProperties": {"objectId": oid, **props}})

    def line(self, el: ET.Element, tf: Transform, st: dict[str, str]) -> None:
        start, end = self.arrow(el, "marker-start"), self.arrow(el, "marker-end")
        stroke = _colour(st.get("stroke"))
        if stroke is None:
            return  # an unstroked line draws nothing in SVG
        sx, sy, tx, ty = tf
        x1, y1 = tx + sx * _num(el.get("x1")), ty + sy * _num(el.get("y1"))
        x2, y2 = tx + sx * _num(el.get("x2")), ty + sy * _num(el.get("y2"))
        if x1 == x2 and y1 == y2:
            self.problem(f"{_label(el)} has zero length (Slides refuses it)")
            return
        alpha = float(st.get("stroke-opacity", 1)) * float(st.get("opacity", 1))
        weight = _num(st.get("stroke-width"), 1) * (abs(sx) + abs(sy)) / 2
        oid = self.new_id()
        # Direction is the sign of the scale, with the start point as the origin.
        self.requests.append({"createLine": {
            "objectId": oid, "category": "STRAIGHT",
            "elementProperties": _element_properties(
                self.page_id, x1, y1, abs(x2 - x1), abs(y2 - y1),
                -1 if x2 < x1 else 1, -1 if y2 < y1 else 1)}})
        self.requests.append({"updateLineProperties": {
            "objectId": oid,
            "fields": "lineFill.solidFill.color,lineFill.solidFill.alpha,weight,dashStyle,"
                      "startArrow,endArrow",
            "lineProperties": {
                "lineFill": {"solidFill": {"color": {"rgbColor": stroke}, "alpha": alpha}},
                "weight": {"magnitude": weight, "unit": "PT"},
                "dashStyle": _dash(st), "startArrow": start, "endArrow": end}}})

    def text(self, el: ET.Element, tf: Transform, st: dict[str, str]) -> None:
        for child in el:
            self.problem(f"<text> child <{_tag(child)}> (plain text only)")
        if len(el):
            return
        for name in ("x", "y"):
            if len(_SPLIT.split((el.get(name) or "0").strip())) > 1:
                self.problem(f"<text> {name} with several values (one position only)")
                return
        content = " ".join((el.text or "").split())
        if not content:
            return
        anchor = st.get("text-anchor", "start").strip()
        if anchor not in _ALIGNMENT:
            self.problem(f"<text> text-anchor={anchor}")
            return
        sx, sy, tx, ty = tf
        size = _num(st.get("font-size"), DEFAULT_FONT_SIZE) * (abs(sx) + abs(sy)) / 2
        x, y = tx + sx * _num(el.get("x")), ty + sy * _num(el.get("y"))
        baseline = y + _BASELINE_SHIFT_EM.get(st.get("dominant-baseline", "auto").strip(),
                                              0.0) * size
        width = text_box_width(content, size)
        left = {"start": x - TEXT_INSET_PT, "middle": x - width / 2,
                "end": x + TEXT_INSET_PT - width}[anchor]
        colour = _colour(st.get("fill", "black")) or {"red": 0.0, "green": 0.0, "blue": 0.0}
        oid = self.new_id()
        self.requests += [
            {"createShape": {"objectId": oid, "shapeType": "TEXT_BOX",
                             "elementProperties": _element_properties(
                                 self.page_id, left, text_box_top(baseline, size), width,
                                 size * 1.3 + 2 * TEXT_INSET_PT)}},
            {"insertText": {"objectId": oid, "text": content}},
            {"updateTextStyle": {
                "objectId": oid, "fields": "fontFamily,fontSize,foregroundColor,bold,italic",
                "style": {
                    "fontFamily": font_family(st.get("font-family", "sans-serif")),
                    "fontSize": {"magnitude": round(size, 2), "unit": "PT"},
                    "foregroundColor": {"opaqueColor": {"rgbColor": colour}},
                    "bold": st.get("font-weight", "normal").strip() in _BOLD,
                    "italic": st.get("font-style", "normal").strip() in ("italic", "oblique")}}},
            {"updateParagraphStyle": {"objectId": oid, "fields": "alignment",
                                      "style": {"alignment": _ALIGNMENT[anchor]}}},
            # Autofit needs nothing: new text boxes read back NONE, the only value
            # the API accepts. The measured baseline assumes TOP alignment.
            {"updateShapeProperties": {"objectId": oid, "fields": "contentAlignment",
                                       "shapeProperties": {"contentAlignment": "TOP"}}},
        ]


def _root(svg: str) -> tuple[ET.Element, list[float]]:
    try:
        root = ET.fromstring(svg)
    except ET.ParseError as e:
        raise SvgError(f"The SVG is not well-formed XML: {e}.") from e
    if root.tag == "svg":
        raise SvgError('The root <svg> needs xmlns="http://www.w3.org/2000/svg".')
    if root.tag != f"{_NS}svg":
        raise SvgError(f"The root element must be <svg>, not <{_tag(root)}>.")
    try:
        if root.get("viewBox"):
            vb = [float(v) for v in _SPLIT.split(root.get("viewBox", "").strip())]
        else:
            vb = [0.0, 0.0, _num(root.get("width")), _num(root.get("height"))]
    except (SvgError, ValueError):
        vb = []
    if len(vb) != 4 or vb[2] <= 0 or vb[3] <= 0:
        raise SvgError("The root <svg> needs a viewBox, or a numeric width and height.")
    return root, vb


def convert(svg: str, page_id: str, box: tuple[float, float, float, float]) -> Drawing:
    """Requests drawing `svg` on `page_id`, fitted into `box` (x, y, w, h in points).

    The viewBox is scaled to fit and centred (SVG's `xMidYMid meet`), the same
    fit the raster path uses. Two or more elements are grouped. Raises
    `Unsupported` listing every problem, or `SvgError` for an unreadable SVG.
    """
    root, (minx, miny, vw, vh) = _root(svg)
    bx, by, bw, bh = box
    k = min(bw / vw, bh / vh)
    origin = (k, k, bx + (bw - vw * k) / 2 - minx * k, by + (bh - vh * k) / 2 - miny * k)
    builder = _Builder(root, page_id)
    for defs in root.iter(f"{_NS}defs"):  # markers first, wherever <defs> sits
        builder.defs(defs)
    builder.walk(root, origin, {}, top=True)
    if builder.problems:
        raise Unsupported(builder.problems)
    if not builder.ids:
        raise SvgError("The SVG draws nothing: no supported element with something visible.")
    if len(builder.ids) == 1:  # Slides refuses a group of one
        return Drawing(builder.requests, builder.ids)
    group_id = f"{builder.prefix}_grp"
    return Drawing([*builder.requests, {"groupObjects": {
        "groupObjectId": group_id, "childrenObjectIds": builder.ids}}], [group_id])
