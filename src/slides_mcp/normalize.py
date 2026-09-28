"""Normalize Google Slides API `pageElement` JSON into a flat internal shape.

We don't want classifier + projection to know about Slides API quirks directly.
Normalize once, reason everywhere.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Literal

EMU_PER_INCH = 914400


ShapeKind = Literal["text", "picture", "shape", "line", "table", "chart", "group", "other"]


@dataclass
class TextRun:
    content: str
    font_family: str | None = None
    size_pt: float | None = None
    bold: bool = False
    italic: bool = False
    color_hex: str | None = None
    # Added in 2.2: the faithful read model.
    weight: int | None = None            # weightedFontFamily.weight (400, 700, ...)
    start: int = 0                       # UTF-16 start index within the shape text
    end: int = 0                         # UTF-16 end index (exclusive)
    color_theme: str | None = None       # theme slot name when the colour is a theme ref
    color_inherited: bool = False        # no foregroundColor on the run


@dataclass
class FlatShape:
    object_id: str
    kind: ShapeKind
    # Page-space bounding box in inches. Rotation, shear and every enclosing
    # group transform are applied, so this is where the element really sits.
    left_in: float
    top_in: float
    w_in: float
    h_in: float
    # kind-specific, all optional
    text: str | None = None
    runs: list[TextRun] = field(default_factory=list)
    shape_type: str | None = None       # e.g. RECTANGLE, TEXT_BOX, LINE
    fill_hex: str | None = None          # solid fill colour; None when nothing is painted
    outline_hex: str | None = None
    image_url: str | None = None
    children: list[FlatShape] = field(default_factory=list)
    has_rotation: bool = False
    # Added in 2.2.
    fill: dict[str, Any] | None = None       # {kind, hex?, alpha?, theme?}
    outline: dict[str, Any] | None = None    # {kind, hex?, alpha?, theme?, weight_pt?}
    autofit: str | None = None               # NONE | TEXT_AUTOFIT | SHAPE_AUTOFIT
    rotation_deg: float = 0.0
    # Absolute affine transform in EMU: [scaleX, shearX, shearY, scaleY, tx, ty]
    matrix: tuple[float, float, float, float, float, float] = (1, 0, 0, 1, 0, 0)
    size_emu: tuple[float, float] = (0.0, 0.0)   # intrinsic width, height
    box_emu: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)  # page bbox
    placeholder: str | None = None
    parent_id: str | None = None


Matrix = tuple[float, float, float, float, float, float]
IDENTITY: Matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def _emu_in(v: int | float | None) -> float:
    return round((v or 0) / EMU_PER_INCH, 3)


def _dim_emu(d: dict[str, Any] | None) -> float:
    """size.width is {magnitude, unit}; convert to EMU."""
    if not d:
        return 0.0
    mag = float(d.get("magnitude", 0) or 0)
    if d.get("unit", "EMU") == "PT":
        return mag * 12700
    return mag


def _unwrap_dim(d: dict[str, Any] | None) -> float:
    return _emu_in(_dim_emu(d))


def _matrix(transform: dict[str, Any] | None) -> Matrix:
    """AffineTransform JSON -> (a, c, b, d, tx, ty) in EMU.

    The API omits zero-valued fields. A transform carrying neither scale nor
    shear is the identity; otherwise a missing field is 0 (a 90 degree
    rotation has scaleX 0, so it is simply absent).
    """
    if not transform:
        return IDENTITY
    keys = ("scaleX", "scaleY", "shearX", "shearY")
    present = any(k in transform for k in keys)
    has_shear = bool(transform.get("shearX") or transform.get("shearY"))
    default_scale = 0.0 if has_shear else 1.0
    if not present:
        default_scale = 1.0
    sx = float(transform.get("scaleX", default_scale))
    sy = float(transform.get("scaleY", default_scale))
    shx = float(transform.get("shearX", 0.0) or 0.0)
    shy = float(transform.get("shearY", 0.0) or 0.0)
    tx = float(transform.get("translateX", 0.0) or 0.0)
    ty = float(transform.get("translateY", 0.0) or 0.0)
    if transform.get("unit") == "PT":
        tx *= 12700
        ty *= 12700
    return (sx, shx, shy, sy, tx, ty)


def compose(parent: Matrix, child: Matrix) -> Matrix:
    """parent x child: the child's transform expressed in the parent's space."""
    a1, c1, b1, d1, e1, f1 = parent
    a2, c2, b2, d2, e2, f2 = child
    return (
        a1 * a2 + c1 * b2,
        a1 * c2 + c1 * d2,
        b1 * a2 + d1 * b2,
        b1 * c2 + d1 * d2,
        a1 * e2 + c1 * f2 + e1,
        b1 * e2 + d1 * f2 + f1,
    )


def bbox(m: Matrix, w: float, h: float) -> tuple[float, float, float, float]:
    """Axis-aligned page bbox (x, y, w, h) of a w x h box under matrix m."""
    a, c, b, d, e, f = m
    xs = [e + a * x + c * y for x, y in ((0, 0), (w, 0), (0, h), (w, h))]
    ys = [f + b * x + d * y for x, y in ((0, 0), (w, 0), (0, h), (w, h))]
    return min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)


def rotation_deg(m: Matrix) -> float:
    a, _c, b, _d, _e, _f = m
    if not a and not b:
        return 0.0
    deg = math.degrees(math.atan2(b, a))
    deg = round(deg, 2)
    return 0.0 if deg == 0 else deg


def _rgb_to_hex(rgb: dict[str, Any] | None) -> str | None:
    """RgbColor -> #RRGGBB. The API omits zero channels, so `{}` is black."""
    if rgb is None:
        return None
    r = int(round((rgb.get("red", 0) or 0) * 255))
    g = int(round((rgb.get("green", 0) or 0) * 255))
    b = int(round((rgb.get("blue", 0) or 0) * 255))
    return f"#{r:02X}{g:02X}{b:02X}"


def _color_parts(
    color_obj: dict[str, Any] | None,
    theme: dict[str, str] | None = None,
) -> tuple[str | None, str | None]:
    """OpaqueColor / OptionalColor / SolidFill.color -> (hex, theme_slot)."""
    if not color_obj:
        return None, None
    if "opaqueColor" in color_obj:
        return _color_parts(color_obj["opaqueColor"], theme)
    if "rgbColor" in color_obj:
        return _rgb_to_hex(color_obj["rgbColor"]), None
    if slot := color_obj.get("themeColor"):
        return (theme or {}).get(slot), slot
    return None, None


def _resolve_color(
    color_obj: dict[str, Any] | None,
    theme: dict[str, str] | None = None,
) -> str | None:
    """Colour object -> hex. Theme refs resolve only when a theme is given."""
    return _color_parts(color_obj, theme)[0]


def fill_record(
    fill: dict[str, Any] | None,
    theme: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Shape / page background fill -> {kind, hex?, alpha?, theme?}.

    kind is none | solid | image | inherit | other. `NOT_RENDERED` is none
    even though the API still ships a default white `solidFill` beside it;
    that white is what the pre-2.2 read model wrongly reported. `other` is a
    painted fill the API does not describe (an empty `{}` object); in
    practice that is a gradient.
    """
    if fill is None:
        return {"kind": "none"}
    state = fill.get("propertyState", "RENDERED")
    if state == "NOT_RENDERED":
        return {"kind": "none"}
    if state == "INHERIT":
        return {"kind": "inherit"}
    if solid := fill.get("solidFill"):
        hex_, slot = _color_parts(solid.get("color"), theme)
        rec: dict[str, Any] = {"kind": "solid", "hex": hex_ if (hex_ or slot) else "#000000",
                               "alpha": round(float(solid.get("alpha", 1.0)), 3)}
        if slot:
            rec["theme"] = slot
        return rec
    if fill.get("stretchedPictureFill"):
        return {"kind": "image"}
    return {"kind": "other"}


def _outline_record(
    outline: dict[str, Any] | None,
    theme: dict[str, str] | None = None,
) -> dict[str, Any]:
    if not outline:
        return {"kind": "none"}
    state = outline.get("propertyState", "RENDERED")
    if state == "NOT_RENDERED":
        return {"kind": "none"}
    if state == "INHERIT":
        return {"kind": "inherit"}
    rec = fill_record(outline.get("outlineFill"), theme) if outline.get("outlineFill") else {
        "kind": "other"}
    if w := outline.get("weight"):
        rec["weight_pt"] = round(_dim_emu(w) / 12700, 2)
    return rec


def _fill_hex(shape_props: dict[str, Any], theme: dict[str, str] | None = None) -> str | None:
    rec = fill_record(shape_props.get("shapeBackgroundFill"), theme)
    return rec.get("hex") if rec["kind"] == "solid" else None


def _outline_hex(shape_props: dict[str, Any], theme: dict[str, str] | None = None) -> str | None:
    rec = _outline_record(shape_props.get("outline"), theme)
    return rec.get("hex") if rec["kind"] == "solid" else None


def _extract_text(
    shape: dict[str, Any],
    theme: dict[str, str] | None = None,
) -> tuple[str, list[TextRun]]:
    text_obj = shape.get("text") or {}
    elements = text_obj.get("textElements") or []
    content_parts: list[str] = []
    runs: list[TextRun] = []
    for el in elements:
        run = el.get("textRun")
        if not run:
            continue
        text = run.get("content", "")
        style = run.get("style") or {}
        font = style.get("fontFamily")
        size_pt = None
        if fs := style.get("fontSize"):
            size_pt = float(fs.get("magnitude", 0)) if fs.get("unit") == "PT" else None
        fg = style.get("foregroundColor")
        color, slot = _color_parts(fg, theme)
        wff = style.get("weightedFontFamily") or {}
        start = int(el.get("startIndex", 0) or 0)
        runs.append(TextRun(
            content=text,
            font_family=font,
            size_pt=size_pt,
            bold=bool(style.get("bold")),
            italic=bool(style.get("italic")),
            color_hex=color,
            weight=wff.get("weight"),
            start=start,
            end=int(el.get("endIndex", start + utf16_len(text))),
            color_theme=slot,
            color_inherited=not fg,
        ))
        content_parts.append(text)
    return "".join(content_parts), runs


def utf16_len(s: str) -> int:
    """Length in UTF-16 code units: the unit every Slides text index uses."""
    return len(s.encode("utf-16-le")) // 2


def _normalize_element(
    el: dict[str, Any],
    theme: dict[str, str] | None = None,
    parent: Matrix = IDENTITY,
    parent_id: str | None = None,
) -> FlatShape:
    object_id = el.get("objectId", "")
    size = el.get("size") or {}
    w_emu = _dim_emu(size.get("width"))
    h_emu = _dim_emu(size.get("height"))
    local = _matrix(el.get("transform"))
    m = compose(parent, local)
    bx, by, bw, bh = bbox(m, w_emu, h_emu)
    rot = rotation_deg(m)
    has_rot = bool(m[1] or m[2])
    geo = {
        "left_in": _emu_in(bx), "top_in": _emu_in(by),
        "w_in": _emu_in(bw), "h_in": _emu_in(bh),
        "has_rotation": has_rot, "rotation_deg": rot, "matrix": m,
        "size_emu": (w_emu, h_emu), "box_emu": (bx, by, bw, bh),
        "parent_id": parent_id,
    }

    if "elementGroup" in el:
        children = [
            _normalize_element(c, theme, m, object_id)
            for c in el["elementGroup"].get("children") or []
        ]
        if children and not (w_emu or h_emu):
            # Groups carry no size; their box is the union of the children.
            xs = [c.box_emu[0] for c in children] + [c.box_emu[0] + c.box_emu[2] for c in children]
            ys = [c.box_emu[1] for c in children] + [c.box_emu[1] + c.box_emu[3] for c in children]
            bx, by, bw, bh = min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)
            geo.update(left_in=_emu_in(bx), top_in=_emu_in(by), w_in=_emu_in(bw),
                       h_in=_emu_in(bh), box_emu=(bx, by, bw, bh))
        return FlatShape(object_id=object_id, kind="group", children=children, **geo)

    if "image" in el:
        img = el["image"]
        return FlatShape(
            object_id=object_id, kind="picture",
            image_url=img.get("contentUrl") or img.get("sourceUrl"), **geo,
        )

    if "table" in el:
        return FlatShape(object_id=object_id, kind="table", **geo)

    if "sheetsChart" in el or "chart" in el:
        return FlatShape(object_id=object_id, kind="chart", **geo)

    if "line" in el:
        line = el["line"]
        line_props = line.get("lineProperties") or {}
        line_fill = fill_record(line_props.get("lineFill"), theme)
        if w := line_props.get("weight"):
            line_fill["weight_pt"] = round(_dim_emu(w) / 12700, 2)
        return FlatShape(
            object_id=object_id, kind="line",
            shape_type=line.get("lineType", "LINE"),
            outline_hex=line_fill.get("hex"),
            outline=line_fill, **geo,
        )

    if "shape" in el:
        shape = el["shape"]
        shape_type = shape.get("shapeType", "UNKNOWN")
        shape_props = shape.get("shapeProperties") or {}
        text, runs = _extract_text(shape, theme)
        kind: ShapeKind = "text" if (text.strip() or shape_type == "TEXT_BOX") else "shape"
        fill = fill_record(shape_props.get("shapeBackgroundFill"), theme)
        outline = _outline_record(shape_props.get("outline"), theme)
        autofit = (shape_props.get("autofit") or {}).get("autofitType")
        return FlatShape(
            object_id=object_id, kind=kind,
            text=text, runs=runs,
            shape_type=shape_type,
            fill_hex=fill.get("hex") if fill["kind"] == "solid" else None,
            outline_hex=outline.get("hex") if outline["kind"] == "solid" else None,
            fill=fill, outline=outline, autofit=autofit,
            placeholder=(shape.get("placeholder") or {}).get("type"),
            **geo,
        )

    return FlatShape(object_id=object_id, kind="other", **geo)


def normalize_page(
    page: dict[str, Any],
    theme: dict[str, str] | None = None,
) -> list[FlatShape]:
    """Turn a Slides API Page.pageElements array into FlatShapes.

    `theme` maps theme slot names (ACCENT1, DARK1, ...) to hex so theme
    colour refs resolve; see `DeckContext.theme_for`.
    """
    elements = page.get("pageElements") or []
    return [_normalize_element(e, theme) for e in elements]


def flatten(shapes: list[FlatShape]) -> list[FlatShape]:
    """Recursively flatten groups. Used by classifier."""
    out: list[FlatShape] = []
    for s in shapes:
        if s.kind == "group":
            out.extend(flatten(s.children))
        else:
            out.append(s)
    return out


def extract_notes(slide: dict[str, Any]) -> tuple[str, str | None]:
    """Pull speaker notes + the notes body objectId.

    Returns (text, object_id). object_id is the pageElement id of the notes
    BODY placeholder — callers need it to emit per-object notes edits
    (deleteText + insertText). Returns ("", None) when no notes body exists.
    """
    notes_page = (slide.get("slideProperties") or {}).get("notesPage")
    if not notes_page:
        return "", None
    for el in notes_page.get("pageElements") or []:
        shape = el.get("shape") or {}
        placeholder = (shape.get("placeholder") or {}).get("type")
        if placeholder != "BODY":
            continue
        text, _ = _extract_text(shape)
        return text.strip(), el.get("objectId")
    return "", None


def extract_notes_text(slide: dict[str, Any]) -> str:
    """Back-compat wrapper: returns only the text."""
    return extract_notes(slide)[0]


def is_hidden(slide: dict[str, Any]) -> bool:
    """True when the slide has `slideProperties.isSkipped: true`.

    Hidden slides include backups, drafts, and the v0.x meta-slide marker.
    Not surfaced by Google Slides UI presentations but real metadata.
    """
    return bool((slide.get("slideProperties") or {}).get("isSkipped"))


def layout_id(slide: dict[str, Any]) -> str | None:
    """Return `slideProperties.layoutObjectId` if present.

    Useful for finding all slides that use a given template/layout in a deck
    review (e.g. "every slide on the deprecated dark-cover layout").
    """
    return (slide.get("slideProperties") or {}).get("layoutObjectId")


# ---- deck context: theme colours, backgrounds, notes shapes (2.2) -----------

# The Slides editor default scheme, used when no page in the chain sets one.
_DEFAULT_THEME = {
    "DARK1": "#000000", "LIGHT1": "#FFFFFF", "DARK2": "#595959", "LIGHT2": "#EEEEEE",
    "ACCENT1": "#4285F4", "ACCENT2": "#212121", "ACCENT3": "#78909C",
    "ACCENT4": "#FFAB40", "ACCENT5": "#0097A7", "ACCENT6": "#EEFF41",
    "HYPERLINK": "#0097A7", "FOLLOWED_HYPERLINK": "#0097A7",
}
# TEXT/BACKGROUND slots are aliases the editor maps onto DARK/LIGHT slots.
_THEME_ALIASES = {"TEXT1": "DARK1", "BACKGROUND1": "LIGHT1",
                  "TEXT2": "DARK2", "BACKGROUND2": "LIGHT2"}


def _scheme(page: dict[str, Any] | None) -> dict[str, str]:
    colors = (((page or {}).get("pageProperties") or {}).get("colorScheme") or {}).get(
        "colors") or []
    out: dict[str, str] = {}
    for c in colors:
        if (slot := c.get("type")) and "color" in c:
            out[slot] = _rgb_to_hex(c["color"]) or "#000000"
    return out


class DeckContext:
    """Per-presentation lookups that need more than one page: the theme
    colour chain and the background chain (slide -> layout -> master)."""

    def __init__(self, prez: dict[str, Any]):
        self.prez = prez
        self.masters = {m["objectId"]: m for m in prez.get("masters") or [] if "objectId" in m}
        self.layouts = {x["objectId"]: x for x in prez.get("layouts") or [] if "objectId" in x}

    def _chain(self, slide: dict[str, Any]) -> list[dict[str, Any]]:
        props = slide.get("slideProperties") or {}
        layout = self.layouts.get(props.get("layoutObjectId") or "")
        master_id = props.get("masterObjectId") or (
            ((layout or {}).get("layoutProperties") or {}).get("masterObjectId"))
        master = self.masters.get(master_id or "")
        if master is None and len(self.masters) == 1:
            master = next(iter(self.masters.values()))
        return [p for p in (slide, layout, master) if p]

    def theme_for(self, slide: dict[str, Any]) -> dict[str, str]:
        theme = dict(_DEFAULT_THEME)
        for page in reversed(self._chain(slide)):
            theme.update(_scheme(page))
        for alias, target in _THEME_ALIASES.items():
            theme.setdefault(alias, theme.get(target, "#000000"))
        return theme

    def background_for(self, slide: dict[str, Any]) -> dict[str, Any]:
        theme = self.theme_for(slide)
        for page in self._chain(slide):
            fill = (page.get("pageProperties") or {}).get("pageBackgroundFill")
            if not fill:
                continue
            rec = fill_record(fill, theme)
            if rec["kind"] == "inherit":
                continue
            if rec["kind"] == "none" and page is not self._chain(slide)[-1]:
                # an unrendered background on a slide or layout defers upward
                continue
            return rec
        return {"kind": "solid", "hex": theme.get("LIGHT1", "#FFFFFF"), "alpha": 1.0}


def notes_shape(slide: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
    """(speakerNotesObjectId, that shape's JSON) for a slide's notes page."""
    notes_page = (slide.get("slideProperties") or {}).get("notesPage") or {}
    want = (notes_page.get("notesProperties") or {}).get("speakerNotesObjectId")
    fallback: tuple[str | None, dict[str, Any] | None] = (want, None)
    for el in notes_page.get("pageElements") or []:
        shape = el.get("shape") or {}
        oid = el.get("objectId")
        if want and oid == want:
            return oid, shape
        if not want and (shape.get("placeholder") or {}).get("type") == "BODY":
            fallback = (oid, shape)
    return fallback


def luminance(hex_: str | None) -> float | None:
    """Relative luminance 0..1 of #RRGGBB, for light/dark slide checks."""
    if not hex_ or len(hex_) != 7:
        return None
    r, g, b = (int(hex_[i:i + 2], 16) / 255 for i in (1, 3, 5))

    def lin(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return round(0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b), 4)
