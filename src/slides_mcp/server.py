"""FastMCP server exposing slides-mcp tools over stdio.

Start with:
  uv run slides-mcp

MCP clients should set:
  SLIDES_MCP_TOKEN_PATH=/path/to/token.json
  SLIDES_MCP_THEMES_DIR=/path/to/user/themes   (optional)

Tool surface:
  list_themes()
  list_archetypes()
  list_deck_layouts(deck_url)
  get_deck_outline(deck_url, theme="example", sub_theme="primary")
  get_slide(deck_url, slide_id, theme="example", sub_theme="primary", mode="clean")
  search_deck(deck_url, query)
  patch_slide(deck_url, slide_id, new_dsl_yaml, theme, sub_theme)
  render_thumbnail(deck_url, slide_id)
  audit_deck_colors(deck_url, theme, sub_theme)
  promote_to_theme(theme, sub_theme, role_name, kind, value)
  clone_deck(src_url, new_title)
  auth_status()
"""
from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.utilities.types import Image

from . import archetypes as archetype_reg
from . import audit as audit_mod
from . import auth, slides_api
from . import classify as classify_mod
from . import create as create_mod
from . import normalize as normalize_mod
from . import projection as projection_mod
from . import theme as theme_mod

mcp = FastMCP("slides-mcp")


def _sub_theme(theme_name: str, sub_name: str) -> theme_mod.SubTheme:
    return theme_mod.load_theme(theme_name).sub(sub_name)


@mcp.tool()
def list_themes() -> dict[str, Any]:
    """List all theme files discoverable in the search paths."""
    return {"themes": theme_mod.available_themes()}


@mcp.tool()
def list_archetypes() -> dict[str, Any]:
    """List all archetype templates bundled with or overriding the server."""
    reg = archetype_reg.registry()
    return {
        "archetypes": [
            {"name": a.name, "description": a.description,
             "required_slots": list(a.required_slots),
             "optional_slots": list(a.optional_slots)}
            for a in reg.values()
        ]
    }


@mcp.tool()
def auth_status() -> dict[str, Any]:
    """Diagnostic: report token.json state without exposing secrets."""
    return auth.credentials_info()


@mcp.tool()
def get_deck_outline(
    deck_url: str,
    theme: str = "example",
    sub_theme: str = "primary",
) -> dict[str, Any]:
    """Return a compact index of the whole deck: ~20 tokens/slide."""
    deck_id = slides_api.deck_id_from_url(deck_url)
    sub = _sub_theme(theme, sub_theme)
    prez = slides_api.get_presentation(deck_id)
    slides: list[dict[str, Any]] = []
    for slide in prez.get("slides", []):
        shapes = normalize_mod.normalize_page(slide)
        archetype = classify_mod.classify(shapes)
        title_shape = projection_mod._best_title(normalize_mod.flatten(shapes))
        entry: dict[str, Any] = {
            "slide_id": slide["objectId"],
            "title": title_shape.text.strip()[:100] if title_shape and title_shape.text else "",
        }
        if archetype != "generic_layout":
            entry["archetype"] = archetype
        slides.append(entry)
    _ = sub  # reserved for future theme-aware outline hints
    return {
        "deck_id": deck_id,
        "title": prez.get("title", ""),
        "slide_count": len(slides),
        "slides": slides,
    }


@mcp.tool()
def list_deck_layouts(deck_url: str) -> dict[str, Any]:
    """Return a breakdown of which archetypes appear in the deck, with counts."""
    outline = get_deck_outline(deck_url)
    from collections import Counter
    counts = Counter(s.get("archetype", "generic_layout") for s in outline["slides"])
    return {
        "deck_id": outline["deck_id"],
        "layouts": [
            {"archetype": a, "count": c, "slide_ids": [
                s["slide_id"] for s in outline["slides"]
                if s.get("archetype", "generic_layout") == a
            ]}
            for a, c in counts.most_common()
        ],
    }


@mcp.tool()
def get_slide(
    deck_url: str,
    slide_id: str,
    theme: str = "example",
    sub_theme: str = "primary",
    mode: str = "clean",
    include_elements: bool = False,
) -> dict[str, Any]:
    """Return one slide as compact YAML + metadata.

    mode: "clean" (default) projects through the archetype slot schema.
          "faithful" preserves raw geometry for exact round-trip.
    include_elements: opt-in geometry channel. Adds a top-level `elements`
          list of {id, at: [x, y, w, h]} — the shape-level handles patch_slide
          uses to move icons. Default False so text-only reads stay within
          the 150 tok/slide budget. Set True when the agent intends to
          reposition shapes.
    """
    if mode not in ("clean", "faithful"):
        raise ValueError(f"mode must be 'clean' or 'faithful'; got {mode!r}")
    deck_id = slides_api.deck_id_from_url(deck_url)
    sub = _sub_theme(theme, sub_theme)
    page = slides_api.get_slide(deck_id, slide_id)
    shapes = normalize_mod.normalize_page(page)
    notes = normalize_mod.extract_notes_text(page)
    archetype = classify_mod.classify(shapes)
    dsl = projection_mod.project(
        shapes, archetype, slide_id, notes, sub,
        mode=mode, include_elements=include_elements,  # type: ignore[arg-type]
    )
    return {
        "deck_id": deck_id,
        "slide_id": slide_id,
        "archetype": archetype,
        "mode": dsl.get("mode", mode),
        "dsl_yaml": yaml.safe_dump(dsl, sort_keys=False, allow_unicode=True),
    }


@mcp.tool()
def search_deck(deck_url: str, query: str) -> dict[str, Any]:
    """Find slides matching `query` in title/body text. Does not load full bodies."""
    outline = get_deck_outline(deck_url)
    q = query.lower()
    deck_id = outline["deck_id"]
    # Fetch per-slide text only (lightweight field mask is used in get_presentation)
    prez = slides_api.get_presentation(deck_id)
    hits: list[dict[str, Any]] = []
    for slide in prez.get("slides", []):
        shapes = normalize_mod.normalize_page(slide)
        for s in normalize_mod.flatten(shapes):
            if s.kind == "text" and s.text and q in s.text.lower():
                hits.append({
                    "slide_id": slide["objectId"],
                    "snippet": s.text.strip()[:200],
                })
                break
    return {"deck_id": deck_id, "query": query, "hits": hits}


@mcp.tool()
def list_slides_by(
    deck_url: str,
    archetype: str | None = None,
    contains_text: str | None = None,
) -> dict[str, Any]:
    """Structural grep over a deck. Filters (AND semantics):

    archetype: exact classify() label match (e.g. "3col_pill_cards").
    contains_text: case-insensitive substring search in any text shape.

    Single pass over the deck. Returns matching slides with their archetype,
    title (best-text extraction), and a snippet when text filtering hit.
    One call replaces the two-call `get_deck_outline` + client-side filter
    pattern for whole-deck reasoning.
    """
    deck_id = slides_api.deck_id_from_url(deck_url)
    prez = slides_api.get_presentation(deck_id)
    q = (contains_text or "").lower().strip() or None
    matches: list[dict[str, Any]] = []
    for slide in prez.get("slides", []):
        shapes = normalize_mod.normalize_page(slide)
        flat = normalize_mod.flatten(shapes)
        slide_archetype = classify_mod.classify(shapes)
        if archetype and slide_archetype != archetype:
            continue
        snippet: str | None = None
        if q is not None:
            hit = None
            for s in flat:
                if s.kind == "text" and s.text and q in s.text.lower():
                    hit = s
                    break
            if hit is None:
                continue
            snippet = hit.text.strip()[:200] if hit.text else None
        title_shape = projection_mod._best_title(flat)
        entry: dict[str, Any] = {
            "slide_id": slide["objectId"],
            "archetype": slide_archetype,
        }
        if title_shape and title_shape.text:
            entry["title"] = title_shape.text.strip()[:100]
        if snippet:
            entry["snippet"] = snippet
        matches.append(entry)
    return {
        "deck_id": deck_id,
        "filters": {"archetype": archetype, "contains_text": contains_text},
        "total": len(matches),
        "slides": matches,
    }


@mcp.tool()
def patch_slide(
    deck_url: str,
    slide_id: str,
    new_dsl_yaml: str,
    theme: str = "example",
    sub_theme: str = "primary",
    verify: str = "auto",
) -> dict[str, Any]:
    """Apply a DSL patch. Fetches current state, diffs, writes minimal batchUpdate.

    verify: "auto" (thumbnail only for geometry changes), "always", "never"
    """
    from . import diff as diff_mod

    if verify not in ("auto", "always", "never"):
        raise ValueError(f"verify must be auto|always|never; got {verify!r}")

    deck_id = slides_api.deck_id_from_url(deck_url)
    sub = _sub_theme(theme, sub_theme)

    # Fetch current. Always include elements so the diff can detect geometry
    # changes (move/resize) — the caller may or may not have sent `elements`
    # in new_dsl_yaml; if they did, we need them in old too.
    page = slides_api.get_slide(deck_id, slide_id)
    shapes = normalize_mod.normalize_page(page)
    notes, notes_object_id = normalize_mod.extract_notes(page)
    archetype = classify_mod.classify(shapes)
    old_dsl = projection_mod.project(
        shapes, archetype, slide_id, notes, sub,
        mode="clean", include_elements=True,
    )

    new_dsl = yaml.safe_load(new_dsl_yaml) or {}
    result = diff_mod.diff_slide(
        old_dsl, new_dsl, slide_id=slide_id, notes_object_id=notes_object_id,
    )

    applied = None
    if result.requests:
        applied = slides_api.batch_update(deck_id, result.requests)

    thumbnail = None
    if verify == "always" or (verify == "auto" and diff_mod.geometry_changed(old_dsl, new_dsl)):
        try:
            thumbnail = slides_api.get_thumbnail(deck_id, slide_id)
        except slides_api.SlidesApiError as e:
            result.warnings.append(f"thumbnail fetch failed: {e}")

    # Return new state (re-fetch if writes applied). Carry elements through
    # so callers see the post-write positions they asked for.
    if applied:
        page = slides_api.get_slide(deck_id, slide_id)
        shapes = normalize_mod.normalize_page(page)
        notes = normalize_mod.extract_notes_text(page)
        archetype = classify_mod.classify(shapes)
        new_state = projection_mod.project(
            shapes, archetype, slide_id, notes, sub,
            mode="clean", include_elements=True,
        )
    else:
        new_state = old_dsl

    return {
        "deck_id": deck_id,
        "slide_id": slide_id,
        "applied_request_count": len(result.requests),
        "summary": result.summary,
        "warnings": result.warnings,
        "thumbnail_url": thumbnail,
        "new_dsl_yaml": yaml.safe_dump(new_state, sort_keys=False, allow_unicode=True),
    }


@mcp.tool()
def render_thumbnail(deck_url: str, slide_id: str, size: str = "MEDIUM") -> Image:
    """Render a slide as a PNG image and return it as native MCP ImageContent.

    size: "SMALL" (200×112 at 16:9), "MEDIUM" (800×450), "LARGE" (1600×900).
    The image is fetched as bytes so the caller consumes it directly — no
    URL round-trip. URLs returned by the underlying Slides API expire ~30min,
    but bytes don't, so this is the lossless shape for a bidi agent loop.
    """
    deck_id = slides_api.deck_id_from_url(deck_url)
    png_bytes = slides_api.get_thumbnail_bytes(deck_id, slide_id, size=size)
    return Image(data=png_bytes, format="png")


@mcp.tool()
def render_thumbnail_url(deck_url: str, slide_id: str, size: str = "MEDIUM") -> dict[str, Any]:
    """Return the short-lived contentUrl for a slide thumbnail (no image bytes).

    Use this when a URL is sufficient (e.g. embedding in a report). For agent
    consumption prefer `render_thumbnail`, which returns ImageContent.
    """
    deck_id = slides_api.deck_id_from_url(deck_url)
    url = slides_api.get_thumbnail(deck_id, slide_id, size=size)
    return {"deck_id": deck_id, "slide_id": slide_id, "thumbnail_url": url, "size": size}


@mcp.tool()
def audit_deck_colors(
    deck_url: str,
    theme: str = "example",
    sub_theme: str = "primary",
) -> dict[str, Any]:
    """Walk the whole deck and report colors/fonts not in the active theme."""
    deck_id = slides_api.deck_id_from_url(deck_url)
    sub = _sub_theme(theme, sub_theme)
    prez = slides_api.get_presentation(deck_id)
    slide_shapes: list[tuple[str, list[normalize_mod.FlatShape]]] = []
    for slide in prez.get("slides", []):
        slide_shapes.append((slide["objectId"], normalize_mod.normalize_page(slide)))
    report = audit_mod.audit_deck(slide_shapes, sub, theme_name=theme)
    return {
        "deck_id": deck_id,
        "theme": theme,
        "sub_theme": sub_theme,
        "total_text_runs": report.total_text_runs,
        "total_shapes_with_fill": report.total_shapes_with_fill,
        "color_drifts": [
            {"hex": d.hex_value, "count": d.count,
             "nearest_role": d.nearest_role, "nearest_hex": d.nearest_hex,
             "example_locations": d.where[:3]}
            for d in report.color_drifts
        ],
        "font_drifts": [
            {"family": d.family, "size_pt": d.size_pt, "count": d.count,
             "example_locations": d.where[:3]}
            for d in report.font_drifts
        ],
    }


@mcp.tool()
def promote_to_theme(
    theme: str,
    sub_theme: str,
    role_name: str,
    kind: str,
    value: str,
) -> dict[str, Any]:
    """Add a drift value to the user's theme file.

    kind: "color" or "font"
    value: for color → hex string (e.g. "#1F4F9F")
           for font → JSON string like '{"family":"Inter","size_pt":18,"weight":400}'
    """
    if kind == "color":
        path = audit_mod.promote_color_to_theme(theme, sub_theme, role_name, value)
        return {"theme_file": str(path), "kind": "color", "role": role_name, "hex": value.upper()}
    if kind == "font":
        spec = json.loads(value)
        path = audit_mod.promote_font_to_theme(
            theme, sub_theme, role_name,
            family=spec["family"], size_pt=float(spec["size_pt"]),
            weight=int(spec.get("weight", 400)),
            color_role=spec.get("color_role"),
        )
        return {"theme_file": str(path), "kind": "font", "role": role_name, "spec": spec}
    raise ValueError(f"kind must be 'color' or 'font'; got {kind!r}")


_EMU_PER_INCH = 914400


def _inch_to_emu(v: float) -> int:
    return int(round(v * _EMU_PER_INCH))


def _hex_to_rgb_fracs(hex_value: str) -> dict[str, float]:
    h = hex_value.lstrip("#").upper()
    if len(h) != 6:
        raise ValueError(f"expected 6-digit hex, got {hex_value!r}")
    return {
        "red": int(h[0:2], 16) / 255,
        "green": int(h[2:4], 16) / 255,
        "blue": int(h[4:6], 16) / 255,
    }


def _new_object_id(prefix: str = "s_") -> str:
    import secrets
    return f"{prefix}{secrets.token_hex(5)}"


@mcp.tool()
def create_shape(
    deck_url: str,
    slide_id: str,
    at: list[float],
    shape_type: str = "RECTANGLE",
    text: str | None = None,
    fill_role: str | None = None,
    fill_hex: str | None = None,
    theme: str = "example",
    sub_theme: str = "primary",
) -> dict[str, Any]:
    """Insert a new shape on a slide.

    at: [left_in, top_in, width_in, height_in] in inches.
    shape_type: any valid Slides API ShapeType (RECTANGLE, ELLIPSE, ROUND_RECTANGLE,
      RIGHT_ARROW, TEXT_BOX, …).
    text: optional string inserted into the new shape.
    fill_role: palette role name (resolved via the active sub-theme) — preferred
      for theme hygiene. Falls back to fill_hex when role isn't in the palette.
    fill_hex: direct hex string (e.g. "#1F4F9F"). Used if fill_role is unset
      or unresolved.

    Returns the new object id so the caller can reference it in follow-up calls.
    Batched into a single batchUpdate: createShape, optional insertText, optional
    updateShapeProperties (fill).
    """
    if not at or len(at) < 4:
        raise ValueError("at must be [left_in, top_in, width_in, height_in]")
    left_in, top_in, w_in, h_in = (float(x) for x in at[:4])
    if w_in <= 0 or h_in <= 0:
        raise ValueError("width and height must be positive")

    deck_id = slides_api.deck_id_from_url(deck_url)
    sub = _sub_theme(theme, sub_theme)

    new_id = _new_object_id()
    requests: list[dict[str, Any]] = [
        {
            "createShape": {
                "objectId": new_id,
                "shapeType": shape_type,
                "elementProperties": {
                    "pageObjectId": slide_id,
                    "size": {
                        "width": {"magnitude": _inch_to_emu(w_in), "unit": "EMU"},
                        "height": {"magnitude": _inch_to_emu(h_in), "unit": "EMU"},
                    },
                    "transform": {
                        "scaleX": 1, "scaleY": 1,
                        "translateX": _inch_to_emu(left_in),
                        "translateY": _inch_to_emu(top_in),
                        "unit": "EMU",
                    },
                },
            }
        }
    ]

    resolved_hex = None
    if fill_role:
        resolved_hex = sub.resolve_color(fill_role)
    if not resolved_hex and fill_hex:
        resolved_hex = fill_hex
    if resolved_hex:
        requests.append({
            "updateShapeProperties": {
                "objectId": new_id,
                "fields": "shapeBackgroundFill.solidFill.color",
                "shapeProperties": {
                    "shapeBackgroundFill": {
                        "solidFill": {"color": {"rgbColor": _hex_to_rgb_fracs(resolved_hex)}}
                    }
                },
            }
        })

    if text:
        requests.append({
            "insertText": {"objectId": new_id, "text": text, "insertionIndex": 0}
        })

    slides_api.batch_update(deck_id, requests)
    return {
        "deck_id": deck_id,
        "slide_id": slide_id,
        "object_id": new_id,
        "applied_request_count": len(requests),
    }


@mcp.tool()
def create_slide(
    deck_url: str,
    archetype: str,
    content: dict[str, Any],
    insertion_index: int = -1,
    theme: str = "example",
    sub_theme: str = "primary",
    slide_id: str | None = None,
) -> dict[str, Any]:
    """Create a new slide from an archetype + semantic content.

    archetype: registered name (e.g. "3col_pill_cards", "text_heavy_body",
      "cover_with_hero"). `list_archetypes` for the full registry;
      `supported_archetypes` (the ones with a content builder) is a subset.
    content: dict keyed by slot name. Shapes per archetype:
      - text_heavy_body: {title: str, paragraphs: [str, ...]}
      - cover_with_hero: {title: str, subtitle?: str}
      - 3col_pill_cards: {title: str, lead?: str,
                          columns: [{pill, body}, ×3]}
    insertion_index: 0-indexed slide position. -1 (default) = append at end.
    slide_id: optional suggested objectId for the new slide. Auto-generated
      if omitted.

    Returns {slide_id, thumbnail_url, archetype, insertion_index,
    applied_request_count, warnings, supported_archetypes}. The caller
    follows up with `render_thumbnail(slide_id)` to consume the rendered PNG
    as native MCP `ImageContent` — this closes the VISION OUTPUT loop
    without coupling the write tool to content-block return serialization.
    """
    deck_id = slides_api.deck_id_from_url(deck_url)
    sub = _sub_theme(theme, sub_theme)

    resolved_index = insertion_index
    if resolved_index < 0:
        prez = slides_api.get_presentation(deck_id)
        resolved_index = len(prez.get("slides", []))

    new_slide_id = slide_id or _new_object_id(prefix="sl_")

    create_req: dict[str, Any] = {
        "createSlide": {
            "objectId": new_slide_id,
            "insertionIndex": resolved_index,
            "slideLayoutReference": {"predefinedLayout": "BLANK"},
        }
    }
    content_reqs, warnings = create_mod.build_slide_requests(
        new_slide_id, archetype, dict(content), sub
    )

    all_reqs = [create_req] + content_reqs
    slides_api.batch_update(deck_id, all_reqs)

    thumbnail_url = slides_api.get_thumbnail(deck_id, new_slide_id, size="MEDIUM")

    return {
        "deck_id": deck_id,
        "slide_id": new_slide_id,
        "archetype": archetype,
        "insertion_index": resolved_index,
        "applied_request_count": len(all_reqs),
        "thumbnail_url": thumbnail_url,
        "warnings": warnings,
        "supported_archetypes": create_mod.supported_archetypes(),
        "next_step_hint": f"call render_thumbnail(slide_id={new_slide_id!r}) for native ImageContent to visually verify",
    }


@mcp.tool()
def duplicate_slot(
    deck_url: str,
    slide_id: str,
    source_id: str,
    translate_in: list[float] | None = None,
) -> dict[str, Any]:
    """Duplicate an existing pageElement and (optionally) move it by a delta.

    source_id: objectId of the element to duplicate (from get_slide elements[]).
    translate_in: optional [dx, dy] in inches. Applied RELATIVE so the duplicate
      lands offset from the original by (dx, dy). Omit to keep API default
      (small default offset).

    Returns the new object id.
    """
    deck_id = slides_api.deck_id_from_url(deck_url)
    new_id = _new_object_id(prefix="d_")
    requests: list[dict[str, Any]] = [
        {
            "duplicateObject": {
                "objectId": source_id,
                "objectIds": {source_id: new_id},
            }
        }
    ]
    if translate_in and len(translate_in) >= 2:
        dx, dy = float(translate_in[0]), float(translate_in[1])
        if abs(dx) > 0.001 or abs(dy) > 0.001:
            requests.append({
                "updatePageElementTransform": {
                    "objectId": new_id,
                    "applyMode": "RELATIVE",
                    "transform": {
                        "scaleX": 1, "scaleY": 1,
                        "translateX": _inch_to_emu(dx),
                        "translateY": _inch_to_emu(dy),
                        "unit": "EMU",
                    },
                }
            })
    slides_api.batch_update(deck_id, requests)
    return {
        "deck_id": deck_id,
        "slide_id": slide_id,
        "source_id": source_id,
        "new_object_id": new_id,
        "applied_request_count": len(requests),
    }


@mcp.tool()
def clone_deck(
    src_url: str,
    new_title: str,
    replacements: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Copy a deck via Drive. Returns new deck ID + a URL hint.

    replacements: optional {find_text: replace_with} map applied to the copy
    via a single `replaceAllText` batchUpdate (matchCase=False). One-call
    template cloning — no second MCP round-trip per pair.
    """
    src_id = slides_api.deck_id_from_url(src_url)
    new_id = slides_api.copy_deck(src_id, new_title)
    applied: list[dict[str, Any]] = []
    if replacements:
        requests = [
            {
                "replaceAllText": {
                    "containsText": {"text": find, "matchCase": False},
                    "replaceText": replace,
                }
            }
            for find, replace in replacements.items()
            if find
        ]
        if requests:
            resp = slides_api.batch_update(new_id, requests)
            for reply in resp.get("replies", []) or []:
                occ = reply.get("replaceAllText", {}).get("occurrencesChanged", 0)
                applied.append({"occurrences_changed": occ})
    return {
        "src_deck_id": src_id,
        "new_deck_id": new_id,
        "new_deck_url": f"https://docs.google.com/presentation/d/{new_id}/edit",
        "replacements_applied": applied,
    }


# ------------------------------------------------------------------
# exec_batch_update — raw Slides API escape hatch
# ------------------------------------------------------------------

_DESTRUCTIVE_KINDS = frozenset({
    "deleteObject",
    "deleteSlide",
    "deleteText",
    "deleteTableRow",
    "deleteTableColumn",
    "deleteParagraphBullets",
    "replaceAllText",
})


def _scan_destructive(requests: list[dict[str, Any]]) -> list[str]:
    """Return destructive request kinds present in the list, preserving order
    of first appearance. duplicateObject / replaceAllShapesWith* are NOT
    destructive (they create; they don't remove).
    """
    found: list[str] = []
    seen: set[str] = set()
    for req in requests:
        if not isinstance(req, dict):
            continue
        for kind in req:
            if kind in _DESTRUCTIVE_KINDS and kind not in seen:
                found.append(kind)
                seen.add(kind)
    return found


def _request_kinds(requests: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for r in requests:
        if isinstance(r, dict):
            out.extend(list(r.keys()))
    return out


def _audit_log_path() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "slides-mcp" / "audit.jsonl"


def _append_audit(
    deck_id: str,
    requests: list[dict[str, Any]],
    dry_run: bool,
    applied_count: int,
    refused: bool = False,
) -> None:
    """Append one JSONL line summarizing a batchUpdate call.

    Only kinds + counts are logged — never full request bodies (PII-ish and
    unbounded). IO errors are swallowed: audit failure must not break the
    tool call.
    """
    try:
        path = _audit_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": datetime.now(UTC).isoformat(),
            "deck_id": deck_id,
            "request_count": len(requests),
            "request_kinds": _request_kinds(requests),
            "dry_run": dry_run,
            "applied": applied_count,
            "refused": refused,
        }
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass


@mcp.tool()
def exec_batch_update(
    deck_url: str,
    requests: list[dict[str, Any]],
    dry_run: bool = False,
    confirm_destructive: bool = False,
) -> dict[str, Any]:
    """Raw Google Slides batchUpdate passthrough — the escape hatch for arbitrary
    writes the bespoke tools don't cover.

    Any Request kind in the Slides API is accepted. Reference:
    https://developers.google.com/slides/api/reference/rest/v1/presentations/request

    Common recipes:
      - updateTextStyle: {objectId, style:{fontFamily,fontSize,bold,foregroundColor…}, textRange, fields}
      - updateShapeProperties: {objectId, fields, shapeProperties:{shapeBackgroundFill:{solidFill:{color:{rgbColor:{red,green,blue}}}}}}
      - updateParagraphStyle: {objectId, style:{alignment,…}, textRange, fields}
      - updatePageProperties: {objectId, pageProperties:{pageBackgroundFill:…}, fields}

    dry_run: when True, return the would-be payload (and a preview of the
      first 5 requests) without firing. Use before a large run.
    confirm_destructive: required True if the batch contains any of:
      deleteObject, deleteSlide, deleteText, deleteTableRow, deleteTableColumn,
      deleteParagraphBullets, replaceAllText. Default False refuses with a
      summary of what was detected — no writes.

    On success returns the API `replies` list as receipt. On failure, the
    Slides API halts at the bad request; the error message usually names the
    offending index and field — surfaced verbatim.

    Every call (including refused + dry_run) appends one line to
    $XDG_CONFIG_HOME/slides-mcp/audit.jsonl (or ~/.config/slides-mcp/…).
    """
    deck_id = slides_api.deck_id_from_url(deck_url)
    if not isinstance(requests, list) or not requests:
        raise ValueError("requests must be a non-empty list")

    kinds = _request_kinds(requests)
    destructive = _scan_destructive(requests)
    if destructive and not confirm_destructive:
        _append_audit(deck_id, requests, dry_run, 0, refused=True)
        return {
            "deck_id": deck_id,
            "refused": True,
            "reason": (
                "destructive request kinds present without confirm_destructive=True. "
                "Re-invoke with confirm_destructive=True to proceed."
            ),
            "destructive_kinds": destructive,
            "request_count": len(requests),
            "request_kinds": kinds,
        }

    if dry_run:
        _append_audit(deck_id, requests, True, 0)
        return {
            "deck_id": deck_id,
            "dry_run": True,
            "would_apply": len(requests),
            "request_kinds": kinds,
            "preview": requests[:5],
            "note": "dry run — no writes",
        }

    try:
        response = slides_api.batch_update(deck_id, requests)
    except slides_api.SlidesApiError as e:
        _append_audit(deck_id, requests, False, 0)
        raise RuntimeError(
            f"batchUpdate failed: {e}. Google's error message usually names the "
            f"offending request index and field — fix and retry."
        ) from e

    replies = response.get("replies", []) or []
    _append_audit(deck_id, requests, False, len(replies))
    return {
        "deck_id": deck_id,
        "applied_request_count": len(replies),
        "total_request_count": len(requests),
        "request_kinds": kinds,
        "replies": replies,
    }


def main() -> None:
    """Entry point for `slides-mcp` console script."""
    mcp.run()


if __name__ == "__main__":
    main()
