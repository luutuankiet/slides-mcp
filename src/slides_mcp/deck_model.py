"""The deck as a script sees it: one JSON snapshot built from a presentation.

`run_deck_script` hands this to the sandbox. It always carries the full read
model (the raw `read_slides` projection shows only the non-default parts).
Every coordinate is EMU in page space unless the key says otherwise.
"""
from __future__ import annotations

from typing import Any

from . import normalize, notes_md
from .normalize import EMU_PER_INCH, FlatShape

_R = 3  # decimals for inch values


def _run(r: normalize.TextRun) -> dict[str, Any]:
    return {
        "start": r.start,
        "end": r.end,
        "text": r.content,
        "fontFamily": r.font_family,
        "weight": r.weight,
        "bold": r.bold,
        "italic": r.italic,
        "sizePt": r.size_pt,
        "color": "inherited" if r.color_inherited else r.color_hex,
        "colorTheme": r.color_theme,
    }


def _element(s: FlatShape, raw_transform: dict[str, Any] | None,
             parent_matrix: normalize.Matrix) -> dict[str, Any]:
    x, y, w, h = s.box_emu
    e: dict[str, Any] = {
        "id": s.object_id,
        "kind": s.kind,
        "shapeType": s.shape_type,
        "placeholder": s.placeholder,
        "parentId": s.parent_id,
        "x": round(x), "y": round(y), "w": round(w), "h": round(h),
        "in": {k: round(v / EMU_PER_INCH, _R) for k, v in zip("xywh", (x, y, w, h), strict=True)},
        "rotation": s.rotation_deg,
        "size": {"w": s.size_emu[0], "h": s.size_emu[1]},
        # The element's own transform as the API reports it (group-relative
        # for grouped elements), plus the enclosing groups' matrix.
        "transform": raw_transform or {},
        "parentMatrix": list(parent_matrix),
        "fill": s.fill,
        "outline": s.outline,
        "autofit": s.autofit,
        "text": s.text or "",
        "runs": [_run(r) for r in s.runs],
    }
    if s.kind == "group":
        e["childIds"] = [c.object_id for c in s.children]
    if s.image_url:
        e["imageUrl"] = s.image_url
    return e


def _walk(raw_elements: list[dict[str, Any]], shapes: list[FlatShape],
          parent: normalize.Matrix, out: list[dict[str, Any]]) -> None:
    for raw, s in zip(raw_elements, shapes, strict=False):
        out.append(_element(s, raw.get("transform"), parent))
        if s.kind == "group":
            kids = (raw.get("elementGroup") or {}).get("children") or []
            _walk(kids, s.children, s.matrix, out)


def build(prez: dict[str, Any]) -> dict[str, Any]:
    ctx = normalize.DeckContext(prez)
    page = prez.get("pageSize") or {}
    pw = normalize._dim_emu(page.get("width")) or 9144000.0
    ph = normalize._dim_emu(page.get("height")) or 5143500.0
    slides: list[dict[str, Any]] = []
    for pos, slide in enumerate(prez.get("slides") or [], start=1):
        theme = ctx.theme_for(slide)
        shapes = normalize.normalize_page(slide, theme)
        elements: list[dict[str, Any]] = []
        _walk(slide.get("pageElements") or [], shapes, normalize.IDENTITY, elements)
        bg = ctx.background_for(slide)
        lum = normalize.luminance(bg.get("hex"))
        notes_id, notes_shape = normalize.notes_shape(slide)
        slides.append({
            "id": slide["objectId"],
            "position": pos,
            "hidden": normalize.is_hidden(slide),
            "layoutId": normalize.layout_id(slide),
            "background": {**bg, "luminance": lum},
            "isDark": lum is not None and lum < 0.4,
            "theme": theme,
            "notes": {
                "objectId": notes_id,
                "text": notes_md.to_text(notes_shape),
                "markdown": notes_md.to_markdown(notes_shape),
            },
            "elements": elements,
        })
    return {
        "id": prez.get("presentationId", ""),
        "title": prez.get("title", ""),
        "revisionId": prez.get("revisionId"),
        "pageSize": {"w": pw, "h": ph,
                     "in": {"w": round(pw / EMU_PER_INCH, _R), "h": round(ph / EMU_PER_INCH, _R)}},
        "slides": slides,
    }


def id_index(prez: dict[str, Any]) -> dict[str, str]:
    """Every object id in the deck -> the slide id it belongs to ("" for
    layouts and masters). Notes shapes map to their slide."""
    idx: dict[str, str] = {}

    def walk(el: dict[str, Any], sid: str) -> None:
        if oid := el.get("objectId"):
            idx[oid] = sid
        for c in (el.get("elementGroup") or {}).get("children") or []:
            walk(c, sid)

    for page in (prez.get("layouts") or []) + (prez.get("masters") or []):
        if oid := page.get("objectId"):
            idx[oid] = ""
        for el in page.get("pageElements") or []:
            walk(el, "")
    for slide in prez.get("slides") or []:
        sid = slide["objectId"]
        idx[sid] = sid
        for el in slide.get("pageElements") or []:
            walk(el, sid)
        notes_page = (slide.get("slideProperties") or {}).get("notesPage") or {}
        if npid := notes_page.get("objectId"):
            idx[npid] = sid
        for el in notes_page.get("pageElements") or []:
            walk(el, sid)
        if nid := (notes_page.get("notesProperties") or {}).get("speakerNotesObjectId"):
            idx[nid] = sid
    return idx
