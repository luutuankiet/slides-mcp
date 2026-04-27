"""Project a normalized slide → token-efficient dict for the agent.

Four detail levels, each a strict superset of cost vs the one above:

  outline  — slide_id + title + archetype + element_count + flags    (~20 tok)
  summary  — outline + joined body text + notes preview               (~80 tok)
  full     — title + every body string + image refs + tables/charts +
             full notes                                               (~150 tok)
  raw      — every shape with geometry + style + runs (debug; faithful  ~400 tok)

The goal is to mirror `read_files` philosophy: pick the cheapest mode that
answers your question, and keep the per-slide schema obvious enough that the
agent doesn't need a second tool call to interpret the response.
"""
from __future__ import annotations

from typing import Any, Literal

from .normalize import FlatShape, flatten

Detail = Literal["outline", "summary", "full", "raw"]

# Approximate token budget per slide for each detail level. Informational
# only — the projection doesn't enforce these. Use for capacity planning.
BUDGET_TOK_PER_SLIDE: dict[Detail, int] = {
    "outline": 20,
    "summary": 80,
    "full": 150,
    "raw": 400,
}


def _run_size(s: FlatShape) -> float:
    if s.runs and s.runs[0].size_pt:
        return s.runs[0].size_pt
    return 0.0


def best_title(flat: list[FlatShape]) -> FlatShape | None:
    """Pick the largest visible text on the slide. Tiebreak: width.

    Position-agnostic — it works on covers (title bottom-half), centered
    layouts, top-banner layouts. The only signal it relies on is the
    fontSize of the first text run.
    """
    candidates = [s for s in flat if s.kind == "text" and s.text]
    if not candidates:
        return None
    return max(candidates, key=lambda s: (_run_size(s), s.w_in))


def _image_ref(s: FlatShape) -> str:
    """Compact reference for an image element — see render_thumbnail to view."""
    if s.object_id:
        return f"ref://{s.object_id}"
    return "<image_asset>"


def _outline(slide_id: str, shapes: list[FlatShape], archetype: str, notes: str) -> dict[str, Any]:
    flat = flatten(shapes)
    title_shape = best_title(flat)
    return {
        "slide_id": slide_id,
        "title": title_shape.text.strip()[:120] if title_shape and title_shape.text else "",
        "archetype": archetype,
        "element_count": len(flat),
        "has_notes": bool(notes),
        "has_image": any(s.kind == "picture" for s in flat),
    }


def _summary(
    slide_id: str,
    shapes: list[FlatShape],
    archetype: str,
    notes: str,
    *,
    include_images: bool = True,
) -> dict[str, Any]:
    flat = flatten(shapes)
    title_shape = best_title(flat)
    body_lines = [
        (s.text or "").strip()
        for s in flat
        if s.kind == "text" and s.text and s is not title_shape and len((s.text or "").strip()) >= 4
    ]
    out: dict[str, Any] = {
        "slide_id": slide_id,
        "title": title_shape.text.strip()[:200] if title_shape and title_shape.text else "",
        "archetype": archetype,
    }
    # Cap body — joined preview, not every word
    if body_lines:
        joined = " · ".join(line[:140] for line in body_lines[:8])
        out["body"] = joined[:600]
    if include_images:
        n_pics = sum(1 for s in flat if s.kind == "picture")
        if n_pics:
            out["image_count"] = n_pics
    if notes:
        out["notes_preview"] = notes.strip()[:200]
    return out


def _full(
    slide_id: str,
    shapes: list[FlatShape],
    archetype: str,
    notes: str,
    *,
    include_images: bool = True,
) -> dict[str, Any]:
    flat = flatten(shapes)
    title_shape = best_title(flat)
    out: dict[str, Any] = {
        "slide_id": slide_id,
        "archetype": archetype,
    }
    if title_shape and title_shape.text:
        out["title"] = title_shape.text.strip()

    body: list[str] = []
    for s in flat:
        if s.kind == "text" and s.text and s is not title_shape:
            t = s.text.strip()
            if t:
                body.append(t)
    if body:
        out["body"] = body

    if include_images:
        images = [_image_ref(s) for s in flat if s.kind == "picture"]
        if images:
            out["images"] = images

    table_count = sum(1 for s in flat if s.kind == "table")
    chart_count = sum(1 for s in flat if s.kind == "chart")
    if table_count:
        out["tables"] = table_count
    if chart_count:
        out["charts"] = chart_count

    if notes:
        out["notes"] = notes.strip()
    return out


def _raw(slide_id: str, shapes: list[FlatShape], archetype: str, notes: str) -> dict[str, Any]:
    """Faithful: every leaf shape with geometry + style + runs. Token-heavy."""
    elements: list[dict[str, Any]] = []
    for s in flatten(shapes):
        e: dict[str, Any] = {
            "id": s.object_id,
            "kind": s.kind,
            "at": [s.left_in, s.top_in, s.w_in, s.h_in],
        }
        if s.text:
            e["text"] = s.text.strip()
        if s.shape_type:
            e["shape_type"] = s.shape_type
        if s.fill_hex:
            e["fill_hex"] = s.fill_hex
        if s.outline_hex:
            e["outline_hex"] = s.outline_hex
        if s.image_url:
            e["image_url"] = s.image_url
        if s.runs:
            runs_info: list[dict[str, Any]] = []
            for r in s.runs:
                ri: dict[str, Any] = {"text": r.content}
                if r.font_family:
                    ri["font_family"] = r.font_family
                if r.size_pt:
                    ri["size_pt"] = r.size_pt
                if r.bold:
                    ri["bold"] = True
                if r.italic:
                    ri["italic"] = True
                if r.color_hex:
                    ri["color_hex"] = r.color_hex
                runs_info.append(ri)
            e["runs"] = runs_info
        if s.has_rotation:
            e["has_rotation"] = True
        elements.append(e)

    out: dict[str, Any] = {
        "slide_id": slide_id,
        "archetype": archetype,
        "elements": elements,
    }
    if notes:
        out["notes"] = notes.strip()
    return out


def project(
    slide_id: str,
    shapes: list[FlatShape],
    archetype: str,
    notes: str,
    *,
    detail: Detail = "summary",
    include_images: bool = True,
) -> dict[str, Any]:
    """Single dispatcher — agent-friendly entry point.

    See module docstring for detail-level semantics + token budgets.
    """
    if detail == "outline":
        return _outline(slide_id, shapes, archetype, notes)
    if detail == "summary":
        return _summary(slide_id, shapes, archetype, notes, include_images=include_images)
    if detail == "full":
        return _full(slide_id, shapes, archetype, notes, include_images=include_images)
    if detail == "raw":
        return _raw(slide_id, shapes, archetype, notes)
    raise ValueError(f"detail must be outline|summary|full|raw; got {detail!r}")
