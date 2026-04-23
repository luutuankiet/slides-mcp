"""Project a slide's flat shape list + archetype name → compact YAML dict.

Two modes:
  - clean:    use archetype slot schema; drop redundant geometry; map colors to theme roles
  - faithful: preserve every element with geometry, theme-role where obvious

Clean extractors are per-archetype. If the slide's shape list doesn't match the
expected pattern for an archetype, we gracefully fall back to faithful mode.

Unimplemented clean extractors (fall through to faithful):
  - 4_col_numbered_flow
  - 4col_card_with_image
  - table_slide
  - logo_strip

These are scaffolded; extraction rules will be added as real deck data validates them.
"""
from __future__ import annotations

from typing import Any, Literal

from .normalize import FlatShape, TextRun, flatten
from .theme import SubTheme

Mode = Literal["clean", "faithful"]


def _hex_to_role(hex_value: str | None, sub: SubTheme) -> str | None:
    if not hex_value:
        return None
    return sub.role_for_hex(hex_value)


def _font_to_role(run_family: str | None, run_size: float | None, sub: SubTheme) -> str | None:
    """Best-effort match: does this font match a named font_role in the theme?"""
    if not run_family or not run_size:
        return None
    for role, spec in sub.fonts.items():
        if spec.family == run_family and abs(spec.size_pt - run_size) < 0.5:
            return role
    return None


def _run_size(s: FlatShape) -> float:
    if s.runs and s.runs[0].size_pt:
        return s.runs[0].size_pt
    return 0.0


def _compact_image_ref(s: FlatShape) -> str:
    """Short reference for an image element.

    Full Google `contentUrl` values run ~70 tok/slide and expire; for agent
    reads we keep an objectId-scoped ref instead. Callers that need bytes
    use `render_thumbnail`; callers that need the live URL use
    `render_thumbnail_url` or the faithful-mode passthrough.
    """
    if s.object_id:
        return f"ref://{s.object_id}"
    return "<image_asset>"


def _best_title(flat: list[FlatShape]) -> FlatShape | None:
    """Biggest text on the slide by font size, tiebreaker = width. Position-agnostic."""
    candidates = [s for s in flat if s.kind == "text" and s.text]
    if not candidates:
        return None
    return max(candidates, key=lambda s: (_run_size(s), s.w_in))


def _shape_to_faithful_dict(s: FlatShape, sub: SubTheme) -> dict[str, Any]:
    base: dict[str, Any] = {
        "kind": s.kind,
        "id": s.object_id,
        "at": [s.left_in, s.top_in, s.w_in, s.h_in],
    }
    if s.kind == "text" and s.text:
        base["text"] = s.text.strip()
        if s.runs:
            r = s.runs[0]
            role = _font_to_role(r.font_family, r.size_pt, sub)
            if role:
                base["font_role"] = role
            else:
                if r.font_family:
                    base["font_family"] = r.font_family
                if r.size_pt:
                    base["size_pt"] = r.size_pt
            color_role = _hex_to_role(r.color_hex, sub)
            if color_role:
                base["color_role"] = color_role
            elif r.color_hex:
                base["color_hex"] = r.color_hex
    if s.kind == "picture":
        base["asset"] = s.image_url or "<drive_file>"
    if s.kind == "shape":
        base["shape_type"] = s.shape_type
        fill_role = _hex_to_role(s.fill_hex, sub)
        if fill_role:
            base["fill_role"] = fill_role
        elif s.fill_hex:
            base["fill_hex"] = s.fill_hex
    if s.kind == "line":
        color_role = _hex_to_role(s.outline_hex, sub)
        if color_role:
            base["color_role"] = color_role
        elif s.outline_hex:
            base["color_hex"] = s.outline_hex
    if s.kind == "group" and s.children:
        base["children"] = [_shape_to_faithful_dict(c, sub) for c in s.children]
    if s.has_rotation:
        base["has_rotation"] = True
    return base


def project_faithful(
    shapes: list[FlatShape],
    slide_id: str,
    notes: str,
    sub: SubTheme,
    archetype: str | None = None,
) -> dict[str, Any]:
    """Raw geometry passthrough — preserves everything round-trip-able."""
    return {
        "id": slide_id,
        "layout": archetype or "generic_layout",
        "mode": "faithful",
        "elements": [_shape_to_faithful_dict(s, sub) for s in shapes],
        "notes": notes or None,
    }


def _project_3col_pill_cards(
    shapes: list[FlatShape], slide_id: str, notes: str, sub: SubTheme,
) -> dict[str, Any] | None:
    """Clean extractor for 3col_pill_cards. Returns None if shape doesn't fit."""
    flat = flatten(shapes)
    title_shape = _best_title(flat)
    texts = [s for s in flat if s.kind == "text" and s.text]

    # Find 3 column "pill" headers (small text frames in a row, aligned)
    big_texts = [s for s in texts if len(s.text or "") > 10 and s.top_in > 3.0]
    if not big_texts:
        return None
    # group by top — tight tolerance so pill and body rows don't merge
    rows: list[list[FlatShape]] = []
    for s in sorted(big_texts, key=lambda x: x.top_in):
        for r in rows:
            if abs(r[0].top_in - s.top_in) < 0.25:
                r.append(s)
                break
        else:
            rows.append([s])

    pill_row = None
    body_row = None
    for r in rows:
        if len(r) == 3:
            r.sort(key=lambda s: s.left_in)
            if pill_row is None:
                pill_row = r
            else:
                body_row = r
                break
    if not pill_row:
        return None

    columns: list[dict[str, Any]] = []
    for i, pill_shape in enumerate(pill_row):
        body_text = None
        if body_row and i < len(body_row):
            body_text = body_row[i].text.strip() if body_row[i].text else None
        columns.append({
            "pill": (pill_shape.text or "").strip(),
            "body": body_text or "",
        })

    # optional lead paragraph between title and columns
    lead = None
    if title_shape:
        lead_candidates = [
            s for s in texts
            if s.top_in > title_shape.top_in + 0.5
            and s.top_in < pill_row[0].top_in - 0.3
            and s.text and len(s.text) > 40
        ]
        if lead_candidates:
            lead = " ".join((s.text or "").strip() for s in lead_candidates).strip()

    out: dict[str, Any] = {
        "id": slide_id,
        "layout": "3col_pill_cards",
        "title": title_shape.text.strip() if title_shape else "",
        "columns": columns,
    }
    if lead:
        out["lead"] = lead
    if notes:
        out["notes"] = notes
    ids: dict[str, Any] = {}
    if title_shape and title_shape.object_id:
        ids["title"] = title_shape.object_id
    if ids:
        out["_object_ids"] = ids
    return out


def _project_cover_with_hero(
    shapes: list[FlatShape], slide_id: str, notes: str, sub: SubTheme,
) -> dict[str, Any] | None:
    flat = flatten(shapes)
    hero = next(
        (s for s in flat if s.kind == "picture" and s.w_in > 6 and s.h_in > 6 and s.top_in < 1.0),
        None,
    )
    if not hero:
        return None
    title_shape = _best_title(flat)
    subtitle = None
    if title_shape:
        sub_candidates = sorted(
            [
                s for s in flat
                if s.kind == "text" and s.text
                and s.top_in > title_shape.top_in + 0.3
                and s.left_in >= title_shape.left_in - 0.5
                and s != title_shape
            ],
            key=lambda s: s.top_in,
        )
        if sub_candidates:
            subtitle = (sub_candidates[0].text or "").strip()

    side = "left" if hero.left_in < 4.0 else "right"
    out: dict[str, Any] = {
        "id": slide_id,
        "layout": "cover_with_hero",
        "title": title_shape.text.strip() if title_shape else "",
        "hero": {"image": _compact_image_ref(hero), "side": side},
    }
    if subtitle:
        out["subtitle"] = subtitle
    if notes:
        out["notes"] = notes
    ids: dict[str, Any] = {}
    if title_shape and title_shape.object_id:
        ids["title"] = title_shape.object_id
    if subtitle and sub_candidates and sub_candidates[0].object_id:
        ids["subtitle"] = sub_candidates[0].object_id
    if ids:
        out["_object_ids"] = ids
    return out


def _project_text_heavy_body(
    shapes: list[FlatShape], slide_id: str, notes: str, sub: SubTheme,
) -> dict[str, Any] | None:
    flat = flatten(shapes)
    title_shape = _best_title(flat)
    body_texts = [
        s for s in flat
        if s.kind == "text" and s.text and s != title_shape and len(s.text) > 50
    ]
    body_texts.sort(key=lambda s: s.top_in)
    if not body_texts:
        return None
    out: dict[str, Any] = {
        "id": slide_id,
        "layout": "text_heavy_body",
        "title": title_shape.text.strip() if title_shape else "",
        "paragraphs": [(s.text or "").strip() for s in body_texts],
    }
    if notes:
        out["notes"] = notes
    ids: dict[str, Any] = {}
    if title_shape and title_shape.object_id:
        ids["title"] = title_shape.object_id
    para_ids = [s.object_id for s in body_texts if s.object_id]
    if len(para_ids) == len(body_texts) and para_ids:
        ids["paragraphs"] = para_ids
    if ids:
        out["_object_ids"] = ids
    return out


def _project_text_left_image_right(
    shapes: list[FlatShape], slide_id: str, notes: str, sub: SubTheme,
) -> dict[str, Any] | None:
    flat = flatten(shapes)
    pics = [s for s in flat if s.kind == "picture"]
    if not pics:
        return None
    image = max(pics, key=lambda s: s.w_in * s.h_in)
    title_shape = _best_title(flat)
    body_texts = [
        s for s in flat
        if s.kind == "text" and s.text and s != title_shape and len(s.text) > 20
    ]
    body_texts.sort(key=lambda s: s.top_in)
    out: dict[str, Any] = {
        "id": slide_id,
        "layout": "text_left_image_right",
        "title": title_shape.text.strip() if title_shape else "",
        "body_paragraph": " ".join((s.text or "").strip() for s in body_texts) or None,
        "image": _compact_image_ref(image),
    }
    if notes:
        out["notes"] = notes
    ids: dict[str, Any] = {}
    if title_shape and title_shape.object_id:
        ids["title"] = title_shape.object_id
    # body_paragraph id is only safe to track when exactly one shape contributed
    if len(body_texts) == 1 and body_texts[0].object_id:
        ids["body_paragraph"] = body_texts[0].object_id
    if ids:
        out["_object_ids"] = ids
    return out


_CLEAN_EXTRACTORS = {
    "3col_pill_cards": _project_3col_pill_cards,
    "cover_with_hero": _project_cover_with_hero,
    "text_heavy_body": _project_text_heavy_body,
    "text_left_image_right": _project_text_left_image_right,
}


def _projected_elements(shapes: list[FlatShape]) -> list[dict[str, Any]]:
    """Minimal geometry channel for every leaf element on the slide.

    Output: [{id, at: [x, y, w, h]}, ...] with coordinates in inches. Groups
    are flattened (leaves only); this is what a diff/patch caller needs to
    move a shape — the `id` matches the Slides API pageElement objectId.
    Kept opt-in so it doesn't inflate the read budget for text-only callers.
    """
    out: list[dict[str, Any]] = []
    for s in flatten(shapes):
        out.append({
            "id": s.object_id,
            "at": [s.left_in, s.top_in, s.w_in, s.h_in],
        })
    return out


def _is_default_run(r: TextRun) -> bool:
    """A run carries no styling signal (all fields at their defaults).

    Such runs are redundant — their text is already in the slot value. When a
    shape consists of exactly ONE default run, the style channel adds no
    information and can be skipped to save tokens.
    """
    return (
        r.font_family is None
        and r.size_pt is None
        and not r.bold
        and not r.italic
        and r.color_hex is None
    )


def _run_to_dict(r: TextRun) -> dict[str, Any]:
    """Emit a run as DSL: always include `text`; include style fields only when
    they carry signal. Keeps per-run overhead ~8 tokens when styled, ~4 when plain."""
    out: dict[str, Any] = {"text": r.content}
    if r.font_family:
        out["font_family"] = r.font_family
    if r.size_pt:
        out["size_pt"] = r.size_pt
    if r.bold:
        out["bold"] = True
    if r.italic:
        out["italic"] = True
    if r.color_hex:
        out["color_hex"] = r.color_hex
    return out


def _projected_styles(shapes: list[FlatShape]) -> dict[str, list[dict[str, Any]]]:
    """Build the `_styles` channel: per-shape run lists, style-bearing shapes only.

    Skipped:
      - non-text shapes
      - shapes without an object_id (we can't reference them)
      - shapes whose only run is fully-default (no useful signal)

    Returned as {object_id: [run_dict, ...]}. Agent combines with shape text
    to reason about character-range styling — pairs with `update_text_style`.
    """
    out: dict[str, list[dict[str, Any]]] = {}
    for s in flatten(shapes):
        if s.kind != "text" or not s.runs or not s.object_id:
            continue
        if len(s.runs) == 1 and _is_default_run(s.runs[0]):
            continue  # redundant; the slot value already encodes this
        out[s.object_id] = [_run_to_dict(r) for r in s.runs]
    return out


def project(
    shapes: list[FlatShape],
    archetype: str,
    slide_id: str,
    notes: str,
    sub: SubTheme,
    mode: Mode = "clean",
    include_elements: bool = False,
    include_styles: bool = False,
) -> dict[str, Any]:
    """Main entry. Returns a DSL dict ready for yaml.dump.

    include_elements: if True, appends a top-level `elements` list carrying
    `{id, at}` for every leaf shape — the geometry channel for patch_slide
    callers that want to move icons. Default False to keep the read budget
    flat for text-only callers (the common case). Faithful mode always
    carries full per-element dicts regardless of this flag.

    include_styles: if True, appends a top-level `_styles` channel mapping
    object_id → list of runs, each with {text, font_family?, size_pt?, bold?,
    italic?, color_hex?}. Use this to SEE existing character-range styling
    before calling `update_text_style`. Adds ~30 tok/slide for styled shapes;
    0 tok for shapes with uniform default styling. Default False preserves
    the 150 tok/slide text-only budget.
    """
    if mode == "faithful":
        result = project_faithful(shapes, slide_id, notes, sub, archetype=archetype)
        if include_styles:
            styles = _projected_styles(shapes)
            if styles:
                result["_styles"] = styles
        return result

    extractor = _CLEAN_EXTRACTORS.get(archetype)
    if extractor:
        result = extractor(shapes, slide_id, notes, sub)
        if result is not None:
            if include_elements:
                result["elements"] = _projected_elements(shapes)
            if include_styles:
                styles = _projected_styles(shapes)
                if styles:
                    result["_styles"] = styles
            return result

    # fall through to faithful; mark why
    faithful = project_faithful(shapes, slide_id, notes, sub, archetype=archetype)
    faithful["mode"] = "faithful"
    faithful["fallback_reason"] = (
        f"no clean extractor for archetype '{archetype}'" if extractor is None
        else f"clean extractor for '{archetype}' rejected this slide's shape"
    )
    if include_styles:
        styles = _projected_styles(shapes)
        if styles:
            faithful["_styles"] = styles
    return faithful
