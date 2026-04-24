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
from . import catalog as catalog_mod
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


def list_themes() -> dict[str, Any]:
    """List all theme files discoverable in the search paths."""
    return {"themes": theme_mod.available_themes()}


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


def _render_thumbnail_bytes(deck_url: str, slide_id: str, size: str = "MEDIUM") -> Image:
    """Render a slide as a PNG image and return it as native MCP ImageContent.

    size: "SMALL" (200×112 at 16:9), "MEDIUM" (800×450), "LARGE" (1600×900).
    The image is fetched as bytes so the caller consumes it directly — no
    URL round-trip. URLs returned by the underlying Slides API expire ~30min,
    but bytes don't, so this is the lossless shape for a bidi agent loop.
    """
    deck_id = slides_api.deck_id_from_url(deck_url)
    png_bytes = slides_api.get_thumbnail_bytes(deck_id, slide_id, size=size)
    return Image(data=png_bytes, format="png")


def _render_thumbnail_url(deck_url: str, slide_id: str, size: str = "MEDIUM") -> dict[str, Any]:
    """Return the short-lived contentUrl for a slide thumbnail (no image bytes).

    Use this when a URL is sufficient (e.g. embedding in a report). For agent
    consumption prefer the bytes variant.
    """
    deck_id = slides_api.deck_id_from_url(deck_url)
    url = slides_api.get_thumbnail(deck_id, slide_id, size=size)
    return {"deck_id": deck_id, "slide_id": slide_id, "thumbnail_url": url, "size": size}


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


def _find_notes_placeholder_id(
    prez: dict[str, Any], slide_id: str
) -> str | None:
    """Find the speaker-notes BODY placeholder objectId on a given slide.

    Google Slides auto-creates a notesPage for every slide; its body
    placeholder is where speaker notes live. Walks
    ``slides[].slideProperties.notesPage.pageElements`` for a shape with
    ``placeholder.type == "BODY"``. Falls back to the first placeholder-bearing
    pageElement if BODY isn't explicitly typed.

    Returns None if no usable notes placeholder is found — callers should
    gracefully skip notes population in that case (meta slide still valid).
    """
    for slide in prez.get("slides", []) or []:
        if slide.get("objectId") != slide_id:
            continue
        notes_page = (slide.get("slideProperties") or {}).get("notesPage") or {}
        fallback_id: str | None = None
        for element in notes_page.get("pageElements", []) or []:
            shape = element.get("shape") or {}
            placeholder = shape.get("placeholder") or {}
            ptype = placeholder.get("type")
            obj_id = element.get("objectId")
            if ptype == "BODY":
                return obj_id
            if placeholder and fallback_id is None:
                fallback_id = obj_id
        return fallback_id
    return None


def _populate_meta_slide_notes(
    deck_id: str, meta_slide_id: str
) -> list[str]:
    """Best-effort populate the meta slide's speaker notes.

    Fetches the notesPage BODY placeholder id for ``meta_slide_id``, then
    issues an insertText batchUpdate with SPEAKER_NOTES_TEXT. Any failure
    (missing placeholder, API error) returns a warning string so the caller
    can surface it without failing meta creation itself — notes are a
    durability layer, not a correctness requirement.

    Returns a list of warning strings (empty on success).
    """
    warnings: list[str] = []
    try:
        prez = slides_api.get_presentation(
            deck_id,
            fields=(
                "slides(objectId,slideProperties.notesPage.pageElements("
                "objectId,shape.placeholder))"
            ),
        )
        notes_id = _find_notes_placeholder_id(prez, meta_slide_id)
        if not notes_id:
            warnings.append(
                "speaker notes placeholder not found on new meta slide; "
                "notes left empty (meta slide body still carries warning preamble)"
            )
            return warnings
        notes_reqs = theme_brief_mod.build_notes_populate_requests(notes_id)
        slides_api.batch_update(deck_id, notes_reqs)
    except Exception as exc:  # noqa: BLE001 — best-effort, don't fail meta creation
        warnings.append(
            f"speaker notes not populated: {type(exc).__name__}: {exc}"
        )
    return warnings


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
    # Durability: populate speaker notes with rebuild instructions.
    # Best-effort — any failure surfaces as a warning, doesn't fail meta creation.
    notes_warnings = _populate_meta_slide_notes(deck_id, slide_id)
    return {
        "deck_id": deck_id,
        "slide_id": slide_id,
        "brief": brief,
        "action": "created",
        "applied_request_count": len(reqs),
        "warnings": notes_warnings,
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


def scaffold_meta_brief(
    deck_url: str,
    auto_commit_if_high_confidence: bool = False,
) -> dict[str, Any]:
    """Brownfield one-shot: ensure the deck has a theme brief, in a single call.

    Most decks the agent opens are BROWNFIELD — they already exist with colors,
    text, fonts, but no meta-slide. The legacy 3-call dance
    (get_theme_brief → extract_theme_brief → set_theme_brief) forces a human
    review loop for every deck. ``scaffold_meta_brief`` collapses it:

      1. If the deck already has a parseable brief → returns ``status: "exists"``.
      2. If absent (or corrupted meta): extracts a proposal from the deck's
         existing palette + shape topology.
         - ``auto_commit_if_high_confidence=True`` AND ``confidence == "high"``:
           commits via set_theme_brief → ``status: "created"``. The new meta
           slide's marker, body, AND speaker notes are populated — humans who
           find the hidden slide later have context + rebuild instructions.
         - Otherwise: returns ``status: "proposed"`` with the proposed brief
           and evidence. Caller reviews with user, then commits via
           set_theme_brief(deck_url, brief).

    This is the brownfield-first entry point. Use this over
    extract_theme_brief + set_theme_brief when you want durability + scaffold
    in one shot.

    Args:
        deck_url: standard Google Slides URL
        auto_commit_if_high_confidence: when True, proposals with
            ``confidence == "high"`` commit automatically. Default False for
            safety — the agent should review before committing on unfamiliar
            decks. Confidence is "high" when the extraction saw ≥3 slides
            and ≥8 distinct fill colors, indicating a well-established
            visual identity.

    Returns:
        deck_id: str
        status: "exists" | "created" | "proposed"
        slide_id: str | None   (None when status == "proposed")
        brief: dict            (existing, created, or proposed)
        proposal: dict | None  ({evidence, confidence}; None when status == "exists")
        next_step_hint: str
        warnings: list[str]    (non-empty when notes population had issues)
    """
    deck_id = slides_api.deck_id_from_url(deck_url)
    prez = _fetch_for_brief(deck_id)
    meta = theme_brief_mod.find_meta_slide(prez)

    # Path 1: meta exists and parses → nothing to do.
    if meta is not None:
        existing = theme_brief_mod.parse_brief_body(meta["body_text"])
        if existing is not None:
            return {
                "deck_id": deck_id,
                "status": "exists",
                "slide_id": meta["slide_id"],
                "brief": existing,
                "proposal": None,
                "next_step_hint": (
                    "deck already has a theme brief — no action taken. Use "
                    "update_theme_brief for forward-only edits, or "
                    "apply_brief_and_restyle to repaint existing slides."
                ),
                "warnings": [],
            }
        # meta found but body unparseable → treat as "needs repair" (extract fresh).

    # Path 2: absent or corrupted → extract a proposal.
    extraction = theme_brief_mod.extract_brief_from_prez(prez)
    proposed = extraction["proposed_brief"]
    confidence = extraction["confidence"]
    evidence = extraction["evidence"]

    if auto_commit_if_high_confidence and confidence == "high":
        # Commit via set_theme_brief (which also populates speaker notes).
        committed = set_theme_brief(deck_url, proposed)
        return {
            "deck_id": deck_id,
            "status": "created",
            "slide_id": committed["slide_id"],
            "brief": proposed,
            "proposal": {"evidence": evidence, "confidence": confidence},
            "next_step_hint": (
                "meta slide created with a high-confidence brief extracted "
                "from the deck. Review the deck visually; tweak via "
                "update_theme_brief if needed, or apply_brief_and_restyle "
                "to repaint existing slides with the new brief."
            ),
            "warnings": list(committed.get("warnings", [])),
        }

    return {
        "deck_id": deck_id,
        "status": "proposed",
        "slide_id": None,
        "brief": proposed,
        "proposal": {"evidence": evidence, "confidence": confidence},
        "next_step_hint": (
            f"proposal drafted (confidence={confidence!r}) but NOT committed. "
            "Review with user, then call set_theme_brief(deck_url, brief) to "
            "persist. Or re-run scaffold_meta_brief with "
            "auto_commit_if_high_confidence=True to auto-commit when "
            "confidence is 'high'."
        ),
        "warnings": [],
    }


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


def _tweak_brief_compute(
    deck_url: str,
    directive: str,
) -> dict[str, Any]:
    """Natural-language directive → delta against the deck's active brief.

    Reads the deck's current theme brief, parses ``directive`` through the
    heuristic axis rules, and returns the **computed delta + candidate
    brief** — without writing anything. The caller is expected to:

      1. Inspect ``matched_axes`` / ``unresolved_terms`` / ``confidence``.
      2. Call ``preview_brief_tweak(deck_url, candidate_brief=candidate_brief)``
         — this writes 2-4 actual sample slides into the deck under the
         candidate brief (with the current brief side-by-side when one exists)
         so the HUMAN opens Google Slides and picks by eye. The meta-slide
         is restored at the end — preview is non-persistent.
      3. If approved, commit with ``apply_brief_and_restyle(deck_url,
         brief=candidate, confirm_destructive=True)`` — repaints every
         existing slide. Remember to delete the tweak_preview_* slides
         afterward (delete_slide, one per id).

    Supported axes (heuristic, substring match on the lowercased directive):
      - "warmer" / "cooler"                   → rotate accent + category_set hue
      - "more saturated" / "more muted"       → scale accent + category_set S
      - "darker surface" / "lighter surface"  → shift palette.surface V
      - "sharper" / "rounder"                 → shape_language swap
      - "more editorial" / "more tech" /
        "bolder font" / "elegant"             → font_family swap via pairing
      - "bolder/outlined/dot/hidden numbering" → numbering_style swap

    Anything outside these axes lands in ``unresolved_terms`` — the agent
    should surface those to the user rather than silently over-apply.

    Returns ``{deck_id, directive, current_brief, delta, candidate_brief,
    matched_axes, unresolved_terms, changed_fields, confidence, rationale,
    warnings, next_step_hint}``.

    Raises FileNotFoundError if the deck has no theme brief. Call
    ``set_theme_brief`` or ``extract_theme_brief`` first.
    """
    deck_id = slides_api.deck_id_from_url(deck_url)
    prez = _fetch_for_brief(deck_id)
    meta = theme_brief_mod.find_meta_slide(prez)
    if meta is None:
        raise FileNotFoundError(
            "no theme-brief meta-slide on this deck. Brownfield flow: call "
            "extract_theme_brief(deck_url) to propose a brief from the "
            "existing deck, review it, then set_theme_brief(deck_url, brief) "
            "to create the hidden meta-slide — THEN tweak_brief works."
        )
    current_brief = theme_brief_mod.parse_brief_body(meta["body_text"])
    if current_brief is None:
        raise FileNotFoundError(
            "meta-slide found but body did not parse as a brief; call "
            "set_theme_brief to repair before tweaking."
        )

    result = theme_brief_mod.compute_directive_delta(current_brief, directive)

    confidence = result["confidence"]
    if confidence == "high":
        hint = (
            "preview_brief_tweak(deck_url, candidate_brief=candidate_brief) "
            "to write sample slides into the deck for HUMAN preview, then "
            "apply_brief_and_restyle(deck_url, brief=candidate_brief, "
            "confirm_destructive=True) to commit + repaint — remember to "
            "delete the tweak_preview_* slides after the human picks."
        )
    elif confidence == "medium":
        hint = (
            "directive partially recognised — unresolved_terms may need a "
            "human-in-loop clarification. If you still want to proceed, call "
            "preview_brief_tweak to drop sample slides into the deck so the "
            "human can eyeball before apply_brief_and_restyle."
        )
    else:
        hint = (
            "directive did not match any axis; try rewording with one of "
            "the supported phrases (warmer/cooler, more saturated/muted, "
            "darker/lighter surface, sharper/rounder, more editorial/tech, "
            "bolder/outlined/dot/hidden numbering)"
        )

    return {
        "deck_id": deck_id,
        "directive": directive,
        "current_brief": current_brief,
        "delta": result["delta"],
        "candidate_brief": result["candidate_brief"],
        "matched_axes": result["matched_axes"],
        "unresolved_terms": result["unresolved_terms"],
        "changed_fields": result["changed_fields"],
        "confidence": confidence,
        "rationale": result["rationale"],
        "warnings": result["warnings"],
        "next_step_hint": hint,
    }


@mcp.tool()
def apply_brief_and_restyle(
    deck_url: str,
    brief: dict[str, Any] | None = None,
    delta: dict[str, Any] | None = None,
    slide_ids: list[str] | str = "all",
    normalize_fonts: bool = True,
    confirm_destructive: bool = False,
) -> dict[str, Any]:
    """Commit brief (or merge delta) + repaint existing slides — one call.

    Collapses the old two-step ceremony::

        # BEFORE (two calls, two verifies, no lineage)
        update_theme_brief(deck_url, changes=delta)
        restyle_slides(deck_url, normalize_fonts=True, confirm_destructive=True)

        # AFTER (one call, unified response)
        apply_brief_and_restyle(deck_url, delta=delta,
                                normalize_fonts=True,
                                confirm_destructive=True)

    Pass **exactly one** of:
      - ``brief``: full brief dict → wholesale replacement (like set_theme_brief).
      - ``delta``: partial changes dict → deep-merged into current brief
        (like update_theme_brief). Requires an existing meta-slide.

    ``slide_ids``: list of slide IDs, or the literal ``"all"`` to restyle every
        non-meta slide.

    ``normalize_fonts``: forwarded to restyle_slides. Default **True** here
        because the common "apply" case includes a font repaint — the brief's
        ``font_family`` axis only takes effect on existing slides when this
        is True.

    ``confirm_destructive``: required True. restyle_slides overwrites per-call
        hex that may have been passed at create_slide time.

    Returns::

        {
          deck_id, meta_slide_id, brief (committed),
          action: "created" | "updated",
          restyle: {
            restyled_slide_ids, skipped_slide_ids, total_rewrites,
            per_slide, applied_request_count, thumbnails,
          },
          warnings,
        }

    Raises:
      - ValueError if neither or both of ``brief``/``delta`` are given, if
        ``confirm_destructive`` is False, or if the final brief fails validation.
      - FileNotFoundError if ``delta`` is given but no meta-slide exists
        to merge into.
    """
    if (brief is None) == (delta is None):
        raise ValueError(
            "apply_brief_and_restyle requires exactly one of `brief` "
            "(wholesale replacement) or `delta` (partial merge). Got "
            + ("both" if brief is not None else "neither")
        )
    if not confirm_destructive:
        raise ValueError(
            "apply_brief_and_restyle repaints existing slides and overwrites "
            "per-call hex that may have been set at create_slide time. "
            "Re-invoke with confirm_destructive=True to proceed."
        )

    # --- Early-validate `brief` path before any network call -------------
    if brief is not None:
        early_candidate: dict[str, Any] = dict(brief)
        early_candidate.setdefault("version", theme_brief_mod.SCHEMA_VERSION)
        ok_early, errors_early = theme_brief_mod.validate_brief(early_candidate)
        if not ok_early:
            raise ValueError("invalid brief: " + "; ".join(errors_early))

    deck_id = slides_api.deck_id_from_url(deck_url)
    prez = _fetch_for_brief(deck_id)
    meta = theme_brief_mod.find_meta_slide(prez)

    # --- Resolve final brief ---------------------------------------------
    if brief is not None:
        final_brief: dict[str, Any] = dict(brief)
        final_brief.setdefault("version", theme_brief_mod.SCHEMA_VERSION)
    else:
        if meta is None:
            raise FileNotFoundError(
                "no theme-brief meta-slide to merge delta into. Pass `brief` "
                "for a wholesale replacement, or call set_theme_brief first "
                "(brownfield path: extract_theme_brief + set_theme_brief)."
            )
        existing = theme_brief_mod.parse_brief_body(meta["body_text"]) or {}
        final_brief = theme_brief_mod.merge_brief(existing, delta or {})
        ok, errors = theme_brief_mod.validate_brief(final_brief)
        if not ok:
            raise ValueError("invalid brief: " + "; ".join(errors))

    # --- Step 1: Commit brief to meta-slide ------------------------------
    if meta is not None and meta.get("body_box_id"):
        # In-place body rewrite.
        reqs = theme_brief_mod.build_update_brief_requests(
            meta["body_box_id"], final_brief
        )
        slides_api.batch_update(deck_id, reqs)
        meta_slide_id = meta["slide_id"]
        action = "updated"
    else:
        # No meta (or corrupted) — delete + recreate.
        if meta is not None:
            slides_api.batch_update(
                deck_id, [{"deleteObject": {"objectId": meta["slide_id"]}}]
            )
        deck_w_in, deck_h_in = _deck_dimensions_in(deck_id)
        prez_after = slides_api.get_presentation(deck_id, fields="slides.objectId")
        insertion_index = len(prez_after.get("slides", []))
        meta_slide_id = _new_object_id(prefix=theme_brief_mod.META_SLIDE_ID_PREFIX)
        marker_id = _new_object_id(prefix=theme_brief_mod.MARKER_BOX_ID_PREFIX)
        body_id = _new_object_id(prefix=theme_brief_mod.BODY_BOX_ID_PREFIX)
        create_reqs = theme_brief_mod.build_create_meta_slide_requests(
            slide_id=meta_slide_id,
            marker_box_id=marker_id,
            body_box_id=body_id,
            brief=final_brief,
            deck_width_in=deck_w_in,
            deck_height_in=deck_h_in,
            insertion_index=insertion_index,
        )
        slides_api.batch_update(deck_id, create_reqs)
        action = "created"

    # --- Step 2: Restyle existing slides ---------------------------------
    restyle_result = restyle_slides(
        deck_url=deck_url,
        slide_ids=slide_ids,
        normalize_fonts=normalize_fonts,
        confirm_destructive=True,
    )

    return {
        "deck_id": deck_id,
        "meta_slide_id": meta_slide_id,
        "brief": final_brief,
        "action": action,
        "restyle": {
            "restyled_slide_ids": restyle_result.get("restyled_slide_ids", []),
            "skipped_slide_ids": restyle_result.get("skipped_slide_ids", []),
            "total_rewrites": restyle_result.get("total_rewrites", 0),
            "per_slide": restyle_result.get("per_slide", {}),
            "applied_request_count": restyle_result.get(
                "applied_request_count", 0
            ),
            "thumbnails": restyle_result.get("thumbnails", {}),
        },
        "warnings": list(restyle_result.get("warnings", [])),
    }


# Default showcase content for preview_brief_tweak when the caller doesn't
# provide any. Chosen for brief-axis coverage: cover shows accent + title
# font + surface; 3col_pill_cards shows category_set + shape_language +
# body font; text_left_image_right rounds out accent-on-lighter-surface.
_PREVIEW_DEFAULT_CONTENT: list[dict[str, Any]] = [
    {
        "archetype": "cover_with_hero",
        "content": {
            "title": "Brief preview — cover",
            "subtitle": "Palette + title font under this candidate brief.",
        },
        "slide_id": "cover",
    },
    {
        "archetype": "3col_pill_cards",
        "content": {
            "title": "Three pillars in this tone",
            "lead": "Category set + shape language come through here.",
            "columns": [
                {"pill": "Clarity", "body": "What the numbers say at a glance."},
                {"pill": "Impact", "body": "Why this matters for the reader."},
                {"pill": "Next steps", "body": "What we do with this finding."},
            ],
        },
        "slide_id": "pills",
    },
]


def preview_brief_tweak(
    deck_url: str,
    candidate_brief: dict[str, Any],
    sample_content: list[dict[str, Any]] | None = None,
    compare_to_current: bool = True,
    variant_prefix: str = "tweak_preview",
) -> dict[str, Any]:
    """Write sample slides into the deck under ``candidate_brief`` so the
    **HUMAN** opens Google Slides, flips through, and picks. This is the
    approval gate for live brief iteration — not a PIL swatch that only the
    agent sees.

    Anchor (Decision R/S): the brief lives in the deck's hidden meta-slide.
    ``preview_brief_tweak`` **temporarily** swaps the meta brief to render
    preview slides via ``generate_variants``, then **restores** the original
    brief at the end. ``candidate_brief`` is NOT persisted by this tool —
    call ``apply_brief_and_restyle(deck_url, brief=candidate_brief,
    confirm_destructive=True)`` to commit after the human approves.

    Default behaviour (no ``sample_content``):
      Writes 2 showcase slides (cover_with_hero + 3col_pill_cards) per brief.
      When ``compare_to_current=True`` AND the deck has a current brief,
      writes those 2 slides TWICE — once under the current brief, once under
      the candidate — so the human sees them side-by-side in the deck.
      Slide IDs: ``{variant_prefix}{0|1}_{cover|pills}``.

    Custom ``sample_content``:
      List of ``{archetype, content, slide_id?}`` dicts, same shape
      ``generate_variants`` accepts. Use to preview a specific archetype the
      deck actually uses (e.g. text_left_image_right).

    Brownfield:
      If the deck has no meta-slide yet, pass ``compare_to_current=False``
      AND understand that ``generate_variants``'s internal ``set_theme_brief``
      call will CREATE the meta-slide with ``candidate_brief``. Recommended
      flow instead: ``extract_theme_brief(deck_url)`` → review →
      ``set_theme_brief(deck_url, extracted_brief)`` to establish a baseline
      meta, THEN ``tweak_brief`` + ``preview_brief_tweak`` work naturally.

    Returns::

        {
          deck_id,
          preview_slide_ids_candidate: [...],
          preview_slide_ids_current: [...],    # [] if no current brief
          thumbnails: {slide_id: url},
          candidate_brief,
          current_brief,                       # None if brownfield
          variants_manifest,                   # pass to lock_variant for bulk cleanup
          meta_restored: bool,                  # True when original brief was restored
          next_step_hint,
          cleanup_hint,
        }

    Raises:
      - ValueError if ``candidate_brief`` fails validation.

    Cleanup after human approves:
      - commit: ``apply_brief_and_restyle(deck_url, brief=candidate_brief,
        confirm_destructive=True)`` — repaints existing slides.
      - delete preview slides: loop ``delete_slide(deck_url, slide_id)`` over
        every entry in ``preview_slide_ids_candidate +
        preview_slide_ids_current``, OR call ``lock_variant(deck_url,
        variant_id=<winner>, variants_manifest)`` to auto-delete the losing
        variant plus commit the winner's brief in one go.
    """
    ok, errors = theme_brief_mod.validate_brief(candidate_brief)
    if not ok:
        raise ValueError(
            "candidate_brief failed validation: " + "; ".join(errors)
        )

    deck_id = slides_api.deck_id_from_url(deck_url)
    prez = _fetch_for_brief(deck_id)
    meta = theme_brief_mod.find_meta_slide(prez)
    current_brief = (
        theme_brief_mod.parse_brief_body(meta["body_text"]) if meta else None
    )

    # Resolve briefs list
    if compare_to_current and current_brief is not None:
        briefs: list[dict[str, Any]] = [current_brief, candidate_brief]
    else:
        briefs = [candidate_brief]

    # Resolve content
    content_list = sample_content if sample_content else _PREVIEW_DEFAULT_CONTENT

    # Drive the render loop — this internally sets the meta brief per variant
    manifest = generate_variants(
        deck_url=deck_url,
        content_list=content_list,
        briefs=briefs,
        variant_prefix=variant_prefix,
    )

    # Restore original meta brief so the deck ends in the same state it
    # started. `generate_variants` leaves meta at `briefs[-1]` (candidate).
    meta_restored = False
    if current_brief is not None:
        set_theme_brief(deck_url, current_brief)
        meta_restored = True

    # Collect thumbnails for every preview slide the human will review
    thumbnails: dict[str, str] = {}
    preview_slide_ids_candidate: list[str] = []
    preview_slide_ids_current: list[str] = []
    for variant in manifest.get("variants", []):
        variant_id = variant.get("variant_id")
        slide_ids = variant.get("slide_ids", [])
        is_candidate_variant = variant.get("brief") == candidate_brief
        for sid in slide_ids:
            try:
                thumb_url = slides_api.get_thumbnail(deck_id, sid, size="MEDIUM")
                thumbnails[sid] = thumb_url
            except Exception as e:  # noqa: BLE001 — non-fatal
                thumbnails[sid] = f"(thumbnail fetch failed: {e})"
            if is_candidate_variant:
                preview_slide_ids_candidate.append(sid)
            else:
                preview_slide_ids_current.append(sid)
        # Fallback heuristic when brief-equality check misses (dict deep-eq
        # can fail on float stamping) — use variant index order
        del variant_id  # noqa

    # Robust fallback: if the equality heuristic populated neither bucket
    # (should not happen, but safe), fall back to index order: last variant
    # = candidate, earlier = current.
    if not preview_slide_ids_candidate and manifest.get("variants"):
        variants_list = manifest["variants"]
        # last variant is always the candidate per our briefs list ordering
        preview_slide_ids_candidate = list(variants_list[-1].get("slide_ids", []))
        preview_slide_ids_current = []
        for v in variants_list[:-1]:
            preview_slide_ids_current.extend(v.get("slide_ids", []))

    cleanup_hint = (
        "After the human picks: call lock_variant(deck_url, "
        f"variant_id='{variant_prefix}{len(briefs) - 1}', "
        "variants_manifest=<this.variants_manifest>) to commit candidate "
        "+ delete current-baseline preview slides in one call, OR call "
        "delete_slide per id in preview_slide_ids_candidate + "
        "preview_slide_ids_current, then apply_brief_and_restyle to persist."
    )
    next_step_hint = (
        "Open the deck in Google Slides and flip through the "
        f"{variant_prefix}0_* vs {variant_prefix}{len(briefs) - 1}_* slides. "
        "If human approves candidate: apply_brief_and_restyle(deck_url, "
        "brief=candidate_brief, confirm_destructive=True). If not: "
        "delete the preview slides and tweak_brief again with a different directive."
    )

    return {
        "deck_id": deck_id,
        "preview_slide_ids_candidate": preview_slide_ids_candidate,
        "preview_slide_ids_current": preview_slide_ids_current,
        "thumbnails": thumbnails,
        "candidate_brief": candidate_brief,
        "current_brief": current_brief,
        "variants_manifest": manifest,
        "meta_restored": meta_restored,
        "next_step_hint": next_step_hint,
        "cleanup_hint": cleanup_hint,
    }


# ---------------------------------------------------------------------------
# Brief catalog + export/import (Scope D + E, v0.8.0)
#
# The catalog is a USER-OWNED personal library at
# $XDG_CONFIG_HOME/slides-mcp/briefs/<id>.yaml. It is ORTHOGONAL to the
# meta-slide (Decision R/S): the deck's meta is the source of truth for
# an ACTIVE brief; the catalog is for library reuse across decks. Every
# catalog-to-deck commit goes through set_theme_brief — the catalog itself
# never touches decks directly.
# ---------------------------------------------------------------------------


def list_catalog_briefs(mood: str | None = None) -> dict[str, Any]:
    """List briefs saved in the user's personal catalog.

    Catalog path: ``$SLIDES_MCP_CATALOG_DIR`` OR ``$XDG_CONFIG_HOME/slides-mcp/briefs/``
    OR ``~/.config/slides-mcp/briefs/`` — resolved at call time. Entries are
    YAML files, one per brief, 100% user-owned.

    ``mood``: optional case-insensitive substring filter against each
    entry's ``mood_keywords`` list. Omit to list all entries.

    Returns {briefs, count, catalog_dir, mood_filter} where ``briefs`` is
    a list of metadata envelopes (no full brief body) — call
    ``use_catalog_brief`` to fetch + apply a specific entry to a deck.
    """
    entries = catalog_mod.list_briefs(mood=mood)
    return {
        "briefs": entries,
        "count": len(entries),
        "catalog_dir": str(catalog_mod.catalog_dir()),
        "mood_filter": mood,
    }


def save_brief_to_catalog(
    deck_url: str,
    name: str,
    mood_keywords: list[str] | None = None,
    brief_id: str | None = None,
    brief: dict[str, Any] | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Save a brief to the user's catalog for reuse across decks.

    Default source: reads the active deck's hidden meta-slide brief. Pass
    ``brief=`` explicitly to save a brief that isn't yet committed to any
    deck (e.g. a variant from ``propose_brief_variants`` the user liked).

    ``name``: human-readable title ("Client X warm editorial").
    ``mood_keywords``: free-form tags used by ``list_catalog_briefs(mood=)``.
    ``brief_id``: explicit slug; auto-generated from ``name`` if omitted.
    ``overwrite``: replace in-place when the id already exists.

    Catalog entries are NOT a cache of the deck — editing the catalog file
    does not change any deck, and editing a deck does not mutate its
    catalog copy. Use ``use_catalog_brief`` to reapply a saved entry.

    Returns the saved envelope: {id, name, mood_keywords, created_at, path,
    brief}.
    """
    if brief is None:
        deck_id = slides_api.deck_id_from_url(deck_url)
        prez = _fetch_for_brief(deck_id)
        meta = theme_brief_mod.find_meta_slide(prez)
        if meta is None:
            raise FileNotFoundError(
                "deck has no theme-brief meta-slide to save. Pass the "
                "`brief` param explicitly, or commit one with set_theme_brief "
                "(brownfield path: extract_theme_brief + set_theme_brief)."
            )
        brief_from_deck = theme_brief_mod.parse_brief_body(meta["body_text"])
        if brief_from_deck is None:
            raise FileNotFoundError(
                "meta-slide found but body did not parse as a brief"
            )
        brief = brief_from_deck

    ok, errors = theme_brief_mod.validate_brief(brief)
    if not ok:
        raise ValueError(
            "brief invalid — refusing to save: " + "; ".join(errors)
        )

    saved = catalog_mod.save_brief(
        brief=brief,
        name=name,
        brief_id=brief_id,
        mood_keywords=mood_keywords,
        overwrite=overwrite,
    )
    return {
        "id": saved["id"],
        "name": saved["name"],
        "mood_keywords": saved["mood_keywords"],
        "created_at": saved["created_at"],
        "path": saved["path"],
        "brief": saved["brief"],
        "next_step_hint": (
            f"use_catalog_brief(deck_url=<other_deck>, brief_id={saved['id']!r}) "
            "to apply this saved brief to another deck."
        ),
    }


def use_catalog_brief(
    deck_url: str,
    brief_id: str,
) -> dict[str, Any]:
    """Copy a brief from the catalog into a deck's meta-slide.

    Wraps ``catalog.load_brief`` → ``set_theme_brief``. Does NOT repaint
    existing slides — pair with ``apply_brief_and_restyle(deck_url,
    brief=result['brief'], confirm_destructive=True)`` if you also want a
    full repaint.

    Raises FileNotFoundError if ``brief_id`` isn't in the catalog. Call
    ``list_catalog_briefs()`` first to see available ids.

    Returns {deck_id, brief_id, name, brief, action, slide_id,
    next_step_hint}.
    """
    entry = catalog_mod.load_brief(brief_id)
    brief = entry["brief"]

    ok, errors = theme_brief_mod.validate_brief(brief)
    if not ok:
        raise ValueError(
            f"catalog entry {brief_id!r} contains an invalid brief: "
            + "; ".join(errors)
        )

    result = set_theme_brief(deck_url, brief)
    return {
        "deck_id": result["deck_id"],
        "brief_id": brief_id,
        "name": entry.get("name"),
        "brief": brief,
        "action": result["action"],
        "slide_id": result["slide_id"],
        "next_step_hint": (
            "brief committed to meta-slide but existing slides are NOT "
            "repainted. Call apply_brief_and_restyle(deck_url, "
            f"brief={brief!r}, confirm_destructive=True) to repaint."
            if brief else
            "brief committed to meta-slide"
        ),
    }


@mcp.tool()
def export_brief(deck_url: str) -> dict[str, Any]:
    """Export the deck's active brief as a portable YAML string + dict.

    Useful for sharing briefs out-of-band (pasting into a review doc,
    emailing a client, committing a client-specific brief to a VCS repo
    separate from slides-mcp). Round-trips cleanly with ``import_brief``.

    Returns {deck_id, brief, brief_yaml, source_slide_id}.

    Raises FileNotFoundError if the deck has no meta-slide. Brownfield flow:
    extract_theme_brief + set_theme_brief first.
    """
    deck_id = slides_api.deck_id_from_url(deck_url)
    prez = _fetch_for_brief(deck_id)
    meta = theme_brief_mod.find_meta_slide(prez)
    if meta is None:
        raise FileNotFoundError(
            "deck has no theme-brief meta-slide; nothing to export. "
            "Brownfield path: extract_theme_brief + set_theme_brief first."
        )
    brief = theme_brief_mod.parse_brief_body(meta["body_text"])
    if brief is None:
        raise FileNotFoundError(
            "meta-slide found but body did not parse as a brief"
        )
    brief_yaml = yaml.safe_dump(brief, sort_keys=False, allow_unicode=True)
    return {
        "deck_id": deck_id,
        "brief": brief,
        "brief_yaml": brief_yaml,
        "source_slide_id": meta["slide_id"],
    }


def import_brief(
    deck_url: str,
    yaml_source: str,
    is_path: bool = False,
) -> dict[str, Any]:
    """Import a brief from a YAML string (or file path) and commit it to
    the deck's meta-slide via ``set_theme_brief``.

    ``yaml_source``: the YAML content itself (when ``is_path=False``) OR a
      filesystem path to read from (when ``is_path=True``).

    Accepts either a bare brief dict OR an envelope shaped like the catalog
    (``{brief: {...}, ...}``) — the ``brief`` key is extracted transparently.

    Does NOT repaint existing slides. Pair with ``apply_brief_and_restyle``
    if you want to repaint.

    Returns {deck_id, brief, action, slide_id, source, next_step_hint}.

    Raises:
      - FileNotFoundError if ``is_path=True`` and the path is missing.
      - ValueError if the YAML fails to parse or the final brief fails
        ``validate_brief``.
    """
    if is_path:
        path = Path(yaml_source).expanduser()
        if not path.exists():
            raise FileNotFoundError(f"brief YAML file not found: {path}")
        yaml_text = path.read_text()
    else:
        yaml_text = yaml_source

    try:
        parsed = yaml.safe_load(yaml_text)
    except yaml.YAMLError as e:
        raise ValueError(f"yaml_source is not valid YAML: {e}") from e

    if not isinstance(parsed, dict):
        raise ValueError(
            "yaml_source must parse to a dict (a brief or an envelope "
            f"with a `brief` key); got {type(parsed).__name__}"
        )
    # Envelope (catalog shape) or bare brief?
    if "brief" in parsed and isinstance(parsed["brief"], dict):
        brief = parsed["brief"]
    else:
        brief = parsed

    ok, errors = theme_brief_mod.validate_brief(brief)
    if not ok:
        raise ValueError(
            "imported brief failed validation: " + "; ".join(errors)
        )

    result = set_theme_brief(deck_url, brief)
    return {
        "deck_id": result["deck_id"],
        "brief": brief,
        "action": result["action"],
        "slide_id": result["slide_id"],
        "source": "path" if is_path else "string",
        "next_step_hint": (
            "brief committed to meta-slide but existing slides are NOT "
            "repainted. Call apply_brief_and_restyle(deck_url, "
            "brief=..., confirm_destructive=True) to repaint."
        ),
    }


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


# ---------------------------------------------------------------------------
# plan_deck + theme_swap (v0.11.0) — deck-level narrative + client-ready clone
# ---------------------------------------------------------------------------

_SECTION_OPENER_ARCHETYPES: frozenset[str] = frozenset({
    "section_opener",
    "section_divider",
    "cover_with_hero",
})


def _get_deck_outline_for_plan(deck_url: str) -> list[dict[str, Any]]:
    """Thin wrapper reusing get_deck_outline; returns its slides list."""
    outline = get_deck_outline(deck_url)
    return outline.get("slides", [])


def _plan_from_outline(outline_slides: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a deck plan from an existing deck outline.

    Sections are inferred via section_opener-style archetype transitions; when
    absent, all slides fall into a single implicit section so callers still
    get a usable `plan.slides` list for hand-editing.
    """
    slides_payload: list[dict[str, Any]] = [
        {
            "id": s.get("slide_id"),
            "intent": s.get("title") or "",
            "archetype_hint": s.get("archetype", "generic_layout"),
        }
        for s in outline_slides
    ]

    sections: list[dict[str, Any]] = []
    current_section: dict[str, Any] | None = None
    for s in outline_slides:
        arch = s.get("archetype", "generic_layout")
        sid = s.get("slide_id")
        title = s.get("title") or ""
        if arch in _SECTION_OPENER_ARCHETYPES or current_section is None:
            if current_section is not None:
                sections.append(current_section)
            current_section = {
                "id": f"section_{len(sections)}",
                "title": title or arch,
                "slide_ids": [sid] if sid else [],
            }
        else:
            if sid:
                current_section["slide_ids"].append(sid)
    if current_section is not None:
        sections.append(current_section)

    return {
        "vision": "",
        "arc": "",
        "sections": sections,
        "slides": slides_payload,
        "worklog": [],
    }


_HEADER_RE = __import__("re").compile(r"^(#{1,2})\s+(.+?)\s*$")


def _plan_from_doc(doc_path: str) -> tuple[dict[str, Any], list[str]]:
    """Parse a markdown doc into a plan. H1 = vision; H2 = sections/slides.

    Returns (plan, warnings). Raises FileNotFoundError when doc_path is missing.
    """
    warnings: list[str] = []
    path = Path(doc_path).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"doc_path not found: {path}")
    text = path.read_text(encoding="utf-8", errors="replace")

    vision_parts: list[str] = []
    sections: list[dict[str, Any]] = []
    slides: list[dict[str, Any]] = []
    for line in text.splitlines():
        m = _HEADER_RE.match(line)
        if not m:
            continue
        level = len(m.group(1))
        title = m.group(2).strip()
        if level == 1:
            vision_parts.append(title)
        elif level == 2:
            section_id = f"section_{len(sections)}"
            slide_id = f"slide_{len(slides)}"
            sections.append({
                "id": section_id,
                "title": title,
                "slide_ids": [slide_id],
            })
            slides.append({
                "id": slide_id,
                "intent": title,
                "archetype_hint": "text_heavy_body",
            })
    vision = " — ".join(vision_parts) if vision_parts else ""
    if not sections:
        warnings.append(
            f"no H2 headers found in {path}; plan.sections + plan.slides are empty"
        )
    plan = {
        "vision": vision,
        "arc": vision,
        "sections": sections,
        "slides": slides,
        "worklog": [],
    }
    return plan, warnings


@mcp.tool()
def plan_deck(
    deck_url: str,
    intent: str = "",
    source: str = "free_text",
    doc_path: str | None = None,
    commit: bool = False,
) -> dict[str, Any]:
    """Propose a deck-level narrative plan (vision + sections + slides + worklog).

    Plan is the deck-level gsd-lite footprint — it captures the WHY of the
    deck (vision + arc), the WHAT (sections + slides), and the decision
    trail (worklog) so a future agent or reviewer can reconstruct intent
    without re-reading every slide. Stored on the meta-slide alongside the
    visual brief when committed (Decision R/S — single DNA channel).

    Pure proposal by default. When `commit=True`, merges the proposed plan
    into the deck's theme brief via ``write_theme_brief(mode='merge',
    delta={'plan': plan})``. Requires an existing meta-slide — if absent,
    returns ``status='proposed'`` with a warning pointing to
    ``write_theme_brief(mode='scaffold')``.

    source modes:
      - ``free_text`` (default): parse ``intent`` as the deck vision. Emits
        empty sections/slides; caller fills in structure later.
        Confidence: ``low``.
      - ``brownfield_deck``: read the existing slide outline, group slides
        into sections via section_opener-style archetype transitions, and
        emit per-slide intents from titles. Confidence: ``medium``.
      - ``doc``: read markdown at ``doc_path``; H1 headers join into the
        vision string, H2 headers become sections + slide intents.
        Confidence: ``medium`` when ≥2 sections found, else ``low``.

    Returns ``{deck_id, status, plan, proposal_source, confidence,
    next_step_hint, warnings}``.

    Raises ValueError on unknown ``source``, missing ``doc_path`` when
    ``source='doc'``. Raises FileNotFoundError when ``doc_path`` doesn't
    exist.
    """
    if source not in ("free_text", "brownfield_deck", "doc"):
        raise ValueError(
            f"source must be one of free_text|brownfield_deck|doc; got {source!r}"
        )

    deck_id = slides_api.deck_id_from_url(deck_url)
    warnings: list[str] = []
    confidence: str

    if source == "free_text":
        arc = intent if len(intent) <= 80 else intent[:80] + "..."
        plan: dict[str, Any] = {
            "vision": intent,
            "arc": arc,
            "sections": [],
            "slides": [],
            "worklog": [],
        }
        confidence = "low"
    elif source == "brownfield_deck":
        outline_slides = _get_deck_outline_for_plan(deck_url)
        plan = _plan_from_outline(outline_slides)
        if intent:
            plan["vision"] = intent
            plan["arc"] = intent if len(intent) <= 80 else intent[:80] + "..."
        confidence = "medium"
    else:  # doc
        if not doc_path:
            raise ValueError("source='doc' requires doc_path")
        plan, doc_warnings = _plan_from_doc(doc_path)
        warnings.extend(doc_warnings)
        if intent and not plan["vision"]:
            plan["vision"] = intent
            plan["arc"] = intent if len(intent) <= 80 else intent[:80] + "..."
        confidence = "medium" if len(plan.get("sections") or []) >= 2 else "low"

    status = "proposed"
    if commit:
        try:
            write_theme_brief(
                deck_url=deck_url, mode="merge", delta={"plan": plan}
            )
            status = "committed"
        except FileNotFoundError:
            warnings.append(
                "no meta brief to merge into — commit via "
                "write_theme_brief(mode='scaffold') first, then retry"
            )

    if status == "committed":
        next_step_hint = (
            "plan merged onto meta-slide; call get_theme_brief(deck_url) to verify"
        )
    elif commit and status == "proposed":
        next_step_hint = (
            "plan NOT committed (no meta-slide). Run "
            "write_theme_brief(mode='scaffold') then re-call plan_deck(..., commit=True)"
        )
    else:
        next_step_hint = (
            "review proposed plan; re-call plan_deck(..., commit=True) to merge "
            "onto the meta-slide. The plan can be edited freely before committing."
        )

    return {
        "deck_id": deck_id,
        "status": status,
        "plan": plan,
        "proposal_source": source,
        "confidence": confidence,
        "next_step_hint": next_step_hint,
        "warnings": warnings,
    }


@mcp.tool()
def theme_swap(
    source_deck_url: str,
    new_title: str,
    target_brief: dict[str, Any] | None = None,
    target_brief_delta: dict[str, Any] | None = None,
    asset_overrides: dict[str, str] | None = None,
    confirm_destructive: bool = False,
) -> dict[str, Any]:
    """Clone a source deck, apply a target brief, swap brand assets → client-ready deck.

    The client-ready-deck ceremony collapsed into one call:

      1. Read source deck's brief — must have ``brand_assets`` to swap.
      2. ``clone_deck(source_deck_url, new_title)`` → new deck.
      3. If ``target_brief`` or ``target_brief_delta`` is set: apply via
         ``apply_brief_and_restyle`` on the new deck (retroactive repaint).
      4. For each ``(asset_id, new_value)`` in ``asset_overrides``:
           - find the matching brand_asset in the source brief by ``id``
           - if ``asset.type == 'text'``: emit ``replaceAllText`` with
             ``containsText.text == asset.match`` and ``replaceText == new_value``
           - if ``asset.type == 'image'``: emit ``replaceImage`` for the
             shape whose objectId == ``asset.match``, with ``url=new_value`` +
             ``imageReplaceMethod='CENTER_INSIDE'``
      5. Update the new deck's brief ``brand_assets`` list so each swapped
         asset's ``match`` reflects the new value (text-mode only; image-mode
         keeps the same shape objectId).

    Pass **exactly one** of ``target_brief`` (wholesale) or
    ``target_brief_delta`` (deep-merge). Both may be ``None`` for an
    asset-swap-only ceremony with no restyle.

    ``confirm_destructive=True`` is required — clone + restyle combined are
    non-reversible from the user's POV (it's a new deck + overwritten styles).

    Args:
        source_deck_url: the deck to clone. Must have a brief with
            ``brand_assets`` (even if empty when no swap is needed — the
            field presence signals intent).
        new_title: the new deck's title.
        target_brief: full brief dict to apply on the clone. Mutually
            exclusive with ``target_brief_delta``.
        target_brief_delta: partial changes to deep-merge onto the source
            brief. Mutually exclusive with ``target_brief``.
        asset_overrides: ``{asset_id: new_value}``. Unknown asset ids
            surface as warnings (skip, don't raise). Empty/None = no swaps.
        confirm_destructive: required True.

    Returns::

        {
            new_deck_url, new_deck_id, source_deck_id,
            assets_swapped: [{id, type, old_match, new_value}, ...],
            restyle_applied: bool,
            warnings: [...],
        }

    Raises:
        ValueError if both ``target_brief`` and ``target_brief_delta`` are
            set, or if ``confirm_destructive`` is False, or if the source
            deck has no brief / no brand_assets attribute.
    """
    if not confirm_destructive:
        raise ValueError(
            "theme_swap clones + repaints + swaps brand assets — "
            "non-reversible. Re-invoke with confirm_destructive=True."
        )
    if target_brief is not None and target_brief_delta is not None:
        raise ValueError(
            "pass at most one of target_brief (wholesale) or "
            "target_brief_delta (merge) — not both"
        )

    # --- Step 1: read source brief --------------------------------------
    src_id = slides_api.deck_id_from_url(source_deck_url)
    src_prez = _fetch_for_brief(src_id)
    src_meta = theme_brief_mod.find_meta_slide(src_prez)
    if src_meta is None:
        raise ValueError(
            "source deck has no brief; run scaffold (write_theme_brief(mode='scaffold')) "
            "or set_theme_brief first"
        )
    src_brief = theme_brief_mod.parse_brief_body(src_meta["body_text"])
    if src_brief is None:
        raise ValueError(
            "source deck meta-slide found but body did not parse as a brief"
        )
    src_brand_assets = src_brief.get("brand_assets")
    if src_brand_assets is None:
        raise ValueError(
            "source deck brief has no brand_assets field; nothing to swap. "
            "Add brand_assets via write_theme_brief(mode='merge', delta=...) first."
        )

    warnings: list[str] = []

    # --- Step 2: clone deck ---------------------------------------------
    clone_result = clone_deck(src_url=source_deck_url, new_title=new_title)
    new_deck_id = clone_result["new_deck_id"]
    new_deck_url = clone_result["new_deck_url"]

    # --- Step 3: apply brief (optional) ---------------------------------
    restyle_applied = False
    if target_brief is not None:
        apply_brief_and_restyle(
            deck_url=new_deck_url,
            brief=target_brief,
            confirm_destructive=True,
        )
        restyle_applied = True
    elif target_brief_delta is not None:
        apply_brief_and_restyle(
            deck_url=new_deck_url,
            delta=target_brief_delta,
            confirm_destructive=True,
        )
        restyle_applied = True

    # --- Step 4: brand-asset swaps --------------------------------------
    asset_index = {a.get("id"): a for a in src_brand_assets if isinstance(a, dict)}
    swap_records: list[dict[str, Any]] = []
    text_requests: list[dict[str, Any]] = []
    image_requests: list[dict[str, Any]] = []
    new_brand_assets_delta: dict[str, str] = {}

    for asset_id, new_value in (asset_overrides or {}).items():
        asset = asset_index.get(asset_id)
        if asset is None:
            warnings.append(
                f"asset_overrides[{asset_id!r}] not in source brand_assets — skipped"
            )
            continue
        atype = asset.get("type")
        match = asset.get("match")
        if atype == "text":
            text_requests.append({
                "replaceAllText": {
                    "containsText": {"text": match, "matchCase": True},
                    "replaceText": new_value,
                }
            })
            swap_records.append({
                "id": asset_id, "type": "text",
                "old_match": match, "new_value": new_value,
            })
            new_brand_assets_delta[asset_id] = new_value
        elif atype == "image":
            image_requests.append({
                "replaceImage": {
                    "imageObjectId": match,
                    "url": new_value,
                    "imageReplaceMethod": "CENTER_INSIDE",
                }
            })
            swap_records.append({
                "id": asset_id, "type": "image",
                "old_match": match, "new_value": new_value,
            })
        else:
            warnings.append(
                f"asset_overrides[{asset_id!r}] has unknown type {atype!r} — skipped"
            )

    if text_requests:
        slides_api.batch_update(new_deck_id, text_requests)
    if image_requests:
        slides_api.batch_update(new_deck_id, image_requests)

    # --- Step 5: update new-deck brief brand_assets (text swaps only) ---
    if new_brand_assets_delta:
        try:
            new_prez = _fetch_for_brief(new_deck_id)
            new_meta = theme_brief_mod.find_meta_slide(new_prez)
            if new_meta is not None:
                new_brief_current = theme_brief_mod.parse_brief_body(
                    new_meta["body_text"]
                )
                if new_brief_current is not None:
                    updated_assets = []
                    for asset in new_brief_current.get("brand_assets") or []:
                        if isinstance(asset, dict) and asset.get("id") in new_brand_assets_delta:
                            asset_copy = dict(asset)
                            if asset.get("type") == "text":
                                asset_copy["match"] = new_brand_assets_delta[
                                    asset["id"]
                                ]
                            updated_assets.append(asset_copy)
                        else:
                            updated_assets.append(asset)
                    write_theme_brief(
                        deck_url=new_deck_url,
                        mode="merge",
                        delta={"brand_assets": updated_assets},
                    )
        except Exception as e:  # noqa: BLE001 — best-effort brief refresh
            warnings.append(f"brand_assets brief refresh on new deck failed: {e}")

    return {
        "source_deck_id": src_id,
        "new_deck_id": new_deck_id,
        "new_deck_url": new_deck_url,
        "assets_swapped": swap_records,
        "restyle_applied": restyle_applied,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Dispatcher tools (v0.9+): collapsed MCP tool surface
#
# Each dispatcher is ONE @mcp.tool() whose `kind` / `mode` / `op` argument
# fans out to a family of underlying library functions (still callable
# directly from tests + internal helpers). Keeps the MCP tool catalog small
# while preserving the function-level API for Python consumers.
# ---------------------------------------------------------------------------


@mcp.tool()
def list_registry(
    kind: str,
    filter: str | None = None,
    deck_url: str | None = None,
) -> dict[str, Any]:
    """Browse slides-mcp registries. Dispatches by `kind`:

      - "themes"         → list_themes()                          (no params)
      - "archetypes"     → list_archetypes()                      (no params)
      - "icons"          → list_icons(filter_keyword=filter)      (optional filter)
      - "font_pairings"  → list_font_pairings(mood=filter)        (optional filter)
      - "catalog_briefs" → list_catalog_briefs(mood=filter)       (optional filter)
      - "deck_layouts"   → list_deck_layouts(deck_url)            (REQUIRES deck_url)

    For structural grep across one deck's slides (archetype + contains_text
    filters) use `list_slides_by` — its multi-filter shape doesn't fit this
    dispatcher.

    Raises ValueError when required params are missing or the kind is unknown.
    """
    if kind == "themes":
        return list_themes()
    if kind == "archetypes":
        return list_archetypes()
    if kind == "icons":
        return list_icons(filter_keyword=filter)
    if kind == "font_pairings":
        return list_font_pairings(mood=filter)
    if kind == "catalog_briefs":
        return list_catalog_briefs(mood=filter)
    if kind == "deck_layouts":
        if not deck_url:
            raise ValueError("list_registry(kind='deck_layouts') requires deck_url")
        return list_deck_layouts(deck_url)
    raise ValueError(
        f"Unknown list kind: {kind!r}. "
        "Expected one of: themes, archetypes, icons, font_pairings, "
        "catalog_briefs, deck_layouts"
    )


@mcp.tool()
def write_theme_brief(
    deck_url: str,
    mode: str,
    brief: dict[str, Any] | None = None,
    delta: dict[str, Any] | None = None,
    yaml_source: str | None = None,
    is_path: bool = False,
    auto_commit_if_high_confidence: bool = False,
) -> dict[str, Any]:
    """Write or amend the deck's theme-brief meta-slide. Dispatches by `mode`:

      - "replace"  → set_theme_brief(deck_url, brief)                    REQUIRES brief
      - "merge"    → update_theme_brief(deck_url, changes=delta)          REQUIRES delta
      - "scaffold" → scaffold_meta_brief(deck_url, auto_commit_if_high_confidence)
                   (brownfield one-shot: detects/proposes/optionally commits)
      - "import"   → import_brief(deck_url, yaml_source, is_path)         REQUIRES yaml_source

    For a destructive one-call "commit + repaint every existing slide"
    ceremony use `apply_brief_and_restyle` — it's kept separate because the
    surface semantics are different (confirm_destructive gate, restyle return).

    Raises ValueError when required per-mode params are missing or the mode
    is unknown.
    """
    if mode == "replace":
        if brief is None:
            raise ValueError("write_theme_brief(mode='replace') requires brief")
        return set_theme_brief(deck_url, brief)
    if mode == "merge":
        if delta is None:
            raise ValueError("write_theme_brief(mode='merge') requires delta")
        return update_theme_brief(deck_url, changes=delta)
    if mode == "scaffold":
        return scaffold_meta_brief(
            deck_url,
            auto_commit_if_high_confidence=auto_commit_if_high_confidence,
        )
    if mode == "import":
        if yaml_source is None:
            raise ValueError("write_theme_brief(mode='import') requires yaml_source")
        return import_brief(deck_url, yaml_source=yaml_source, is_path=is_path)
    raise ValueError(
        f"Unknown write mode: {mode!r}. "
        "Expected one of: replace, merge, scaffold, import"
    )


@mcp.tool()
def audit(
    deck_url: str,
    kind: str,
    slide_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Deck-level audits. Dispatches by `kind`:

      - "colors"          → audit_deck_colors(deck_url)
      - "typography"      → audit_typography(deck_url)
      - "brief_coherence" → audit_brief_coherence(deck_url, slide_ids=slide_ids)

    `slide_ids` is only meaningful for brief_coherence (to score a freshly-
    generated batch without legacy drift polluting the composite).

    Raises ValueError for unknown kind.
    """
    if kind == "colors":
        return audit_deck_colors(deck_url)
    if kind == "typography":
        return audit_typography(deck_url)
    if kind == "brief_coherence":
        return audit_brief_coherence(deck_url, slide_ids=slide_ids)
    raise ValueError(
        f"Unknown audit kind: {kind!r}. "
        "Expected one of: colors, typography, brief_coherence"
    )


@mcp.tool()
def update_text(
    deck_url: str,
    slide_id: str,
    object_id: str,
    scope: str,
    style: dict[str, Any],
    range: dict[str, Any] | str | None = None,
    verify: str = "auto",
) -> dict[str, Any]:
    """Apply text-level styling to a range inside a shape. Dispatches by `scope`:

      - "run"       → update_text_style      (bold/italic/color/size/font/...)
      - "paragraph" → update_paragraph_style (alignment/indent/line spacing/...)

    Shared range language: None or "all", {"paragraph": N}, {"chars": [s, e]},
    or {"match": "unique_substring"}.

    Raises ValueError for unknown scope.
    """
    if scope == "run":
        return update_text_style(
            deck_url, slide_id, object_id, style, range=range, verify=verify,
        )
    if scope == "paragraph":
        return update_paragraph_style(
            deck_url, slide_id, object_id, style, range=range, verify=verify,
        )
    raise ValueError(
        f"Unknown text scope: {scope!r}. Expected one of: run, paragraph"
    )


@mcp.tool()
def preview(
    kind: str,
    brief: dict[str, Any] | None = None,
    briefs: list[dict[str, Any]] | None = None,
    deck_url: str | None = None,
    slide_ids: list[str] | None = None,
    variant_id: str | None = None,
    archetype: str | None = None,
    content: dict[str, Any] | None = None,
    title: str | None = None,
    thumbnail_size: str = "SMALL",
    max_slides: int = 36,
) -> Image:
    """Zero-write preview primitives. All return MCP ImageContent (PNG).
    Dispatches by `kind`:

      - "brief_swatch"       → render_brief_swatch(brief)           REQUIRES brief
      - "brief_swatch_grid"  → render_brief_swatch_grid(briefs)     REQUIRES briefs
      - "deck_contact_sheet" → render_deck_contact_sheet(deck_url,  REQUIRES deck_url
                                   slide_ids, variant_id, title,
                                   thumbnail_size, max_slides)
      - "archetype"          → preview_archetype(archetype, content, brief)
                                   REQUIRES archetype + content

    For Slides-API-backed thumbnails (real rendered slide) use `render_thumbnail`;
    for WRITING sample slides into the deck under a candidate brief (human-eye
    approval gate) use `tweak_brief(preview='slides', ...)`.

    Raises ValueError when per-kind required params are missing or the kind
    is unknown.
    """
    if kind == "brief_swatch":
        if brief is None:
            raise ValueError("preview(kind='brief_swatch') requires brief")
        return render_brief_swatch(brief)
    if kind == "brief_swatch_grid":
        if not briefs:
            raise ValueError("preview(kind='brief_swatch_grid') requires briefs")
        return render_brief_swatch_grid(briefs)
    if kind == "deck_contact_sheet":
        if not deck_url:
            raise ValueError("preview(kind='deck_contact_sheet') requires deck_url")
        return render_deck_contact_sheet(
            deck_url,
            slide_ids=slide_ids,
            variant_id=variant_id,
            title=title,
            thumbnail_size=thumbnail_size,
            max_slides=max_slides,
        )
    if kind == "archetype":
        if not archetype or content is None:
            raise ValueError(
                "preview(kind='archetype') requires archetype + content"
            )
        return preview_archetype(archetype, content, brief)
    raise ValueError(
        f"Unknown preview kind: {kind!r}. "
        "Expected one of: brief_swatch, brief_swatch_grid, "
        "deck_contact_sheet, archetype"
    )


@mcp.tool()
def render_thumbnail(
    deck_url: str,
    slide_id: str,
    size: str = "MEDIUM",
    mode: str = "bytes",
) -> Any:
    """Render a slide as a thumbnail. Dispatches by `mode`:

      - "bytes" (default) → MCP ImageContent (PNG bytes, no URL expiry)
      - "url"             → {deck_id, slide_id, thumbnail_url, size}
                            short-lived contentUrl (no image bytes)

    Prefer bytes for the bidi agent vision loop; url when a URL is enough
    (embedding in a report, non-agent caller).

    Raises ValueError for unknown mode.
    """
    if mode == "bytes":
        return _render_thumbnail_bytes(deck_url, slide_id, size=size)
    if mode == "url":
        return _render_thumbnail_url(deck_url, slide_id, size=size)
    raise ValueError(
        f"Unknown render_thumbnail mode: {mode!r}. Expected one of: bytes, url"
    )


@mcp.tool()
def tweak_brief(
    deck_url: str,
    directive: str,
    preview: str = "none",
    candidate_brief: dict[str, Any] | None = None,
    compare_to_current: bool = True,
    sample_content: list[dict[str, Any]] | None = None,
    variant_prefix: str = "tweak_preview",
) -> dict[str, Any]:
    """Natural-language directive → brief-delta + validated candidate. Dispatches
    by `preview`:

      - "none" (default) → compute-only. Returns {delta, candidate_brief,
                            matched_axes, unresolved_terms, confidence,
                            rationale, ...}. No deck writes.
      - "slides"         → preview_brief_tweak: writes 2-4 sample slides into
                            the deck under `candidate_brief` (defaulting to
                            the computed candidate when omitted) so the HUMAN
                            opens Google Slides and picks. Meta is restored
                            at the end.

    Params consumed when preview='slides': `candidate_brief` (falls back to
    computed candidate when omitted), `compare_to_current`, `sample_content`,
    `variant_prefix`. All are ignored when preview='none'.

    For a one-call commit + repaint once the human approves, see
    `apply_brief_and_restyle`.

    Raises ValueError for unknown preview mode.
    """
    if preview == "none":
        return _tweak_brief_compute(deck_url, directive)
    if preview == "slides":
        # Compute first so caller gets back both delta + preview results.
        computed = _tweak_brief_compute(deck_url, directive)
        effective_candidate = (
            candidate_brief
            if candidate_brief is not None
            else computed["candidate_brief"]
        )
        preview_result = preview_brief_tweak(
            deck_url,
            candidate_brief=effective_candidate,
            sample_content=sample_content,
            compare_to_current=compare_to_current,
            variant_prefix=variant_prefix,
        )
        return {
            "tweak": computed,
            "preview": preview_result,
        }
    raise ValueError(
        f"Unknown tweak_brief preview mode: {preview!r}. "
        "Expected one of: none, slides"
    )


@mcp.tool()
def catalog_brief(
    op: str,
    deck_url: str | None = None,
    brief_id: str | None = None,
    name: str | None = None,
    mood_keywords: list[str] | None = None,
    brief: dict[str, Any] | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Catalog (portable user-owned brief library) ops. Dispatches by `op`:

      - "save" → save_brief_to_catalog(deck_url, name, mood_keywords,
                  brief_id, brief, overwrite)
                  REQUIRES name; deck_url used when brief is omitted
                  (reads active deck's brief).
      - "use"  → use_catalog_brief(deck_url, brief_id)
                  REQUIRES deck_url + brief_id.

    To browse the library use `list_registry(kind='catalog_briefs', filter=mood)`.

    Raises ValueError when required per-op params are missing or the op is
    unknown.
    """
    if op == "save":
        if not name:
            raise ValueError("catalog_brief(op='save') requires name")
        if deck_url is None and brief is None:
            raise ValueError(
                "catalog_brief(op='save') requires deck_url (to read brief "
                "from deck) or an explicit brief="
            )
        return save_brief_to_catalog(
            deck_url=deck_url or "",
            name=name,
            mood_keywords=mood_keywords,
            brief_id=brief_id,
            brief=brief,
            overwrite=overwrite,
        )
    if op == "use":
        if not deck_url or not brief_id:
            raise ValueError("catalog_brief(op='use') requires deck_url + brief_id")
        return use_catalog_brief(deck_url, brief_id)
    raise ValueError(
        f"Unknown catalog op: {op!r}. Expected one of: save, use"
    )


def main() -> None:
    """Entry point for `slides-mcp` console script."""
    mcp.run()


if __name__ == "__main__":
    main()
