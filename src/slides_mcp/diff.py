"""YAML DSL diff → Google Slides batchUpdate requests.

Supported:
  - Change title, subtitle, lead, paragraphs, pill/body text in columns
  - Change speaker notes (deferred emission; writer applies via notes_object_id)
  - Append/remove paragraphs in text_heavy_body
  - Append/remove columns in archetypes that allow variable length (not 3col_pill_cards
    which is fixed-length 3 — column count changes return a warning)
  - **Move icons / shapes** — top-level `elements` array carries per-shape
    {id, at: [x,y,w,h]}; position changes emit `updatePageElementTransform`
    with applyMode=RELATIVE so existing scale/rotation is preserved.

Unsupported (emits warnings):
  - Archetype swap (layout change)
  - Size changes (w/h in `at`); only translation (x, y) is applied for v1
  - Creating / deleting elements via DSL (unknown id in new, or id removed from new)
  - Color/font changes (handled separately via promote_to_theme + theme edit)
  - New slide / delete slide (handled by create_slide / delete_slide tools)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

EMU_PER_INCH = 914400


@dataclass
class DiffResult:
    requests: list[dict[str, Any]] = field(default_factory=list)
    summary: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def empty(self) -> bool:
        return not self.requests


def _replace_text_request(deck_scope_obj_id: str, old: str, new: str) -> dict[str, Any]:
    """`replaceAllText` scoped to a single pageElement (object ID).

    The Slides API's replaceAllText supports pageObjectIds for scoping but not
    pageElement IDs directly. For truly per-element text replacement we'd need to
    delete+insert. Scope here is the slide; caller must ensure uniqueness of old
    within the slide (which clean-mode DSL enforces for titles + distinct slots).
    """
    return {
        "replaceAllText": {
            "containsText": {"text": old, "matchCase": True},
            "replaceText": new,
            "pageObjectIds": [deck_scope_obj_id],
        }
    }


def _object_scoped_text_requests(
    object_id: str, old: str, new: str,
) -> list[dict[str, Any]]:
    """Two-request pair to replace a text element's body by object ID.

    Immune to the `replaceAllText` duplicate-hit problem (two slots on the same
    slide sharing the exact same string). deleteText on empty is a 400, so we
    skip it when `old` is empty.
    """
    out: list[dict[str, Any]] = []
    if old:
        out.append({
            "deleteText": {
                "objectId": object_id,
                "textRange": {"type": "ALL"},
            }
        })
    if new:
        out.append({
            "insertText": {
                "objectId": object_id,
                "text": new,
                "insertionIndex": 0,
            }
        })
    return out


def _notes_replace_requests(
    notes_object_id: str, old: str, new: str,
) -> list[dict[str, Any]]:
    """Two-request pair to replace a notes body by object ID.

    deleteText({ALL}) then insertText at index 0. If the old body is empty we
    skip the delete — deleteText on empty text is a 400.
    """
    out: list[dict[str, Any]] = []
    if old:
        out.append({
            "deleteText": {
                "objectId": notes_object_id,
                "textRange": {"type": "ALL"},
            }
        })
    if new:
        out.append({
            "insertText": {
                "objectId": notes_object_id,
                "text": new,
                "insertionIndex": 0,
            }
        })
    return out


def _diff_text_slot(
    old_val: Any, new_val: Any, slot_name: str,
    slide_id: str, result: DiffResult,
    object_id: str | None = None,
) -> None:
    """Top-level string slot comparison.

    When `object_id` is supplied, emits per-object `deleteText + insertText`
    — immune to duplicate-hit. Otherwise falls back to slide-scoped
    `replaceAllText` (caller must keep the old string unique on the slide).
    """
    if old_val == new_val:
        return
    if not isinstance(old_val, str) or not isinstance(new_val, str):
        result.warnings.append(
            f"slot '{slot_name}' type mismatch (old: {type(old_val).__name__}, "
            f"new: {type(new_val).__name__}); skipping"
        )
        return
    if object_id:
        reqs = _object_scoped_text_requests(object_id, old_val, new_val)
        if reqs:
            result.requests.extend(reqs)
            result.summary.append(f"text change: {slot_name} (object-scoped)")
        return
    if not old_val.strip():
        # Can't use replaceAllText when the target is empty; would need insert
        result.warnings.append(
            f"slot '{slot_name}' was empty; inserting into an empty slot "
            f"needs a different request shape (deleteText+insertText on a known object); "
            f"skipping for v1"
        )
        return
    result.requests.append(_replace_text_request(slide_id, old_val, new_val))
    result.summary.append(f"text change: {slot_name}")


def _diff_columns_text(
    old_cols: list[dict[str, Any]] | None,
    new_cols: list[dict[str, Any]] | None,
    slide_id: str,
    result: DiffResult,
) -> None:
    """Diff column text slots (pill, body, header, subtitle, num, etc.)."""
    old_cols = old_cols or []
    new_cols = new_cols or []
    if len(old_cols) != len(new_cols):
        result.warnings.append(
            f"column count changed ({len(old_cols)}→{len(new_cols)}); "
            f"v1 requires same column count (archetype swap handled separately)"
        )
        return
    for i, (o, n) in enumerate(zip(old_cols, new_cols, strict=True)):
        for key in set(o) | set(n):
            if key in ("pill_color_override", "card_color_override",
                       "pill_color_role", "num_color_role",
                       "status", "header_color_role", "image"):
                if o.get(key) != n.get(key):
                    result.warnings.append(
                        f"column[{i}].{key} change not supported in v1 (text-only)"
                    )
                continue
            _diff_text_slot(o.get(key, ""), n.get(key, ""), f"column[{i}].{key}",
                            slide_id, result)


def _diff_paragraphs(
    old_paras: list[str] | None,
    new_paras: list[str] | None,
    slide_id: str,
    result: DiffResult,
    paragraph_ids: list[str] | None = None,
) -> None:
    old_paras = old_paras or []
    new_paras = new_paras or []
    if len(old_paras) != len(new_paras):
        result.warnings.append(
            f"paragraph count changed ({len(old_paras)}→{len(new_paras)}); "
            f"append/remove paragraphs uses a different request shape; "
            f"only overlapping indexes diffed as text changes"
        )
    ids = paragraph_ids or []
    for i, (o, n) in enumerate(zip(old_paras, new_paras, strict=False)):
        oid = ids[i] if i < len(ids) else None
        _diff_text_slot(o, n, f"paragraph[{i}]", slide_id, result, object_id=oid)


def _elements_by_id(elements: list[dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    if not elements:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for e in elements:
        if not isinstance(e, dict):
            continue
        eid = e.get("id")
        if not eid:
            continue
        out[eid] = e
    return out


def _emu(inches: float) -> int:
    return int(round(inches * EMU_PER_INCH))


def _diff_elements(
    old_elements: list[dict[str, Any]] | None,
    new_elements: list[dict[str, Any]] | None,
    result: DiffResult,
) -> None:
    """Emit updatePageElementTransform requests for element position changes.

    Each element is a dict {id: str, at: [x, y, w, h] in inches}. Matched by id.
    Only translation (x, y) is applied; w/h changes warn (resize deferred).
    Uses applyMode=RELATIVE with a translation delta so existing scale/rotation
    on the underlying pageElement is preserved — critical for scaled icons.
    """
    old_map = _elements_by_id(old_elements)
    new_map = _elements_by_id(new_elements)

    for eid, new_el in new_map.items():
        new_at = new_el.get("at") or []
        if not isinstance(new_at, list) or len(new_at) < 2:
            continue
        old_el = old_map.get(eid)
        if old_el is None:
            result.warnings.append(
                f"element '{eid}' in new DSL has no match in current deck; "
                f"creating elements via DSL patch is not supported"
            )
            continue
        old_at = old_el.get("at") or []
        if not isinstance(old_at, list) or len(old_at) < 2:
            continue
        dx_in = float(new_at[0] or 0) - float(old_at[0] or 0)
        dy_in = float(new_at[1] or 0) - float(old_at[1] or 0)
        if len(new_at) >= 4 and len(old_at) >= 4:
            if new_at[2] != old_at[2] or new_at[3] != old_at[3]:
                result.warnings.append(
                    f"element '{eid}' size change (w/h) not supported in v1; "
                    f"only translation applied"
                )
        if abs(dx_in) < 0.001 and abs(dy_in) < 0.001:
            continue
        result.requests.append({
            "updatePageElementTransform": {
                "objectId": eid,
                "applyMode": "RELATIVE",
                "transform": {
                    "scaleX": 1, "scaleY": 1,
                    "translateX": _emu(dx_in),
                    "translateY": _emu(dy_in),
                    "unit": "EMU",
                },
            }
        })
        result.summary.append(
            f"move element {eid} by ({dx_in:+.2f},{dy_in:+.2f}) in"
        )

    for eid in old_map:
        if eid not in new_map:
            result.warnings.append(
                f"element '{eid}' missing from new DSL; deleting elements via "
                f"DSL patch is not supported (element retained)"
            )


def diff_slide(
    old: dict[str, Any], new: dict[str, Any], slide_id: str,
    notes_object_id: str | None = None,
) -> DiffResult:
    """Return batchUpdate requests that bring `old` → `new` for one slide.

    Both `old` and `new` must be DSL dicts (clean mode). `slide_id` is the
    pageObjectId in the deck. The same archetype is assumed; an archetype swap
    is a warning.

    notes_object_id: if the caller pre-resolved the notes body objectId
    (via normalize.extract_notes), notes changes are emitted as
    deleteText + insertText on that object. Omit and notes changes remain
    a warning (legacy behavior).
    """
    result = DiffResult()

    if old.get("layout") != new.get("layout"):
        result.warnings.append(
            f"archetype change ({old.get('layout')}→{new.get('layout')}) not "
            f"supported in v1; use create_slide + delete_slide instead"
        )
        return result

    # Source of truth for per-slot objectIds: the OLD DSL (server-projected).
    # Callers don't need to round-trip `_object_ids` — we always use old's map.
    obj_ids = old.get("_object_ids") or {}

    # Top-level text slots (strings only). Fall back to replaceAllText when
    # the slot has no registered objectId (legacy behavior).
    text_slots = ["title", "subtitle", "lead", "section_title", "body_paragraph"]
    for slot in text_slots:
        if slot in old or slot in new:
            _diff_text_slot(
                old.get(slot, ""), new.get(slot, ""), slot, slide_id, result,
                object_id=obj_ids.get(slot) if isinstance(obj_ids, dict) else None,
            )

    # Notes (separate pageElement — needs per-object deleteText + insertText).
    if old.get("notes") != new.get("notes"):
        old_notes = (old.get("notes") or "") or ""
        new_notes = (new.get("notes") or "") or ""
        if isinstance(old_notes, str) and isinstance(new_notes, str):
            if notes_object_id:
                reqs = _notes_replace_requests(notes_object_id, old_notes, new_notes)
                if reqs:
                    result.requests.extend(reqs)
                    result.summary.append("notes change")
            else:
                result.warnings.append(
                    "notes change detected but no notes_object_id supplied; "
                    "skipping — caller should pass notes_object_id to diff_slide"
                )
                result.summary.append("notes change (skipped)")
        else:
            result.warnings.append(
                f"notes type mismatch (old: {type(old_notes).__name__}, "
                f"new: {type(new_notes).__name__}); skipping"
            )

    # Columns
    if "columns" in old or "columns" in new:
        _diff_columns_text(old.get("columns"), new.get("columns"), slide_id, result)

    # Paragraphs (text_heavy_body)
    if "paragraphs" in old or "paragraphs" in new:
        para_ids = obj_ids.get("paragraphs") if isinstance(obj_ids, dict) else None
        _diff_paragraphs(
            old.get("paragraphs"), new.get("paragraphs"), slide_id, result,
            paragraph_ids=para_ids if isinstance(para_ids, list) else None,
        )

    # Semantic geometry/asset slots: flag any change (asset swap not supported)
    geometry_slots = ["hero", "image", "accent_panel", "footer_gradient",
                      "logos", "logo_strip", "separators", "brand_stripe"]
    for slot in geometry_slots:
        if slot in old or slot in new:
            if old.get(slot) != new.get(slot):
                result.warnings.append(
                    f"slot '{slot}' asset swap not supported; use elements[].at "
                    f"to move the underlying shape instead"
                )

    # Per-element geometry (move icons). Both old and new may carry `elements`.
    if "elements" in old or "elements" in new:
        _diff_elements(old.get("elements"), new.get("elements"), result)

    return result


def geometry_changed(old: dict[str, Any], new: dict[str, Any]) -> bool:
    """Did anything that would affect visual layout change?
    Used by the MCP tool layer to decide if an auto-thumbnail is worth it.
    """
    for slot in ("hero", "image", "accent_panel", "footer_gradient",
                 "logos", "logo_strip", "separators", "brand_stripe",
                 "layout"):
        if old.get(slot) != new.get(slot):
            return True
    # Per-element position/size changes
    old_map = _elements_by_id(old.get("elements"))
    new_map = _elements_by_id(new.get("elements"))
    if set(old_map) != set(new_map):
        return True
    for eid, new_el in new_map.items():
        if new_el.get("at") != old_map.get(eid, {}).get("at"):
            return True
    return False
