"""Project a normalized slide \u2192 token-efficient dict for the agent.

Four detail levels, each a strict superset of cost vs the one above:

  outline  \u2014 slide_id + title + archetype + counts + flags + position +
             hidden + layout_id + notes_chars                          (~30 tok)
  summary  \u2014 outline + body preview (capped) + FULL notes              (~150 tok)
  full     \u2014 title + every body string (no cap) + image refs +
             tables/charts + FULL notes                                  (~300 tok)
  raw      \u2014 full + every leaf shape with geometry + style + runs       (~600 tok)

Notes-as-content workflow: when `include_notes=True`, summary AND full emit
the FULL notes string. Drafts where the speaker-notes pane carries the actual
script would be unreadable under the v2.0.0 200-char preview cap.

Draft-state metadata: every mode emits `position` (1-indexed in deck),
`hidden` (only when True), `layout_id` (only when set), and `notes_chars`.
These let the agent reason about deck order, find skipped/draft slides, and
spot stub vs polished notes without round-trips.
"""
from __future__ import annotations

from typing import Any, Literal

from .normalize import FlatShape, flatten

Detail = Literal["outline", "summary", "full", "raw"]

# Approximate token budget per slide. Informational only.
BUDGET_TOK_PER_SLIDE: dict[Detail, int] = {
    "outline": 30,
    "summary": 150,
    "full": 300,
    "raw": 600,
}


def _run_size(s: FlatShape) -> float:
    if s.runs and s.runs[0].size_pt:
        return s.runs[0].size_pt
    return 0.0


def best_title(flat: list[FlatShape]) -> FlatShape | None:
    """Pick the largest visible text on the slide. Tiebreak: width.

    Position-agnostic \u2014 it works on covers (title bottom-half), centered
    layouts, top-banner layouts. Only signal: fontSize of the first run.
    """
    candidates = [s for s in flat if s.kind == "text" and s.text]
    if not candidates:
        return None
    return max(candidates, key=lambda s: (_run_size(s), s.w_in))


def _image_ref(s: FlatShape) -> str:
    if s.object_id:
        return f"ref://{s.object_id}"
    return "<image_asset>"


def _emit_meta(
    out: dict[str, Any],
    *,
    position: int | None,
    hidden: bool,
    layout_id: str | None,
) -> None:
    """Inject draft-state metadata into a payload dict, in-place.

    `position` is always emitted when provided (cheap; load-bearing for
    "slide N is broken"-style references). `hidden` is only emitted when
    True (no information when False; absence implies visible). `layout_id`
    is only emitted when set on the source slide.
    """
    if position is not None:
        out["position"] = position
    if hidden:
        out["hidden"] = True
    if layout_id:
        out["layout_id"] = layout_id


def _outline(
    slide_id: str, shapes: list[FlatShape], archetype: str, notes: str,
    *, position: int | None = None, hidden: bool = False, layout_id: str | None = None,
) -> dict[str, Any]:
    flat = flatten(shapes)
    title_shape = best_title(flat)
    out: dict[str, Any] = {
        "slide_id": slide_id,
        "title": title_shape.text.strip()[:120] if title_shape and title_shape.text else "",
        "archetype": archetype,
        "element_count": len(flat),
        "has_notes": bool(notes),
        "notes_chars": len(notes),
        "has_image": any(s.kind == "picture" for s in flat),
    }
    _emit_meta(out, position=position, hidden=hidden, layout_id=layout_id)
    return out


def _summary(
    slide_id: str, shapes: list[FlatShape], archetype: str, notes: str,
    *, include_images: bool = True,
    position: int | None = None, hidden: bool = False, layout_id: str | None = None,
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
    _emit_meta(out, position=position, hidden=hidden, layout_id=layout_id)
    if body_lines:
        # Raised from v2.0.0's 600-char cap to 1500. Per-line cap raised
        # 140 \u2192 200. Still distinguishable from full mode (which has no caps),
        # but actually useful for slides with substantive bodies.
        joined = " \u00b7 ".join(line[:200] for line in body_lines[:12])
        out["body"] = joined[:1500]
    if include_images:
        n_pics = sum(1 for s in flat if s.kind == "picture")
        if n_pics:
            out["image_count"] = n_pics
    if notes:
        # Notes-as-content workflow: emit FULL notes, never truncate. Drafts
        # where the speaker-notes pane carries the actual script would be
        # unreadable under the v2.0.0 200-char preview cap. User constraint
        # (LOG-031): "the stake is high \u2014 maximum verbosity please."
        out["notes"] = notes.strip()
        out["notes_chars"] = len(notes)
    return out


def _full(
    slide_id: str, shapes: list[FlatShape], archetype: str, notes: str,
    *, include_images: bool = True,
    position: int | None = None, hidden: bool = False, layout_id: str | None = None,
) -> dict[str, Any]:
    flat = flatten(shapes)
    title_shape = best_title(flat)
    out: dict[str, Any] = {
        "slide_id": slide_id,
        "archetype": archetype,
    }
    _emit_meta(out, position=position, hidden=hidden, layout_id=layout_id)
    if title_shape and title_shape.text:
        out["title"] = title_shape.text.strip()

    body: list[str] = []
    for s in flat:
        if s.kind == "text" and s.text and s is not title_shape:
            t = s.text.strip()
            if t:
                # No truncation. Full mode = full content.
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
        out["notes_chars"] = len(notes)
    return out


def _raw(
    slide_id: str, shapes: list[FlatShape], archetype: str, notes: str,
    *, position: int | None = None, hidden: bool = False, layout_id: str | None = None,
) -> dict[str, Any]:
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
    _emit_meta(out, position=position, hidden=hidden, layout_id=layout_id)
    if notes:
        out["notes"] = notes.strip()
        out["notes_chars"] = len(notes)
    return out


def project(
    slide_id: str,
    shapes: list[FlatShape],
    archetype: str,
    notes: str,
    *,
    detail: Detail = "summary",
    include_images: bool = True,
    position: int | None = None,
    hidden: bool = False,
    layout_id: str | None = None,
) -> dict[str, Any]:
    """Single dispatcher \u2014 agent-friendly entry point.

    See module docstring for detail-level semantics + token budgets.

    `position`, `hidden`, `layout_id` are draft-state metadata threaded by
    the server from the underlying slide page object. They surface in every
    detail mode \u2014 cheap to emit, load-bearing for deck-review workflows.
    """
    extras = {"position": position, "hidden": hidden, "layout_id": layout_id}
    if detail == "outline":
        return _outline(slide_id, shapes, archetype, notes, **extras)
    if detail == "summary":
        return _summary(slide_id, shapes, archetype, notes, include_images=include_images, **extras)
    if detail == "full":
        return _full(slide_id, shapes, archetype, notes, include_images=include_images, **extras)
    if detail == "raw":
        return _raw(slide_id, shapes, archetype, notes, **extras)
    raise ValueError(f"detail must be outline|summary|full|raw; got {detail!r}")
