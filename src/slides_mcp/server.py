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
from . import icons as icons_mod
from . import normalize as normalize_mod
from . import projection as projection_mod
from . import swatch as swatch_mod
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
def audit_typography(
    deck_url: str,
    theme: str = "example",
    sub_theme: str = "primary",
) -> dict[str, Any]:
    """Brownfield typography audit — complement to audit_deck_colors.

    Walks every text run in the deck and reports:
      - **dominant font + outliers**: most-common font_family vs the long tail
        of families that might be paste-in Calibri pollution or ad-hoc drift
      - **size clusters**: every font size bucketed to 0.5pt, labeled by
        theme font role; sizes used by <5% of runs are tagged as "orphan"
      - **orphan bolds**: bold runs inside shapes where the majority is
        non-bold (often accidental paste-styling)
      - **color drift vs brief**: any run color more than 60 RGB-distance
        away from every brief.palette role; brief is auto-fetched from the
        deck's hidden meta-slide (skipped silently if absent)

    Companion to `audit_deck_colors` — together they produce the full pre-
    restyle picture. Feeds directly into `restyle_slides(brief_overrides=...)`
    which picks up the drift targets.

    Returns `{deck_id, theme, sub_theme, brief_applied, total_text_runs,
    total_text_shapes, dominant_font, font_outliers, size_clusters,
    orphan_bolds, color_drifts_vs_brief}`. Each drift list entry carries
    `example_locations` (first 3 `slide_id/object_id:textN` breadcrumbs)
    for targeted follow-up.
    """
    deck_id = slides_api.deck_id_from_url(deck_url)
    sub = _sub_theme(theme, sub_theme)
    prez = slides_api.get_presentation(deck_id)
    slide_shapes: list[tuple[str, list[normalize_mod.FlatShape]]] = []
    for slide in prez.get("slides", []):
        slide_shapes.append((slide["objectId"], normalize_mod.normalize_page(slide)))

    # best-effort brief fetch: missing brief is fine (skips color_drifts_vs_brief)
    brief: dict[str, Any] | None = None
    try:
        meta = theme_brief_mod.find_meta_slide(prez)
        if meta is not None:
            brief = theme_brief_mod.parse_brief_body(meta["body_text"])
    except Exception:  # noqa: BLE001 — brief extraction is best-effort
        brief = None

    report = audit_mod.audit_typography(slide_shapes, sub, brief=brief, theme_name=theme)
    return {
        "deck_id": deck_id,
        "theme": theme,
        "sub_theme": sub_theme,
        "brief_applied": report.brief_applied,
        "total_text_runs": report.total_text_runs,
        "total_text_shapes": report.total_text_shapes,
        "dominant_font": report.dominant_font,
        "font_outliers": [
            {"family": d.family, "size_pt": d.size_pt, "count": d.count,
             "example_locations": d.where[:3]}
            for d in report.font_outliers
        ],
        "size_clusters": [
            {"size_pt": c.size_pt, "count": c.count, "role_guess": c.role_guess}
            for c in report.size_clusters
        ],
        "orphan_bolds": [
            {"slide_id": o.slide_id, "object_id": o.object_id,
             "run_index": o.run_index, "text_preview": o.text_preview}
            for o in report.orphan_bolds[:20]  # cap to keep response small
        ],
        "color_drifts_vs_brief": [
            {"hex": d.hex_value, "count": d.count,
             "nearest_brief_role": d.nearest_role, "nearest_brief_hex": d.nearest_hex,
             "example_locations": d.where[:3]}
            for d in report.color_drifts_vs_brief
        ],
    }


# Threshold (RGB-sum distance) above which a color is considered drift and
# will be rewritten by restyle_slides. Same value used by audit_typography so
# "what the audit reports" == "what restyle will rewrite".
_RESTYLE_DRIFT_THRESHOLD: int = 60


def _should_rewrite_run_color(run_hex: str, brief_hexes: dict[str, str]) -> tuple[bool, str | None]:
    """Return (should_rewrite, target_hex). Skips near-black + near-white."""
    dist_black = audit_mod._color_distance(run_hex, "#000000")
    dist_white = audit_mod._color_distance(run_hex, "#FFFFFF")
    if dist_black < _RESTYLE_DRIFT_THRESHOLD:
        return False, None  # body text — leave
    if dist_white < _RESTYLE_DRIFT_THRESHOLD:
        return False, None  # inverted title — leave
    role, target_hex, d = audit_mod._nearest_brief_role(run_hex, brief_hexes)
    if not target_hex or target_hex.upper() == run_hex.upper():
        return False, None
    if d <= _RESTYLE_DRIFT_THRESHOLD:
        return False, None
    return True, target_hex


@mcp.tool()
def restyle_slides(
    deck_url: str,
    slide_ids: list[str] | str = "all",
    brief_overrides: dict[str, Any] | None = None,
    confirm_destructive: bool = False,
    theme: str = "example",
    sub_theme: str = "primary",
    verify: str = "auto",
    normalize_fonts: bool = False,
) -> dict[str, Any]:
    """Retroactively repaint slides to match the deck's theme brief.

    normalize_fonts: when True, also rewrite text run fontFamily to match
        brief.font_family.heading (for runs >= 24pt) and brief.font_family.body
        (for runs < 24pt). Defaults False for backward-compat. Requires
        brief.font_family on at least one axis or this flag is a no-op.
        Brownfield font repaint parity with the palette repaint.

    Walks every selected slide, every leaf shape, every text run, and emits
    `updateShapeProperties` + `updateTextStyle` requests for every color that
    drifts more than ~60 RGB-sum distance from the nearest brief palette role.
    Near-black (body text) + near-white (inverted titles) are preserved — they
    express hierarchy, not identity.

    **DESTRUCTIVE.** This overwrites per-call hex that may have been set at
    `create_slide` time. `confirm_destructive=True` is required. One audit
    log entry (`restyle_slides`) per invocation.

    slide_ids: a list of slide IDs to restyle, OR the literal "all" (default)
        to apply to every non-meta slide. Unknown IDs are skipped with a
        warning rather than raising.

    brief_overrides: optional dict deep-merged on top of the deck's committed
        brief before rewriting. Useful for "try this palette without committing"
        workflows. Does NOT update the meta-slide — call `update_theme_brief`
        separately to persist.

    Returns `{deck_id, restyled_slide_ids, skipped_slide_ids, total_rewrites,
    per_slide, applied_request_count, warnings, thumbnails}`. `per_slide` is
    `{slide_id: {fill_rewrites, text_rewrites, thumbnail_url}}` — agent uses
    thumbnails to visually verify one voice before committing the brief
    amendment (if any) via update_theme_brief.
    """
    if not confirm_destructive:
        raise ValueError(
            "restyle_slides rewrites fills + text colors to match the deck brief. "
            "This overwrites per-call hex passed at create_slide time. Re-invoke "
            "with confirm_destructive=True to proceed."
        )

    deck_id = slides_api.deck_id_from_url(deck_url)
    prez = _fetch_for_brief(deck_id)
    meta = theme_brief_mod.find_meta_slide(prez)
    brief = theme_brief_mod.parse_brief_body(meta["body_text"]) if meta else None
    if brief is None:
        if not brief_overrides:
            raise ValueError(
                "deck has no theme brief and no brief_overrides were passed. "
                "Call set_theme_brief(deck_url, brief) first, or pass a minimal "
                "brief_overrides dict with a palette."
            )
        brief = brief_overrides
    elif brief_overrides:
        brief = theme_brief_mod.merge_brief(brief, brief_overrides)

    brief_hexes = audit_mod._brief_palette_hexes(brief)
    if not brief_hexes:
        raise ValueError(
            "resolved brief has no usable palette — add palette.accent / palette.text / "
            "palette.category_set before restyling"
        )

    # Filter meta-slide out of targets (never repaint the brief meta-slide).
    meta_slide_id = meta["slide_id"] if meta else None
    all_non_meta = [
        s["objectId"] for s in prez.get("slides", [])
        if s["objectId"] != meta_slide_id
    ]
    if slide_ids == "all":
        targets = list(all_non_meta)
    else:
        targets = [sid for sid in slide_ids if sid in all_non_meta]

    slides_by_id = {s["objectId"]: s for s in prez.get("slides", [])}
    warnings: list[str] = []
    if slide_ids != "all":
        for sid in slide_ids:
            if sid == meta_slide_id:
                warnings.append(f"skipped meta-slide '{sid}' (reserved)")
            elif sid not in slides_by_id:
                warnings.append(f"unknown slide_id '{sid}' — skipped")

    all_requests: list[dict[str, Any]] = []
    per_slide: dict[str, dict[str, Any]] = {}
    skipped: list[str] = []

    for sid in targets:
        # Per-slide full-field fetch — outline-mask fetch can miss
        # shapeBackgroundFill on text-containing ROUND_RECTANGLEs (pill shapes),
        # which caused a live-deck under-detect bug during v0.6.0 smoke test.
        try:
            slide = slides_api.get_slide(deck_id, sid)
        except Exception:  # noqa: BLE001
            skipped.append(sid)
            continue
        shapes = normalize_mod.normalize_page(slide)
        fill_rewrites = 0
        text_rewrites = 0

        for fs in normalize_mod.flatten(shapes):
            if not fs.object_id:
                continue

            # --- shape fill rewrite ---
            if fs.fill_hex:
                current = fs.fill_hex.upper()
                role, target, d = audit_mod._nearest_brief_role(current, brief_hexes)
                if target and target.upper() != current and d > _RESTYLE_DRIFT_THRESHOLD:
                    all_requests.append({
                        "updateShapeProperties": {
                            "objectId": fs.object_id,
                            "fields": "shapeBackgroundFill.solidFill.color",
                            "shapeProperties": {
                                "shapeBackgroundFill": {
                                    "solidFill": {
                                        "color": {"rgbColor": _hex_to_rgb_fracs(target)}
                                    }
                                }
                            },
                        }
                    })
                    fill_rewrites += 1

            # --- text run color rewrite (shape-scoped, range=ALL) ---
            # Strategy: find dominant chromatic color across this shape's runs;
            # if it drifts, rewrite the entire shape's text (range ALL). This
            # matches brief-as-identity semantics — a shape has one role,
            # not per-run hex chaos.
            chromatic_runs = [
                r for r in (fs.runs or [])
                if r.color_hex
            ]
            if chromatic_runs:
                # Pick the most-used chromatic color as "shape dominant".
                color_counts: dict[str, int] = {}
                for r in chromatic_runs:
                    key = (r.color_hex or "").upper()
                    color_counts[key] = color_counts.get(key, 0) + 1
                dom_hex = max(color_counts, key=lambda k: color_counts[k])
                should, target = _should_rewrite_run_color(dom_hex, brief_hexes)
                if should and target:
                    all_requests.append({
                        "updateTextStyle": {
                            "objectId": fs.object_id,
                            "textRange": {"type": "ALL"},
                            "fields": "foregroundColor",
                            "style": {
                                "foregroundColor": {
                                    "opaqueColor": {
                                        "rgbColor": _hex_to_rgb_fracs(target)
                                    }
                                }
                            },
                        }
                    })
                    text_rewrites += 1

        # --- font normalization (Scope D) ---
        font_rewrites = 0
        if normalize_fonts:
            ff = (brief.get("font_family") or {}) if isinstance(brief, dict) else {}
            brief_heading = ff.get("heading") if isinstance(ff, dict) else None
            brief_body = ff.get("body") if isinstance(ff, dict) else None
            if brief_heading or brief_body:
                for element in slide.get("pageElements", []) or []:
                    shape = element.get("shape") or {}
                    text = shape.get("text") or {}
                    obj_id = element.get("objectId")
                    if not obj_id or not text:
                        continue
                    # Classify this shape as heading vs body by MAX run size.
                    sizes: list[float] = []
                    families_seen: set[str] = set()
                    for te in text.get("textElements", []) or []:
                        tr = te.get("textRun")
                        if not tr:
                            continue
                        style = tr.get("style") or {}
                        sz = (style.get("fontSize") or {}).get("magnitude")
                        if sz is not None:
                            try:
                                sizes.append(float(sz))
                            except (TypeError, ValueError):
                                pass
                        fam = style.get("fontFamily")
                        if isinstance(fam, str) and fam.strip():
                            families_seen.add(fam.strip())
                    if not families_seen:
                        continue
                    max_size = max(sizes) if sizes else 14.0
                    is_heading = max_size >= 24.0
                    target_family = brief_heading if is_heading else brief_body
                    if not target_family:
                        continue
                    # Skip if the shape's family ALREADY matches.
                    if all(f.strip().lower() == target_family.strip().lower()
                           for f in families_seen):
                        continue
                    all_requests.append({
                        "updateTextStyle": {
                            "objectId": obj_id,
                            "textRange": {"type": "ALL"},
                            "fields": "fontFamily",
                            "style": {"fontFamily": target_family},
                        }
                    })
                    font_rewrites += 1

        per_slide[sid] = {
            "fill_rewrites": fill_rewrites,
            "text_rewrites": text_rewrites,
            "font_rewrites": font_rewrites,
        }

    applied_count = 0
    if all_requests:
        response = slides_api.batch_update(deck_id, all_requests)
        applied_count = len(response.get("replies", []) or [])

    _append_audit(deck_id, all_requests, False, applied_count)

    # Thumbnails on verify != "never" AND actual rewrites happened.
    if verify != "never" and applied_count > 0:
        for sid, summary in per_slide.items():
            if (
                summary["fill_rewrites"] == 0
                and summary["text_rewrites"] == 0
                and summary.get("font_rewrites", 0) == 0
            ):
                continue
            try:
                summary["thumbnail_url"] = slides_api.get_thumbnail(
                    deck_id, sid, size="MEDIUM",
                )
            except Exception as e:  # noqa: BLE001 — thumbnail failure non-fatal
                summary["thumbnail_error"] = str(e)

    restyled = [sid for sid, s in per_slide.items()
                if s["fill_rewrites"] or s["text_rewrites"] or s.get("font_rewrites", 0)]
    total_rewrites = sum(
        s["fill_rewrites"] + s["text_rewrites"] + s.get("font_rewrites", 0)
        for s in per_slide.values()
    )

    return {
        "deck_id": deck_id,
        "brief_applied": brief,
        "restyled_slide_ids": restyled,
        "skipped_slide_ids": skipped,
        "total_rewrites": total_rewrites,
        "applied_request_count": applied_count,
        "per_slide": per_slide,
        "warnings": warnings,
        "next_step_hint": (
            "render_thumbnail(slide_id) on restyled slides to visually verify; "
            "if the amended palette should persist, call update_theme_brief(changes=brief_overrides)"
            if brief_overrides else
            "render_thumbnail(slide_id) on restyled slides to visually verify one-voice coherence"
        ),
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
def list_icons(filter_keyword: str | None = None) -> dict[str, Any]:
    """Browse the bundled icon catalog — vanilla primitives composed from Slides
    API native shape types.

    Filter: case-insensitive substring match against icon name + keywords +
    category. Returns sorted list grouped by category.

    Returns `{icons: [{name, category, keywords}], total, categories}`.

    Companion to `create_icon(name, at, fill_hex?)`. Every icon ships as
    Slides API shape primitives (RIGHT_ARROW, STAR_5, HEART, LIGHTNING_BOLT,
    composed rectangles for charts, etc.) — no external deps, theme-color
    native, scales perfectly.
    """
    found = icons_mod.list_icons(filter_keyword)
    categories = sorted({i["category"] for i in found})
    return {
        "icons": found,
        "total": len(found),
        "categories": categories,
        "usage_hint": (
            "call create_icon(deck_url, slide_id, at=[l,t,w,h], name='...') "
            "to draw one. Fill color defaults to the deck's brief.palette.accent."
        ),
    }


@mcp.tool()
def create_icon(
    deck_url: str,
    slide_id: str,
    at: list[float],
    name: str,
    fill_hex: str | None = None,
    outline_hex: str | None = None,
) -> dict[str, Any]:
    """Draw a vanilla icon on a slide by composing Slides API shape primitives.

    The icon catalog (see `list_icons()`) maps each name to 1-N native shape
    types (RIGHT_ARROW, STAR_5, ELLIPSE, composed rectangles, etc.) at
    relative coordinates; this tool scales them to the caller's `at` box.

    at: [left_in, top_in, width_in, height_in] in inches — same shape as
        `create_shape.at`.
    name: icon name from the registry. Unknown names raise KeyError with
        a partial list of known names.
    fill_hex: optional fill color for every shape in the icon. Defaults to
        the deck brief's `palette.accent` (if a theme brief exists), else
        neutral gray. Pass per-call to override.
    outline_hex: optional outline color. Omitted → no outline (filled only).

    Returns `{deck_id, slide_id, icon_name, object_ids,
    applied_request_count, thumbnail_url}`. Follow up with
    `render_thumbnail(slide_id)` for native ImageContent to visually verify.

    Decoration rule (Decision P extension): icons are VANILLA primitives —
    use them freely in pill cards, flow diagrams, hero overlays, pill header
    accents. They are NOT images in the raster sense; `create_image`
    remains reserved for photos / logos / screenshots.
    """
    spec = icons_mod.get_icon_spec(name)
    if not at or len(at) < 4:
        raise ValueError("at must be [left_in, top_in, width_in, height_in]")
    left_in, top_in, w_in, h_in = (float(x) for x in at[:4])
    if w_in <= 0 or h_in <= 0:
        raise ValueError("width and height must be positive")

    deck_id = slides_api.deck_id_from_url(deck_url)

    # Fill resolution: per-call > brief.palette.accent > safety neutral
    resolved_fill = fill_hex
    if resolved_fill is None:
        try:
            prez = _fetch_for_brief(deck_id)
            meta = theme_brief_mod.find_meta_slide(prez)
            if meta is not None:
                brief = theme_brief_mod.parse_brief_body(meta["body_text"])
                if brief:
                    accent = (brief.get("palette") or {}).get("accent")
                    if isinstance(accent, str):
                        resolved_fill = accent
        except Exception:  # noqa: BLE001 — brief fetch is best-effort
            pass
    if resolved_fill is None:
        resolved_fill = "#888888"

    requests: list[dict[str, Any]] = []
    object_ids: list[str] = []
    for shape_spec in spec.get("shapes") or []:
        obj_id = _new_object_id(prefix="ico_")
        object_ids.append(obj_id)
        rel_at = shape_spec.get("at") or [0.0, 0.0, 1.0, 1.0]
        rl, rt, rw, rh = (float(x) for x in rel_at[:4])
        abs_w = max(rw * w_in, 0.05)  # prevent degenerate 0-size shapes
        abs_h = max(rh * h_in, 0.05)
        shape_type = shape_spec.get("type") or "RECTANGLE"
        # Per-shape fill override (for layered icons like target, bullseye).
        shape_fill = shape_spec.get("fill_hex") or resolved_fill
        requests.append({
            "createShape": {
                "objectId": obj_id,
                "shapeType": shape_type,
                "elementProperties": {
                    "pageObjectId": slide_id,
                    "size": {
                        "width": {"magnitude": _inch_to_emu(abs_w), "unit": "EMU"},
                        "height": {"magnitude": _inch_to_emu(abs_h), "unit": "EMU"},
                    },
                    "transform": {
                        "scaleX": 1, "scaleY": 1,
                        "translateX": _inch_to_emu(left_in + rl * w_in),
                        "translateY": _inch_to_emu(top_in + rt * h_in),
                        "unit": "EMU",
                    },
                },
            }
        })
        # Fill + optional outline
        shape_props: dict[str, Any] = {
            "shapeBackgroundFill": {
                "solidFill": {
                    "color": {"rgbColor": _hex_to_rgb_fracs(shape_fill)}
                }
            },
        }
        fields = "shapeBackgroundFill.solidFill.color"
        if outline_hex is not None:
            shape_props["outline"] = {
                "outlineFill": {
                    "solidFill": {
                        "color": {"rgbColor": _hex_to_rgb_fracs(outline_hex)}
                    }
                },
                "weight": {"magnitude": 1.5, "unit": "PT"},
            }
            fields += ",outline.outlineFill.solidFill.color,outline.weight"
        else:
            shape_props["outline"] = {"propertyState": "NOT_RENDERED"}
            fields += ",outline.propertyState"
        requests.append({
            "updateShapeProperties": {
                "objectId": obj_id,
                "fields": fields,
                "shapeProperties": shape_props,
            }
        })

    slides_api.batch_update(deck_id, requests)
    thumbnail_url = slides_api.get_thumbnail(deck_id, slide_id, size="MEDIUM")
    return {
        "deck_id": deck_id,
        "slide_id": slide_id,
        "icon_name": name,
        "object_ids": object_ids,
        "fill_hex": resolved_fill,
        "outline_hex": outline_hex,
        "applied_request_count": len(requests),
        "thumbnail_url": thumbnail_url,
        "next_step_hint": (
            f"render_thumbnail(slide_id={slide_id!r}) for native ImageContent"
        ),
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

    brief_fields_used = _infer_brief_fields_used(content, brief) if brief_applied else []
    return {
        "deck_id": deck_id,
        "slide_id": new_slide_id,
        "archetype": archetype,
        "insertion_index": resolved_index,
        "applied_request_count": len(all_reqs),
        "thumbnail_url": thumbnail_url,
        "warnings": warnings,
        "brief_applied": brief_applied,
        "brief_fields_used": brief_fields_used,
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


def _infer_brief_fields_used(
    content: dict[str, Any],
    brief: dict[str, Any] | None,
) -> list[str]:
    """Heuristic: return dotted brief paths consumed by this create_slide call.

    A brief path counts as "used" when:
      - the brief has it set, AND
      - the per-slide `content` does NOT carry an equivalent override

    This is observability sugar: lets the caller see WHICH brief fallbacks
    kicked in, without instrumenting every builder. Keep this in sync with
    create.py builder surfaces — adding a new builder surface that reads
    from the brief implies adding a detection clause here.
    """
    if not brief:
        return []
    used: list[str] = []
    palette = brief.get("palette") or {}

    accent_override_keys = (
        "title_accent_hex", "separator_color_hex", "accent_color_hex",
    )
    text_override_keys = (
        "body_text_color_hex", "title_color_hex", "subtitle_color_hex",
    )

    if palette.get("accent") and not any(k in content for k in accent_override_keys):
        used.append("palette.accent")

    if palette.get("text") and not any(k in content for k in text_override_keys):
        used.append("palette.text")

    if palette.get("category_set"):
        per_col_hex = any(
            isinstance(c, dict) and c.get("pill_hex")
            for c in (content.get("columns") or [])
        )
        if (
            "pill_palette" not in content
            and "numbers_palette" not in content
            and not per_col_hex
        ):
            used.append("palette.category_set")

    if palette.get("surface"):
        # Builders reserve surface for future use (section_opener, bg fills);
        # today it's carried-but-unused. Don't claim it as applied unless we
        # have a surface-consuming builder.
        pass

    ff = brief.get("font_family") or {}
    if isinstance(ff, dict):
        if isinstance(ff.get("heading"), str) and ff["heading"].strip():
            used.append("font_family.heading")
        if isinstance(ff.get("body"), str) and ff["body"].strip():
            used.append("font_family.body")

    return used


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


@mcp.tool()
def audit_brief_coherence(
    deck_url: str,
    slide_ids: list[str] | None = None,
) -> dict[str, Any]:
    """"Did the deck stick to its brief?" — single closed-loop check.

    slide_ids: when passed, restrict the audit to just those slides. Use
        this to score a freshly-generated batch without legacy drift
        polluting the composite. Typical pattern after create_slide loop:

            generated = [r["slide_id"] for r in create_responses]
            audit_brief_coherence(deck_url, slide_ids=generated)

    Walks every non-meta slide, compares observed palette/fonts/shapes against
    the active theme brief, returns a structured coherence report with a
    composite 0..1 score + drift breakdown + slide-level fix hints.

    Companion of `audit_deck_colors` + `audit_typography` — those return raw
    drift histograms; this tool folds them into one verdict with actionable
    hints. Use as the last gate before a deck ships:

        report = audit_brief_coherence(deck_url)
        if report["coherence_score"] < 0.8:
            restyle_slides(deck_url, slide_ids="all",
                           normalize_fonts=True, confirm_destructive=True)

    Returns:
        brief_active: bool — whether a brief was found and used
        brief_used: dict | None — the brief applied, for caller convenience
        coherence_score: float 0..1 — weighted composite (palette 50%, font 30%, shape 20%)
        sub_scores: {palette, font, shape} — individual 0..1 ratios
        drift_by_kind: {palette, font, shape} — raw drift counts
        slides_with_drift: [{slide_id, drift_fields, fix_hint}, ...] up to 20
        most_common_overrides: [{hex, count, fill_count, text_count}, ...] up to 10
        observations: raw counts for inspection
        next_action_hint: prescriptive next step based on score

    Near-neutral colors (black / white / mid-gray) are considered always-
    matching — they're structural, not brand-expressive. A deck with no brief
    returns coherence_score 0.0 and a hint to run extract_theme_brief.
    """
    deck_id = slides_api.deck_id_from_url(deck_url)
    prez = slides_api.get_presentation(deck_id)
    brief: dict[str, Any] | None = None
    try:
        meta = theme_brief_mod.find_meta_slide(prez)
        if meta is not None:
            brief = theme_brief_mod.parse_brief_body(meta["body_text"])
    except Exception:  # noqa: BLE001 — best-effort; absent brief is normal
        brief = None
    report = theme_brief_mod.audit_brief_coherence(prez, brief, slide_ids=slide_ids)
    report["deck_id"] = deck_id
    report["scoped_slide_ids"] = slide_ids
    return report


@mcp.tool()
def orient_to_deck(
    deck_url: str,
    outline_limit: int = 30,
    outline_offset: int = 0,
) -> dict[str, Any]:
    """Composite onboarding: one call returns everything a fresh agent needs.

    When you enter a deck you haven't seen (forked session, brownfield review,
    mid-project handoff), call this FIRST. Saves the 4-5 sequential calls that
    would otherwise be needed to understand shape, brief, drift, and archetype
    mix.

    Token efficiency:
      - Summary fields (brief, archetype_histogram, coherence, dominant_font)
        are CONSTANT-SIZE regardless of deck length.
      - The `outline` list scales linearly with deck size (~20 tok/slide).
        Capped by `outline_limit` (default 30) so a 200-slide deck stays cheap.
        Use `outline_limit=0` for summary-only, or paginate with
        `outline_offset=N` to walk larger decks.
      - Coherence walker inspects ALL slides internally for accurate scores;
        only reported slides-with-drift are capped (at 20 per audit).

    Args:
        deck_url: standard Google Slides URL
        outline_limit: max outline entries to return. 0 = skip outline entirely
            (~500 tok response). -1 = unlimited. Default 30 = ~600-900 tok.
        outline_offset: starting slide index for outline pagination.

    Returns:
        deck_id: str
        total_slides: int
        outline: list of {slide_id, title, archetype, is_meta}
        outline_truncated: bool — True when outline_limit cut the response
        outline_offset, outline_limit: pagination echo for follow-up calls
        brief: dict | None — active brief (None if absent)
        archetype_histogram: {archetype: count} — what layouts are in use
        dominant_font: {family, percentage} | None — most common text font
        coherence: nested coherence report (see audit_brief_coherence)
        next_action_hint: prescriptive string based on deck state

    Pattern the agent SHOULD follow after this call:

        orient = orient_to_deck(deck_url, outline_limit=30)
        if orient["brief"] is None:
            # propose via extract_theme_brief → render_brief_swatch → user picks
        elif orient["coherence"]["coherence_score"] < 0.7:
            # restyle before any new content lands
        else:
            # deck is coherent, proceed with create_slide / etc.
    """
    deck_id = slides_api.deck_id_from_url(deck_url)
    prez = slides_api.get_presentation(deck_id)

    # Brief
    brief: dict[str, Any] | None = None
    try:
        meta = theme_brief_mod.find_meta_slide(prez)
        if meta is not None:
            brief = theme_brief_mod.parse_brief_body(meta["body_text"])
    except Exception:  # noqa: BLE001
        brief = None
    meta_sid = None
    try:
        meta2 = theme_brief_mod.find_meta_slide(prez)
        meta_sid = meta2["slide_id"] if meta2 else None
    except Exception:  # noqa: BLE001
        meta_sid = None

    # Outline + archetype histogram
    outline: list[dict[str, Any]] = []
    archetype_histogram: dict[str, int] = {}
    font_counter: dict[str, int] = {}
    total_fonts = 0
    for slide in prez.get("slides", []) or []:
        sid = slide.get("objectId") or ""
        is_meta = sid == meta_sid
        flat = normalize_mod.normalize_page(slide)
        arch_name = classify_mod.classify(flat) if flat else "empty"
        if is_meta:
            arch_name = "__meta_slide__"
        archetype_histogram[arch_name] = archetype_histogram.get(arch_name, 0) + 1
        # title (best-effort, first non-empty text)
        title_text = ""
        for element in slide.get("pageElements", []) or []:
            text = (element.get("shape") or {}).get("text") or {}
            tstr = ""
            for te in text.get("textElements", []) or []:
                tr = te.get("textRun")
                if tr and tr.get("content"):
                    tstr += tr["content"]
                    style = tr.get("style") or {}
                    fam = style.get("fontFamily")
                    if fam:
                        font_counter[fam] = font_counter.get(fam, 0) + 1
                        total_fonts += 1
            tstr = tstr.strip()
            if tstr and not title_text:
                title_text = tstr[:80]
        outline.append({
            "slide_id": sid,
            "title": title_text,
            "archetype": arch_name,
            "is_meta": is_meta,
        })

    # Dominant font
    dominant_font: dict[str, Any] | None = None
    if total_fonts > 0:
        top_fam, top_count = max(font_counter.items(), key=lambda kv: kv[1])
        dominant_font = {
            "family": top_fam,
            "percentage": round(top_count * 100.0 / total_fonts, 1),
        }

    # Coherence
    coherence = theme_brief_mod.audit_brief_coherence(prez, brief)

    # Next-action hint
    if brief is None:
        hint = (
            "no active brief — run extract_theme_brief(deck_url) to propose one from "
            "the existing palette, then render_brief_swatch(proposed) to review, "
            "then set_theme_brief(deck_url, brief) to commit"
        )
    elif coherence["coherence_score"] < 0.7:
        hint = (
            f"brief active but coherence low ({coherence['coherence_score']}); "
            f"restyle_slides(slide_ids='all', normalize_fonts=True, "
            f"confirm_destructive=True) before adding new content"
        )
    elif coherence["coherence_score"] < 0.9:
        hint = (
            f"brief active, minor drift ({coherence['coherence_score']}); "
            f"restyle the flagged slides or proceed — agent's call"
        )
    else:
        hint = f"deck coherent ({coherence['coherence_score']}) — ready for new content"

    # Apply outline pagination AFTER full walk (walk is cheap; slicing cap the
    # response). Default outline_limit=30 keeps response ~600-900 tok on any deck.
    total_slides = len(outline)
    if outline_limit == 0:
        outline_page: list[dict[str, Any]] = []
        truncated = total_slides > 0
    elif outline_limit < 0:
        outline_page = outline[max(0, outline_offset):]
        truncated = False
    else:
        start = max(0, outline_offset)
        end = start + outline_limit
        outline_page = outline[start:end]
        truncated = end < total_slides

    return {
        "deck_id": deck_id,
        "total_slides": total_slides,
        "outline": outline_page,
        "outline_truncated": truncated,
        "outline_offset": outline_offset,
        "outline_limit": outline_limit,
        "brief": brief,
        "archetype_histogram": archetype_histogram,
        "dominant_font": dominant_font,
        "coherence": coherence,
        "next_action_hint": hint,
    }


@mcp.tool()
def propose_brief_variants(
    intent: str,
    n: int = 3,
    exclude_current_brief: bool = False,
    deck_url: str | None = None,
) -> dict[str, Any]:
    """Propose N distinct-mood theme briefs from natural-language intent.

    Pure function by default. If `exclude_current_brief=True` AND `deck_url`
    is given, the deck's active brief accent is auto-excluded from the
    candidate pool — useful when offering alternatives to a deck that
    already has a brief committed.

    Each returned brief carries the full axis: palette + shape_language +
    numbering_style + tone + image_prompt_style + font_family. Ready to pass
    to `set_theme_brief` as-is, or to preview via `render_brief_swatch_grid`.

    Returns {variants: list[brief], count, intent, excluded: list[hex]}.
    """
    excluded: list[str] = []
    if exclude_current_brief and deck_url:
        try:
            deck_id = slides_api.deck_id_from_url(deck_url)
            prez = slides_api.get_presentation(deck_id)
            meta = theme_brief_mod.find_meta_slide(prez)
            if meta:
                current = theme_brief_mod.parse_brief_body(meta["body_text"])
                if current:
                    cur_accent = (current.get("palette") or {}).get("accent")
                    if isinstance(cur_accent, str):
                        excluded.append(cur_accent)
        except Exception:  # noqa: BLE001 — absent/malformed brief is OK
            pass
    variants = theme_brief_mod.propose_brief_variants(
        intent, n=n, exclude_accents=excluded or None,
    )
    return {
        "variants": variants,
        "count": len(variants),
        "intent": intent,
        "excluded_accents": excluded,
    }


@mcp.tool()
def list_font_pairings(mood: str | None = None) -> dict[str, Any]:
    """Return curated Google Fonts pairings for theme briefs.

    Each pairing is a {heading, body} combo tagged with mood keywords. Use
    this to pick a `font_family` axis for `set_theme_brief` or to seed
    variant generation.

    mood: optional filter — case-insensitive substring match against the
    pairing's mood tags. Examples: "tech", "editorial", "bold", "warm".
    Omit to list all pairings.

    Returns {pairings: [{id, heading, body, mood: [str, ...], rationale}, ...]}.

    Pairings are Google Fonts catalog picks — free, web-available, cached
    widely. Use `render_brief_swatch` to preview a pairing as a PNG before
    committing via `set_theme_brief`.
    """
    pairings = theme_brief_mod.list_font_pairings(mood)
    return {
        "pairings": pairings,
        "count": len(pairings),
        "mood_filter": mood,
    }


@mcp.tool()
def render_brief_swatch(brief: dict[str, Any]) -> Image:
    """Render a theme brief as a tone-card PNG and return as MCP ImageContent.

    The swatch is the **fast-switch approval primitive** for the "Approve before
    you commit" workflow: a human-scannable PNG composition of every
    visually-expressive field in a brief (palette, shape_language,
    numbering_style, font_family if present, tone, image_prompt_style).
    Zero Slides API calls, zero deck writes — pure PIL composition.

    Use cases:
      - After `propose_brief_variants`, render each variant's swatch to let the
        human pick ONE before running `generate_variants` (which writes slides).
      - After `extract_theme_brief` on a brownfield deck, render the extracted
        brief so the human confirms before setting it.
      - During a taste-test loop: tweak a hex in a brief dict, re-render, eyeball.

    Accepts any dict shaped like the standard brief (see `set_theme_brief`).
    Missing fields degrade gracefully — a minimal brief with just `palette`
    renders a recognizable card with fewer annotations.

    Returns native MCP ImageContent (PNG) — ready for bidi agent vision loop.

    Raises ValueError if palette.surface is absent or malformed (a card must
    have a background to render).
    """
    png_bytes = swatch_mod.render_swatch(brief)
    return Image(data=png_bytes, format="png")


@mcp.tool()
def render_brief_swatch_grid(briefs: list[dict[str, Any]]) -> Image:
    """Render N briefs as a composite grid PNG and return as MCP ImageContent.

    THE fast-switch primitive — cuts "generate 3 variants × 6 slides then
    eyeball" from 18 round-trips to 1. One PNG, N candidate tones, human picks.

    Layout:
      - 1 brief  -> 1×1
      - 2 briefs -> 1×2
      - N >= 3   -> 3 cols, rows auto-expand
      - Each tile labeled `Variant i` + tone if present

    Typical flow:
        briefs = propose_brief_variants("board pitch for Series B", n=3)
        render_brief_swatch_grid(briefs)  # human picks one
        set_theme_brief(deck_url, briefs[chosen])
        # now run generate_variants / create_slide with the locked brief

    No deck writes. No Slides API calls. Returns native MCP ImageContent.

    Raises ValueError if `briefs` is empty.
    """
    png_bytes = swatch_mod.render_swatch_grid(briefs)
    return Image(data=png_bytes, format="png")


@mcp.tool()
def render_deck_contact_sheet(
    deck_url: str,
    slide_ids: list[str] | None = None,
    variant_id: str | None = None,
    title: str | None = None,
    thumbnail_size: str = "SMALL",
    max_slides: int = 36,
) -> Image:
    """Compose every slide's thumbnail into one grid PNG. Returns MCP ImageContent.

    Cuts variant comparison from N round-trips to 1. Use cases:
      - Visual audit of a whole deck in one eyeball pass
      - Compare N variant slides (by prefix) after `generate_variants`
      - Pre-ship snapshot of the deck's visual voice

    Token + latency budget:
      - Each thumbnail is one Slides API call (~0.5s each).
      - Output PNG bytes scale with tile count: SMALL × 36 tiles ≈ 200-400KB;
        MEDIUM × 36 ≈ 2-4MB.
      - `max_slides` (default 36 = 4×9 grid) caps the response. For bigger
        decks, either narrow via slide_ids / variant_id, or call again with
        a different slide_ids window. Excess slides are dropped from the end
        of the resolved target list, with a warning-like note in the tile.

    Args:
        deck_url: standard Google Slides URL
        slide_ids: explicit list of slide IDs to include. None = all
            non-meta slides in deck order.
        variant_id: filter to slides whose ID starts with this prefix. When
            used, overrides slide_ids. Example: variant_id="v0_" picks up all
            v0_* slides from generate_variants output.
        title: optional header rendered at top of contact sheet.
        thumbnail_size: "SMALL" (200x112 EMU), "MEDIUM" (800x450). Default
            SMALL keeps per-slide fetch <50KB; MEDIUM for higher-fidelity
            audits at ~10x the cost.

    Returns native MCP ImageContent (PNG). The grid is 4 cols wide; rows
    auto-expand. Tiles are 400x225 rendered; each labeled with slide ID.

    Raises ValueError if the deck has no slides matching the filter.
    """
    deck_id = slides_api.deck_id_from_url(deck_url)
    prez = slides_api.get_presentation(deck_id)

    # Determine meta slide so we can skip it by default
    meta_sid = None
    try:
        meta = theme_brief_mod.find_meta_slide(prez)
        meta_sid = meta["slide_id"] if meta else None
    except Exception:  # noqa: BLE001
        meta_sid = None

    all_slides = [s.get("objectId") for s in (prez.get("slides") or []) if s.get("objectId")]

    # Resolve target slide list
    if variant_id:
        targets = [s for s in all_slides if s.startswith(variant_id)]
    elif slide_ids:
        available = set(all_slides)
        targets = [s for s in slide_ids if s in available]
    else:
        targets = [s for s in all_slides if s != meta_sid]

    if not targets:
        raise ValueError(
            f"no slides to render (variant_id={variant_id!r}, slide_ids={slide_ids!r})"
        )

    # Apply max_slides cap for cost control on large decks.
    truncated = False
    if len(targets) > max_slides:
        truncated = True
        targets = targets[:max_slides]

    # Fetch thumbnails
    thumbnails: list[tuple[str, bytes]] = []
    for sid in targets:
        try:
            png = slides_api.get_thumbnail_bytes(deck_id, sid, size=thumbnail_size)
            thumbnails.append((sid, png))
        except Exception:  # noqa: BLE001
            # Skip unrenderable slides — keep going with others.
            thumbnails.append((f"{sid} (error)", b""))
            continue

    sheet_title = title
    if truncated and not sheet_title:
        sheet_title = f"(showing first {len(thumbnails)} / {len(all_slides)} slides — pass slide_ids= to narrow)"
    elif truncated and sheet_title:
        sheet_title = f"{sheet_title} (truncated to {len(thumbnails)} of {len(all_slides)})"
    composed = swatch_mod.render_contact_sheet(thumbnails, title=sheet_title)
    return Image(data=composed, format="png")


@mcp.tool()
def preview_archetype(
    archetype: str,
    content: dict[str, Any],
    brief: dict[str, Any] | None = None,
) -> Image:
    """Render an archetype+content+brief combination as a preview PNG.

    **No Slides API calls, no deck writes.** Pure PIL composition. Use when
    you want to compare N archetypes for the same content before committing
    one via `create_slide`.

    Supported archetypes:
      - cover_with_hero: {title, subtitle?}
      - text_left_image_right: {title, body | paragraphs}
      - 3col_pill_cards: {title, lead?, columns: [{pill, body}x3]}
      - 4col_numbered_flow: {title, columns: [{num, subtitle, body}x4]}
      - text_heavy_body: {title, paragraphs}

    Unknown archetypes render a fallback sketch.

    content: same semantic shape you'd pass to `create_slide`, though
        per-call hex overrides are mostly ignored (brief drives the palette).
    brief: optional theme brief dict. If omitted, uses safe default palette.

    Returns MCP ImageContent. A badge in the top-right of every preview
    marks it as "PREVIEW · NOT WRITTEN TO DECK" so the human can't confuse
    it with a real render.

    Typical flow:
        # User debates archetype for a metrics slide.
        for arch in ["3col_pill_cards", "4col_numbered_flow", "text_left_image_right"]:
            preview_archetype(arch, content, brief)
        # Human picks one, then:
        create_slide(deck_url, chosen_arch, content)
    """
    png_bytes = swatch_mod.render_archetype_preview(archetype, content, brief)
    return Image(data=png_bytes, format="png")


def main() -> None:
    """Entry point for `slides-mcp` console script."""
    mcp.run()


if __name__ == "__main__":
    main()
