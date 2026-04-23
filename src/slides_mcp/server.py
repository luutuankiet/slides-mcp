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
from . import text_range as text_range_mod
from . import theme as theme_mod
from . import theme_brief as theme_brief_mod

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
    include_styles: bool = False,
) -> dict[str, Any]:
    """Return one slide as compact YAML + metadata.

    mode: "clean" (default) projects through the archetype slot schema.
          "faithful" preserves raw geometry for exact round-trip.
    include_elements: opt-in geometry channel. Adds a top-level `elements`
          list of {id, at: [x, y, w, h]} — the shape-level handles patch_slide
          uses to move icons. Default False so text-only reads stay within
          the 150 tok/slide budget. Set True when the agent intends to
          reposition shapes.
    include_styles: opt-in character-style channel. Adds a top-level `_styles`
          map of {object_id: [runs]} where each run has {text, font_family?,
          size_pt?, bold?, italic?, color_hex?}. Shapes with uniform default
          styling are omitted (no signal). Set True when the agent intends to
          call `update_text_style` and needs to see current styling to diff
          against. Adds ~30 tok/slide on styled shapes; 0 for plain slides.
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
        include_styles=include_styles,
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
# Archetype reference deck size in EMU — see create.py for the rationale.
# Used as fallback when pageSize isn't returned in the FieldMask.
_REF_WIDTH_EMU = 16 * _EMU_PER_INCH   # 14,630,400
_REF_HEIGHT_EMU = 9 * _EMU_PER_INCH   #  8,229,600


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
def create_image(
    deck_url: str,
    slide_id: str,
    at: list[float],
    image_url: str | None = None,
    image_prompt: str | None = None,
) -> dict[str, Any]:
    """Insert an image on a slide — URL mode OR placeholder mode.

    Exactly one of `image_url` / `image_prompt` must be set. The tool's
    two modes honor Phase 1's shapes-first principle (Decision P, LOG-014):
    `create_image` is reserved for genuine raster content — photos, logos,
    diagrams, screenshots. For decoration (header bars, dividers, pills,
    cards, dots), call `create_shape` instead.

    **URL mode (`image_url` set).** Emits a single `createImage` request.
    Slides API fetches the URL server-side and embeds the bytes, so the
    URL must be reachable from Google's backend (public image, signed URL,
    etc.). Returns `mode: "image"`.

    **Placeholder mode (`image_prompt` set, `image_url` unset).** Emits
    `createShape(RECTANGLE)` + `insertText("[IMAGE: <prompt>]")`. The
    placeholder is a first-class deliverable — the agent renders the deck
    as-is (placeholder visible in thumbnails, showing the intent), and the
    user fills in the real image later (stock search, AI generation, manual
    paste). Closes the Phase 1 gap where raster assets aren't always on
    hand at slide-authoring time, without coupling the server to a stock
    API or an image-gen pipeline. Returns `mode: "placeholder"`.

    at: [left_in, top_in, width_in, height_in] in inches — same shape as
    `create_shape.at`. Position is absolute on the page; no scale inference.

    Returns `{deck_id, slide_id, object_id, mode, applied_request_count,
    thumbnail_url}`. The `thumbnail_url` is Google's short-lived contentUrl
    for the post-write slide; follow up with `render_thumbnail(slide_id)`
    for native MCP `ImageContent` — this closes the VISION OUTPUT loop.
    """
    if (image_url is None) == (image_prompt is None):
        raise ValueError(
            "exactly one of image_url / image_prompt must be set — "
            "image_url for URL mode, image_prompt for placeholder mode"
        )
    if not at or len(at) < 4:
        raise ValueError("at must be [left_in, top_in, width_in, height_in]")
    left_in, top_in, w_in, h_in = (float(x) for x in at[:4])
    if w_in <= 0 or h_in <= 0:
        raise ValueError("width and height must be positive")

    deck_id = slides_api.deck_id_from_url(deck_url)
    new_id = _new_object_id(prefix="i_")

    element_props = {
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
    }

    requests: list[dict[str, Any]]
    if image_url is not None:
        mode = "image"
        requests = [{
            "createImage": {
                "objectId": new_id,
                "url": image_url,
                "elementProperties": element_props,
            }
        }]
    else:
        mode = "placeholder"
        assert image_prompt is not None  # narrowed by the earlier XOR check
        placeholder_text = f"[IMAGE: {image_prompt}]"
        requests = [
            {
                "createShape": {
                    "objectId": new_id,
                    "shapeType": "RECTANGLE",
                    "elementProperties": element_props,
                }
            },
            {
                "insertText": {
                    "objectId": new_id,
                    "text": placeholder_text,
                    "insertionIndex": 0,
                }
            },
        ]

    slides_api.batch_update(deck_id, requests)
    thumbnail_url = slides_api.get_thumbnail(deck_id, slide_id, size="MEDIUM")

    return {
        "deck_id": deck_id,
        "slide_id": slide_id,
        "object_id": new_id,
        "mode": mode,
        "applied_request_count": len(requests),
        "thumbnail_url": thumbnail_url,
        "next_step_hint": f"call render_thumbnail(slide_id={slide_id!r}) for native ImageContent to visually verify",
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
    theme_brief: bool = True,
) -> dict[str, Any]:
    """Create a new slide from an archetype + semantic content.

    archetype: registered name (e.g. "3col_pill_cards", "text_heavy_body",
      "cover_with_hero"). `list_archetypes` for the full registry;
      `supported_archetypes` (the ones with a content builder) is a subset.
    content: dict keyed by slot name. Shapes per archetype:
      - text_heavy_body: {title: str, paragraphs: [str, ...]}
      - cover_with_hero: {title: str, subtitle?: str, title_color_hex?,
                          subtitle_color_hex?, hero?: {url|prompt, side?}}
      - 3col_pill_cards: {title, lead?, columns: [{pill, body, pill_hex?}, ×3],
                          pill_palette?: [hex, ...],   # cycled per column
                          title_accent_hex?: str}      # defaults to col1 pill
      - text_left_image_right: {title, body|paragraphs, image?,
                                accent_color_hex?, body_text_color_hex?}
      - 4col_numbered_flow: {title, columns: [{num, subtitle, body,
                             num_color_hex?}, ×4],
                             numbers_palette?, separator_color_hex?}

    theme_brief (Decision R, Phase 2): when True (default), the server reads
    the deck's hidden theme-brief meta-slide (if present) and uses it to
    fall back for unspecified visual fields. Resolution order in every
    builder:

        per_slide_content > brief.palette.* > theme YAML > safety default

    Map of brief field → builder surfaces it fills:
      - brief.palette.accent → title accent / divider colors, cover title
      - brief.palette.text → body text color, cover subtitle
      - brief.palette.category_set → pill_palette / numbers_palette defaults
      - brief.palette.surface → reserved (future: slide background fills)

    Set theme_brief=False to force pure Phase-1 behavior (theme YAML fallback
    only). Brief absence also degrades gracefully to Phase-1 behavior.

    insertion_index: 0-indexed slide position. -1 (default) = append at end.
    slide_id: optional suggested objectId for the new slide. Auto-generated
      if omitted.

    Returns {slide_id, thumbnail_url, archetype, insertion_index,
    applied_request_count, warnings, supported_archetypes, brief_applied}.
    The caller follows up with `render_thumbnail(slide_id)` to consume the
    rendered PNG as native MCP `ImageContent` — this closes the VISION OUTPUT
    loop without coupling the write tool to content-block return
    serialization.
    """
    deck_id = slides_api.deck_id_from_url(deck_url)
    sub = _sub_theme(theme, sub_theme)

    # Fetch deck metadata once: pageSize + slides (for insertion index + brief
    # scan). Reuses the outline field mask so one call carries text + geometry
    # + sizing. Fallback to 16×9 if mask misses.
    fetch_fields = (
        slides_api.DECK_OUTLINE_FIELDS + ",pageSize"
        if theme_brief
        else "pageSize,slides.objectId"
    )
    prez = slides_api.get_presentation(deck_id, fields=fetch_fields)
    page_size = prez.get("pageSize") or {}
    deck_width_in = (page_size.get("width") or {}).get("magnitude", _REF_WIDTH_EMU)
    deck_height_in = (page_size.get("height") or {}).get("magnitude", _REF_HEIGHT_EMU)
    deck_width_in = deck_width_in / _EMU_PER_INCH
    deck_height_in = deck_height_in / _EMU_PER_INCH

    # Resolve the theme brief if enabled. Absence is silent — the builder
    # treats `brief=None` as "no brief, use per-call and theme only".
    brief: dict[str, Any] | None = None
    brief_applied = False
    if theme_brief:
        meta = theme_brief_mod.find_meta_slide(prez)
        if meta is not None:
            brief = theme_brief_mod.parse_brief_body(meta["body_text"])
            brief_applied = brief is not None

    resolved_index = insertion_index
    if resolved_index < 0:
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
        new_slide_id,
        archetype,
        dict(content),
        sub,
        deck_width_in=deck_width_in,
        deck_height_in=deck_height_in,
        brief=brief,
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
        "brief_applied": brief_applied,
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
def delete_slide(
    deck_url: str,
    slide_id: str,
) -> dict[str, Any]:
    """Delete a single slide from the deck.

    Intent-explicit bespoke delete — collapses the 3-call escape-hatch
    pattern (`get_deck_outline` → `exec_batch_update(deleteObject, confirm_destructive=True)`
    → `get_deck_outline` verify) into one call. The tool name IS the
    opt-in; no separate `confirm` flag.

    Emits a single `deleteObject` batchUpdate request. Google Slides API
    refuses to delete the last remaining slide — that error propagates
    verbatim if hit. A missing slide_id likewise raises from the API.

    Returns `{deck_id, slide_id, applied_request_count: 1, status: "deleted"}`.
    Call `get_deck_outline` afterward to see the shorter deck. Other
    slide objectIds are stable — only the target is gone.
    """
    deck_id = slides_api.deck_id_from_url(deck_url)
    requests = [{"deleteObject": {"objectId": slide_id}}]
    slides_api.batch_update(deck_id, requests)
    return {
        "deck_id": deck_id,
        "slide_id": slide_id,
        "applied_request_count": 1,
        "status": "deleted",
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
# theme_brief — in-deck meta-slide carrying cross-slide visual DNA (Decision R)
# ------------------------------------------------------------------


def _fetch_for_brief(deck_id: str) -> dict[str, Any]:
    """Fetch the presentation with enough fields to locate + parse a brief.

    DECK_OUTLINE_FIELDS already includes `shape.text.textElements.textRun`
    which carries the marker + body text. Reusing it keeps the FieldMask list
    in one place.
    """
    return slides_api.get_presentation(deck_id, fields=slides_api.DECK_OUTLINE_FIELDS)


def _deck_dimensions_in(deck_id: str) -> tuple[float, float]:
    """Return deck (width, height) in inches. Falls back to 16:9 reference."""
    prez = slides_api.get_presentation(deck_id, fields="pageSize")
    page_size = prez.get("pageSize") or {}
    w_emu = (page_size.get("width") or {}).get("magnitude", _REF_WIDTH_EMU)
    h_emu = (page_size.get("height") or {}).get("magnitude", _REF_HEIGHT_EMU)
    return (w_emu / _EMU_PER_INCH, h_emu / _EMU_PER_INCH)


@mcp.tool()
def get_theme_brief(deck_url: str) -> dict[str, Any]:
    """Read the active theme brief from the deck's hidden meta-slide, if any.

    Scans the deck for a slide whose title begins with
    `__SLIDES_MCP_THEME_BRIEF__` and parses the YAML brief from its body text
    box. Returns `{brief, slide_id, marker_box_id, body_box_id}` when found or
    `{brief: None, slide_id: None, ...}` when no meta-slide exists yet.

    Agent workflow:
      1. On session start, call get_theme_brief(deck_url).
      2. If None → call extract_theme_brief (brownfield) or set_theme_brief
         (greenfield) to establish one.
      3. Pass the brief through to every subsequent create_slide call as
         fallback for unspecified content fields.
    """
    deck_id = slides_api.deck_id_from_url(deck_url)
    prez = _fetch_for_brief(deck_id)
    meta = theme_brief_mod.find_meta_slide(prez)
    if meta is None:
        return {
            "deck_id": deck_id,
            "brief": None,
            "slide_id": None,
            "marker_box_id": None,
            "body_box_id": None,
            "status": "absent",
            "next_step_hint": "call set_theme_brief(deck_url, brief) to create, or extract_theme_brief(deck_url) to propose one from the existing deck",
        }
    brief = theme_brief_mod.parse_brief_body(meta["body_text"])
    return {
        "deck_id": deck_id,
        "brief": brief,
        "slide_id": meta["slide_id"],
        "marker_box_id": meta["marker_box_id"],
        "body_box_id": meta["body_box_id"],
        "status": "ok" if brief is not None else "unparseable",
        "warnings": [] if brief is not None else [
            "meta-slide found but body text did not parse as a brief; use update_theme_brief to repair"
        ],
    }


@mcp.tool()
def set_theme_brief(
    deck_url: str,
    brief: dict[str, Any],
) -> dict[str, Any]:
    """Create or replace the deck's theme brief meta-slide.

    The brief is persisted as YAML in a hidden (`isSkipped=True`) slide at the
    end of the deck, titled `__SLIDES_MCP_THEME_BRIEF__ — DO NOT DELETE`.
    Server reads it back on every create_slide call (when Phase 2B lands) to
    resolve unspecified visual fields.

    brief: a dict. Shape:
      version: 1
      palette:
        surface:  "#0F1A4A"      # header bars, backgrounds
        accent:   "#E8612E"      # titles, dividers
        text:     "#000000"      # body text
        category_set: ["#...", "#...", "#..."]  # N-slot defaults (pills, cols)
      shape_language: "sharp" | "rounded" | "mixed"
      numbering_style: "bold" | "outlined" | "dot" | "hidden"
      tone: "clean editorial"    # free-text; informs image prompts + copy tone
      image_prompt_style: "photography, warm light"  # free-text

    If a meta-slide already exists, its body is replaced in-place (slide_id
    preserved). Otherwise a new meta-slide is appended at the end of the deck.
    Validation failures (bad hex, bad enum, wrong types) raise ValueError —
    caller should fix the brief and retry.

    Returns {deck_id, slide_id, brief, action: "created"|"updated", warnings}.
    """
    ok, errors = theme_brief_mod.validate_brief(brief)
    if not ok:
        raise ValueError("invalid theme brief: " + "; ".join(errors))

    deck_id = slides_api.deck_id_from_url(deck_url)
    prez = _fetch_for_brief(deck_id)
    meta = theme_brief_mod.find_meta_slide(prez)

    if meta is not None:
        # Update in-place. If body box is missing (corrupted state), delete
        # the slide and recreate — cheaper than salvaging.
        if meta["body_box_id"]:
            reqs = theme_brief_mod.build_update_brief_requests(
                meta["body_box_id"], brief
            )
            slides_api.batch_update(deck_id, reqs)
            return {
                "deck_id": deck_id,
                "slide_id": meta["slide_id"],
                "brief": brief,
                "action": "updated",
                "applied_request_count": len(reqs),
                "warnings": [],
            }
        # fall through to recreate
        slides_api.batch_update(
            deck_id,
            [{"deleteObject": {"objectId": meta["slide_id"]}}],
        )

    # Create fresh — append at the end of the deck.
    deck_w_in, deck_h_in = _deck_dimensions_in(deck_id)
    prez_after = slides_api.get_presentation(deck_id, fields="slides.objectId")
    insertion_index = len(prez_after.get("slides", []))
    slide_id = _new_object_id(prefix=theme_brief_mod.META_SLIDE_ID_PREFIX)
    marker_id = _new_object_id(prefix=theme_brief_mod.MARKER_BOX_ID_PREFIX)
    body_id = _new_object_id(prefix=theme_brief_mod.BODY_BOX_ID_PREFIX)

    reqs = theme_brief_mod.build_create_meta_slide_requests(
        slide_id=slide_id,
        marker_box_id=marker_id,
        body_box_id=body_id,
        brief=brief,
        deck_width_in=deck_w_in,
        deck_height_in=deck_h_in,
        insertion_index=insertion_index,
    )
    slides_api.batch_update(deck_id, reqs)
    return {
        "deck_id": deck_id,
        "slide_id": slide_id,
        "brief": brief,
        "action": "created",
        "applied_request_count": len(reqs),
        "warnings": [],
    }


@mcp.tool()
def extract_theme_brief(deck_url: str) -> dict[str, Any]:
    """Brownfield: propose a theme brief from an existing deck's dominant palette.

    Audits the deck's shapes + text (excluding any meta-slide already present)
    and returns a proposed brief with per-field rationale. Does NOT commit —
    the agent reviews the proposal with the user, tweaks as needed, then
    commits via `set_theme_brief(deck_url, brief)`.

    Heuristic:
      - `palette.surface` = dominant dark fill (header bar / background), else
        most-common chromatic fill
      - `palette.accent` = most common chromatic (non-neutral) text color, else
        most common chromatic fill
      - `palette.text` = most common dark neutral text color (body text)
      - `palette.category_set` = top 3-5 distinct chromatic fills (+accent)
      - `shape_language` = "rounded" if ROUND_RECTANGLE > 65% of shaped; "sharp"
        if < 25%; else "mixed"
      - `tone` + `image_prompt_style` = empty strings (agent fills from user intent)

    Returns `{proposed_brief, evidence, confidence, deck_id, next_step_hint}`.

    Evidence carries raw color/shape histograms so the agent can explain + iterate
    with the user before committing.

    Confidence: "high" (≥3 slides + ≥8 distinct fills), "medium" (≥2 slides + ≥4
    distinct fills), "low" otherwise. Low-confidence proposals warrant
    user discussion before committing.
    """
    deck_id = slides_api.deck_id_from_url(deck_url)
    prez = _fetch_for_brief(deck_id)
    result = theme_brief_mod.extract_brief_from_prez(prez)
    return {
        "deck_id": deck_id,
        **result,
        "next_step_hint": (
            "review proposed_brief with user, tweak as needed, then call "
            "set_theme_brief(deck_url, brief) to persist. Brief NOT yet committed."
        ),
    }


@mcp.tool()
def update_theme_brief(
    deck_url: str,
    changes: dict[str, Any],
) -> dict[str, Any]:
    """Patch the active theme brief forward-only (non-destructive).

    Deep-merges `changes` into the existing brief and writes the result back
    to the meta-slide. Existing slides are NOT repainted — only future
    create_slide calls see the amended brief. For retroactive re-styling,
    use restyle_slides (Phase 2.5, pending).

    Semantics:
      - Nested dicts merge recursively (e.g. changes={palette: {accent: X}}
        replaces only palette.accent, preserves surface/text/category_set)
      - Lists replace wholesale (no element-wise merge)
      - None drops a key from the brief

    Errors:
      - FileNotFoundError if no meta-slide exists. Call set_theme_brief first.
      - ValueError if the merged brief fails validation.

    Returns {deck_id, slide_id, brief (merged), changes_applied, warnings}.
    """
    deck_id = slides_api.deck_id_from_url(deck_url)
    prez = _fetch_for_brief(deck_id)
    meta = theme_brief_mod.find_meta_slide(prez)
    if meta is None:
        raise FileNotFoundError(
            "no theme-brief meta-slide on this deck; call set_theme_brief first"
        )
    if not meta["body_box_id"]:
        raise FileNotFoundError(
            "meta-slide found but body text box is missing; call set_theme_brief to recreate"
        )
    existing = theme_brief_mod.parse_brief_body(meta["body_text"]) or {}
    merged = theme_brief_mod.merge_brief(existing, changes)

    ok, errors = theme_brief_mod.validate_brief(merged)
    if not ok:
        raise ValueError("merged brief invalid: " + "; ".join(errors))

    reqs = theme_brief_mod.build_update_brief_requests(meta["body_box_id"], merged)
    slides_api.batch_update(deck_id, reqs)
    return {
        "deck_id": deck_id,
        "slide_id": meta["slide_id"],
        "brief": merged,
        "changes_applied": changes,
        "applied_request_count": len(reqs),
        "warnings": [],
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


# ------------------------------------------------------------------
# Typographic depth — bespoke text + paragraph styling (v0.5.0)
# ------------------------------------------------------------------


def _resolve_text_range(
    deck_id: str,
    slide_id: str,
    object_id: str,
    range_spec: dict[str, Any] | str | None,
) -> dict[str, Any]:
    """Shared range resolver for update_text_style + update_paragraph_style.

    Returns a Slides API textRange dict. 'all' / None short-circuits without
    fetching the slide. Other modes fetch the shape text and delegate to
    `text_range_mod.resolve_range` for paragraph/chars/match.
    """
    if range_spec is None or range_spec == "all":
        return {"type": "ALL"}
    page = slides_api.get_slide(deck_id, slide_id)
    try:
        text = text_range_mod.extract_shape_text(page, object_id)
    except KeyError as e:
        raise ValueError(str(e)) from e
    return text_range_mod.resolve_range(text, range_spec)


@mcp.tool()
def update_text_style(
    deck_url: str,
    slide_id: str,
    object_id: str,
    style: dict[str, Any],
    range: dict[str, Any] | str | None = None,
    verify: str = "auto",
) -> dict[str, Any]:
    """Apply character-level styling (bold, italic, color, size, font, …) to a range
    within a text-bearing shape.

    Replaces the bespoke-escape-hatch pattern of agents hand-rolling
    `updateTextStyle` requests with UTF-16 index math. Server resolves the range
    from the shape's real text; agent describes intent semantically.

    ## range language
      - None or "all"              → entire text of the shape
      - {"paragraph": N}           → Nth paragraph (0-indexed, split on "\\n")
      - {"chars": [start, end]}    → UTF-16 code-unit indices, end exclusive
      - {"match": "substring"}     → unique substring; ValueError on 0 or >1 hits

    ## style subset (hex accepted; font size in pt)
      - bool:  bold, italic, underline, strikethrough, smallCaps
      - str:   fontFamily, baselineOffset (NONE | SUPERSCRIPT | SUBSCRIPT)
      - num:   fontSize (pt)
      - hex:   foregroundColor, backgroundColor  (e.g. "#E8612E")
      - dict:  weightedFontFamily {fontFamily: str, weight: int}

    ## verify
      "auto" (default) / "always"  → include thumbnail_url in response
      "never"                       → skip the thumbnail fetch

    ## Example — emphasize the first phrase of a body shape
      update_text_style(
          deck_url, slide_id, object_id=body_box_id,
          range={"match": "The problem"},
          style={"bold": True, "fontSize": 28, "foregroundColor": "#E8612E"},
      )

    Returns {deck_id, slide_id, object_id, range_resolved, fields,
             applied_request_count, thumbnail_url?}.
    """
    if not object_id:
        raise ValueError("object_id required")

    deck_id = slides_api.deck_id_from_url(deck_url)
    text_range = _resolve_text_range(deck_id, slide_id, object_id, range)
    api_style, fields = text_range_mod.normalize_text_style(style)

    req = {
        "updateTextStyle": {
            "objectId": object_id,
            "style": api_style,
            "textRange": text_range,
            "fields": ",".join(fields),
        }
    }
    slides_api.batch_update(deck_id, [req])

    result: dict[str, Any] = {
        "deck_id": deck_id,
        "slide_id": slide_id,
        "object_id": object_id,
        "range_resolved": text_range,
        "fields": fields,
        "applied_request_count": 1,
    }
    if verify in ("auto", "always"):
        result["thumbnail_url"] = slides_api.get_thumbnail(
            deck_id, slide_id, size="MEDIUM"
        )
    return result


# ------------------------------------------------------------------
# Variant selection (v0.5.0 — B1/B2/B3): propose → generate → lock
# ------------------------------------------------------------------


@mcp.tool()
def propose_brief_variants(
    intent: str,
    n: int = 3,
) -> dict[str, Any]:
    """Propose N distinct-mood theme-brief variants from a natural-language intent.

    Pure function (no deck access) — returns briefs ready to pass into
    `generate_variants` or `set_theme_brief`. Seeds the variant selection
    workflow: agent calls this to get 2-5 moods, renders each via
    `generate_variants`, user (or agent) picks the winner, agent calls
    `lock_variant` to commit.

    intent: free-text describing the presentation's context, audience, tone,
      subject matter. Keyword matching is case-insensitive substring: mention
      'enterprise' or 'b2b' to bias toward confident-enterprise moods; 'tech'
      or 'data' toward minimalist-technical; 'warm' or 'human' toward organic
      earth-tones; 'bold' or 'creative' toward high-contrast magazine; 'elegant'
      or 'luxury' toward refined serif. With no matching keywords, the default
      ordering (editorial, enterprise, tech) wins.

    n: number of variants to return. Capped at the pool size (currently 6).
      Defaults to 3 — the sweet spot for visual A/B/C selection.

    Returns {variants: [brief, ...]}. Each brief is a fully-formed dict
    suitable for set_theme_brief (palette, shape_language, numbering_style,
    tone, image_prompt_style). No two returned briefs share a palette.accent
    (distinctness invariant).
    """
    briefs = theme_brief_mod.propose_brief_variants(intent=intent, n=n)
    return {"variants": briefs, "count": len(briefs), "intent": intent}


@mcp.tool()
def generate_variants(
    deck_url: str,
    content_list: list[dict[str, Any]],
    briefs: list[dict[str, Any]],
    variant_prefix: str = "v",
) -> dict[str, Any]:
    """Render N variants of the same content, each under a different theme brief.

    The middle step of the variant selection workflow:

        propose_brief_variants(intent, n=3)
            → generate_variants(deck, content_list, briefs)   ← you are here
            → render_thumbnail(each slide_id) to compare visually
            → lock_variant(variant_id, manifest) to commit winner + delete losers

    For each brief in `briefs`, this tool:
      1. Calls set_theme_brief to swap the deck's meta-slide brief to `briefs[i]`
      2. Creates every slide in `content_list` under that brief. Slide IDs are
         `{variant_prefix}{i}_{suffix}` — e.g. `v0_cover`, `v1_cover`, `v2_cover`
         for three variants of a cover.
      3. Collects the slide_ids into a per-variant manifest.

    content_list: list of slide specs. Each item:
      {
        "archetype": str,   # required — registered archetype name
        "content": dict,    # required — slot-keyed content (see create_slide)
        "slide_id": str?    # optional suffix. default "s0", "s1", ...
      }

    briefs: list of theme-brief dicts (from propose_brief_variants or hand-built).
      Each must pass validate_brief — invalid briefs raise before any slide is
      created.

    variant_prefix: prefix for each variant's slide_ids. Default "v".

    Returns a manifest:
      {
        "deck_id": str,
        "variants": [
          {"variant_id": "v0", "brief": {...}, "slide_ids": [...]},
          ...
        ],
        "variant_prefix": str,
        "total_slides_created": int,
      }

    Pass the WHOLE manifest into `lock_variant` when picking the winner.
    The deck's meta-slide brief ends the loop at `briefs[-1]` (pre-lock state).

    ## Failure mode (partial writes)
    If a create_slide call mid-loop fails, the error propagates and the deck
    is left with the slides created so far + whichever brief was last set.
    Clean up by manually deleting stray slides OR by completing the loop
    against a simpler content_list and then calling lock_variant to prune.
    """
    if not isinstance(content_list, list) or not content_list:
        raise ValueError("content_list must be a non-empty list")
    if not isinstance(briefs, list) or not briefs:
        raise ValueError("briefs must be a non-empty list")

    # Validate all briefs UP-FRONT so we fail before any write.
    for i, brief in enumerate(briefs):
        ok, errors = theme_brief_mod.validate_brief(brief)
        if not ok:
            raise ValueError(f"briefs[{i}] invalid: {'; '.join(errors)}")

    # Validate content_list shape up-front.
    for j, item in enumerate(content_list):
        if not isinstance(item, dict) or "archetype" not in item or "content" not in item:
            raise ValueError(
                f"content_list[{j}] must be a dict with 'archetype' and 'content' keys"
            )

    deck_id = slides_api.deck_id_from_url(deck_url)
    variants: list[dict[str, Any]] = []
    total_slides = 0

    for i, brief in enumerate(briefs):
        variant_id = f"{variant_prefix}{i}"
        set_theme_brief(deck_url, brief)

        slide_ids: list[str] = []
        for j, item in enumerate(content_list):
            suffix = item.get("slide_id") or f"s{j}"
            full_slide_id = f"{variant_id}_{suffix}"
            result = create_slide(
                deck_url=deck_url,
                archetype=item["archetype"],
                content=item["content"],
                slide_id=full_slide_id,
                theme_brief=True,
            )
            slide_ids.append(result["slide_id"])
            total_slides += 1

        variants.append({
            "variant_id": variant_id,
            "brief": brief,
            "slide_ids": slide_ids,
        })

    return {
        "deck_id": deck_id,
        "variants": variants,
        "variant_prefix": variant_prefix,
        "total_slides_created": total_slides,
    }


@mcp.tool()
def lock_variant(
    deck_url: str,
    variant_id: str,
    variants_manifest: dict[str, Any],
) -> dict[str, Any]:
    """Commit one variant as the deck's theme; delete the losing variants' slides.

    Terminal step of the generate-pick-lock workflow. Pass the manifest
    returned by `generate_variants` + the variant_id you picked (e.g. after
    the user viewed three rendered thumbnails).

    Side effects (in order):
      1. `set_theme_brief(deck_url, winner.brief)` — promotes winner's brief
         into the deck's meta-slide (overwriting whatever the loop left).
      2. `delete_slide` on every slide_id in every LOSING variant.

    variants_manifest: the dict from generate_variants (must contain a
      `variants` list with `variant_id`, `brief`, `slide_ids` per entry).
    variant_id: the winning variant's id (e.g. "v1").

    Returns {
      deck_id, locked_variant_id, locked_brief,
      kept_slide_ids, deleted_slide_count, deleted_slide_ids, warnings
    }.
    """
    variants = variants_manifest.get("variants")
    if not isinstance(variants, list) or not variants:
        raise ValueError(
            "variants_manifest.variants must be a non-empty list "
            "(pass the output of generate_variants)"
        )

    winner = None
    losers: list[dict[str, Any]] = []
    for v in variants:
        if v.get("variant_id") == variant_id:
            winner = v
        else:
            losers.append(v)
    if winner is None:
        available = [v.get("variant_id") for v in variants]
        raise ValueError(
            f"variant_id {variant_id!r} not found in manifest; available: {available}"
        )
    if "brief" not in winner:
        raise ValueError(f"winner variant {variant_id!r} has no 'brief' in manifest")

    # 1. Promote winner's brief.
    set_theme_brief(deck_url, winner["brief"])

    # 2. Delete losers' slides. Continue past individual failures but report them.
    deleted_slide_ids: list[str] = []
    warnings: list[str] = []
    for loser in losers:
        for sid in loser.get("slide_ids") or []:
            try:
                delete_slide(deck_url=deck_url, slide_id=sid)
                deleted_slide_ids.append(sid)
            except Exception as e:  # noqa: BLE001 — report, don't abort
                warnings.append(f"delete_slide({sid!r}) failed: {e}")

    deck_id = slides_api.deck_id_from_url(deck_url)
    return {
        "deck_id": deck_id,
        "locked_variant_id": variant_id,
        "locked_brief": winner["brief"],
        "kept_slide_ids": list(winner.get("slide_ids") or []),
        "deleted_slide_count": len(deleted_slide_ids),
        "deleted_slide_ids": deleted_slide_ids,
        "warnings": warnings,
    }


@mcp.tool()
def update_paragraph_style(
    deck_url: str,
    slide_id: str,
    object_id: str,
    style: dict[str, Any],
    range: dict[str, Any] | str | None = None,
    verify: str = "auto",
) -> dict[str, Any]:
    """Apply paragraph-level styling (alignment, indent, line spacing, space above/below)
    to paragraph(s) intersecting a range in a text-bearing shape.

    Slides API applies paragraph style to every paragraph that overlaps the
    range. In practice: pick whatever range is easiest (all / paragraph / match);
    the style lands on the paragraph(s) the range touches.

    ## range language — same as update_text_style
      - None or "all"              → all paragraphs in the shape
      - {"paragraph": N}           → Nth visible paragraph (blank separators skipped)
      - {"chars": [start, end]}    → paragraphs overlapping UTF-16 range
      - {"match": "substring"}     → paragraph containing the unique substring

    ## style subset
      - str enum: alignment (START|CENTER|END|JUSTIFIED),
                  direction (LEFT_TO_RIGHT|RIGHT_TO_LEFT),
                  spacingMode (NEVER_COLLAPSE|COLLAPSE_LISTS)
      - pt number: indentStart, indentEnd, indentFirstLine, spaceAbove, spaceBelow
      - % number:  lineSpacing (100 = single, 150 = 1.5×, 200 = double)

    ## verify
      "auto" (default) / "always"  → include thumbnail_url
      "never"                       → skip thumbnail fetch

    ## Example — center a quote paragraph with generous spacing
      update_paragraph_style(
          deck_url, slide_id, object_id=body_id,
          range={"paragraph": 0},
          style={"alignment": "CENTER", "lineSpacing": 150, "spaceAbove": 12},
      )

    Returns {deck_id, slide_id, object_id, range_resolved, fields,
             applied_request_count, thumbnail_url?}.
    """
    if not object_id:
        raise ValueError("object_id required")

    deck_id = slides_api.deck_id_from_url(deck_url)
    text_range = _resolve_text_range(deck_id, slide_id, object_id, range)
    api_style, fields = text_range_mod.normalize_paragraph_style(style)

    req = {
        "updateParagraphStyle": {
            "objectId": object_id,
            "style": api_style,
            "textRange": text_range,
            "fields": ",".join(fields),
        }
    }
    slides_api.batch_update(deck_id, [req])

    result: dict[str, Any] = {
        "deck_id": deck_id,
        "slide_id": slide_id,
        "object_id": object_id,
        "range_resolved": text_range,
        "fields": fields,
        "applied_request_count": 1,
    }
    if verify in ("auto", "always"):
        result["thumbnail_url"] = slides_api.get_thumbnail(
            deck_id, slide_id, size="MEDIUM"
        )
    return result


def main() -> None:
    """Entry point for `slides-mcp` console script."""
    mcp.run()


if __name__ == "__main__":
    main()
