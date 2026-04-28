"""Normalize Google Slides API `pageElement` JSON into a flat internal shape.

We don't want classifier + projection to know about Slides API quirks directly.
Normalize once, reason everywhere.
"""
from __future__ import annotations

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


@dataclass
class FlatShape:
    object_id: str
    kind: ShapeKind
    left_in: float
    top_in: float
    w_in: float
    h_in: float
    # kind-specific, all optional
    text: str | None = None
    runs: list[TextRun] = field(default_factory=list)
    shape_type: str | None = None       # e.g. RECTANGLE, TEXT_BOX, LINE
    fill_hex: str | None = None          # resolved shape fill
    outline_hex: str | None = None
    image_url: str | None = None
    children: list[FlatShape] = field(default_factory=list)
    has_rotation: bool = False


def _emu_in(v: int | float | None) -> float:
    return round((v or 0) / EMU_PER_INCH, 3)


def _unwrap_dim(d: dict[str, Any] | None) -> float:
    """size.width is {magnitude, unit}. Unit is EMU for all API responses."""
    if not d:
        return 0.0
    mag = d.get("magnitude", 0)
    unit = d.get("unit", "EMU")
    if unit == "EMU":
        return _emu_in(mag)
    if unit == "PT":
        return round(mag / 72, 3)
    return float(mag or 0)


def _extract_transform(
    transform: dict[str, Any] | None,
) -> tuple[float, float, float, float, bool]:
    """Return (translate_x_in, translate_y_in, scale_x, scale_y, has_rotation).

    Google Slides API expresses a pageElement's rendered geometry as
    `size × transform` — size is the INTRINSIC (unscaled) width/height,
    transform.scaleX/scaleY is the per-axis scale applied to map into page
    coords. scaleX/scaleY are omitted from the payload when identity (1.0);
    we coerce None → 1.0.

    Callers should multiply size.width by scale_x (and height by scale_y)
    to get the width/height the agent actually sees on the slide. Using raw
    `size.magnitude` returns 5.25in for a pill card on a 10×5.625 deck even
    though the rendered width is 5.25 × (10/16) = 3.281in — the regression
    this channel exists to fix.
    """
    if not transform:
        return 0.0, 0.0, 1.0, 1.0, False
    tx = _emu_in(transform.get("translateX", 0))
    ty = _emu_in(transform.get("translateY", 0))
    sx_raw = transform.get("scaleX")
    sy_raw = transform.get("scaleY")
    sx = float(sx_raw) if sx_raw is not None else 1.0
    sy = float(sy_raw) if sy_raw is not None else 1.0
    has_rot = bool(transform.get("shearX") or transform.get("shearY"))
    return tx, ty, sx, sy, has_rot


def _rgb_to_hex(rgb: dict[str, Any] | None) -> str | None:
    if not rgb:
        return None
    r = int(round((rgb.get("red", 0) or 0) * 255))
    g = int(round((rgb.get("green", 0) or 0) * 255))
    b = int(round((rgb.get("blue", 0) or 0) * 255))
    return f"#{r:02X}{g:02X}{b:02X}"


def _resolve_color(color_obj: dict[str, Any] | None) -> str | None:
    """color objects can be opaqueColor.{rgbColor|themeColor}."""
    if not color_obj:
        return None
    if "opaqueColor" in color_obj:
        return _resolve_color(color_obj["opaqueColor"])
    if "rgbColor" in color_obj:
        return _rgb_to_hex(color_obj["rgbColor"])
    # themeColor stays unresolved — caller decides how to handle theme refs
    return None


def _fill_hex(shape_props: dict[str, Any]) -> str | None:
    fill = shape_props.get("shapeBackgroundFill") or {}
    solid = fill.get("solidFill") or {}
    return _resolve_color(solid.get("color"))


def _outline_hex(shape_props: dict[str, Any]) -> str | None:
    outline = shape_props.get("outline") or {}
    fill = outline.get("outlineFill") or {}
    solid = fill.get("solidFill") or {}
    return _resolve_color(solid.get("color"))


def _extract_text(shape: dict[str, Any]) -> tuple[str, list[TextRun]]:
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
        color = _resolve_color((style.get("foregroundColor") or {}).get("opaqueColor"))
        if color is None:
            color = _resolve_color(style.get("foregroundColor"))
        runs.append(TextRun(
            content=text,
            font_family=font,
            size_pt=size_pt,
            bold=bool(style.get("bold")),
            italic=bool(style.get("italic")),
            color_hex=color,
        ))
        content_parts.append(text)
    return "".join(content_parts), runs


def _normalize_element(el: dict[str, Any]) -> FlatShape:
    object_id = el.get("objectId", "")
    size = el.get("size") or {}
    w_intrinsic = _unwrap_dim(size.get("width"))
    h_intrinsic = _unwrap_dim(size.get("height"))
    left_in, top_in, scale_x, scale_y, has_rot = _extract_transform(el.get("transform"))
    # Rendered geometry = intrinsic × scale. When scale is identity (1.0),
    # rounding preserves the integer/half values fixtures depend on.
    w_in = round(w_intrinsic * scale_x, 3)
    h_in = round(h_intrinsic * scale_y, 3)

    if "elementGroup" in el:
        children = [_normalize_element(c) for c in el["elementGroup"].get("children") or []]
        return FlatShape(
            object_id=object_id, kind="group",
            left_in=left_in, top_in=top_in, w_in=w_in, h_in=h_in,
            children=children, has_rotation=has_rot,
        )

    if "image" in el:
        img = el["image"]
        return FlatShape(
            object_id=object_id, kind="picture",
            left_in=left_in, top_in=top_in, w_in=w_in, h_in=h_in,
            image_url=img.get("contentUrl") or img.get("sourceUrl"),
            has_rotation=has_rot,
        )

    if "table" in el:
        return FlatShape(
            object_id=object_id, kind="table",
            left_in=left_in, top_in=top_in, w_in=w_in, h_in=h_in,
            has_rotation=has_rot,
        )

    if "sheetsChart" in el or "chart" in el:
        return FlatShape(
            object_id=object_id, kind="chart",
            left_in=left_in, top_in=top_in, w_in=w_in, h_in=h_in,
            has_rotation=has_rot,
        )

    if "line" in el:
        line = el["line"]
        line_props = line.get("lineProperties") or {}
        line_fill = (line_props.get("lineFill") or {}).get("solidFill") or {}
        return FlatShape(
            object_id=object_id, kind="line",
            left_in=left_in, top_in=top_in, w_in=w_in, h_in=h_in,
            shape_type=line.get("lineType", "LINE"),
            outline_hex=_resolve_color(line_fill.get("color")),
            has_rotation=has_rot,
        )

    if "shape" in el:
        shape = el["shape"]
        shape_type = shape.get("shapeType", "UNKNOWN")
        shape_props = shape.get("shapeProperties") or {}
        text, runs = _extract_text(shape)
        kind: ShapeKind = "text" if (text.strip() or shape_type == "TEXT_BOX") else "shape"
        return FlatShape(
            object_id=object_id, kind=kind,
            left_in=left_in, top_in=top_in, w_in=w_in, h_in=h_in,
            text=text, runs=runs,
            shape_type=shape_type,
            fill_hex=_fill_hex(shape_props),
            outline_hex=_outline_hex(shape_props),
            has_rotation=has_rot,
        )

    return FlatShape(
        object_id=object_id, kind="other",
        left_in=left_in, top_in=top_in, w_in=w_in, h_in=h_in,
        has_rotation=has_rot,
    )


def normalize_page(page: dict[str, Any]) -> list[FlatShape]:
    """Turn a Slides API Page.pageElements array into FlatShapes."""
    elements = page.get("pageElements") or []
    return [_normalize_element(e) for e in elements]


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
