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
from typing import Any

import yaml
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.utilities.types import Image

from . import archetypes as archetype_reg
from . import audit as audit_mod
from . import auth, slides_api
from . import classify as classify_mod
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
        title_shape = next(
            (s for s in normalize_mod.flatten(shapes)
             if s.kind == "text" and s.text and s.top_in < 2.5),
            None,
        )
        slides.append({
            "slide_id": slide["objectId"],
            "archetype": archetype,
            "title": title_shape.text.strip()[:100] if title_shape and title_shape.text else "",
            "element_count": len(normalize_mod.flatten(shapes)),
        })
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
    counts = Counter(s["archetype"] for s in outline["slides"])
    return {
        "deck_id": outline["deck_id"],
        "layouts": [
            {"archetype": a, "count": c, "slide_ids": [
                s["slide_id"] for s in outline["slides"] if s["archetype"] == a
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
    notes = normalize_mod.extract_notes_text(page)
    archetype = classify_mod.classify(shapes)
    old_dsl = projection_mod.project(
        shapes, archetype, slide_id, notes, sub,
        mode="clean", include_elements=True,
    )

    new_dsl = yaml.safe_load(new_dsl_yaml) or {}
    result = diff_mod.diff_slide(old_dsl, new_dsl, slide_id=slide_id)

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


@mcp.tool()
def clone_deck(src_url: str, new_title: str) -> dict[str, Any]:
    """Copy a deck via Drive. Returns new deck ID + a URL hint."""
    src_id = slides_api.deck_id_from_url(src_url)
    new_id = slides_api.copy_deck(src_id, new_title)
    return {
        "src_deck_id": src_id,
        "new_deck_id": new_id,
        "new_deck_url": f"https://docs.google.com/presentation/d/{new_id}/edit",
    }


def main() -> None:
    """Entry point for `slides-mcp` console script."""
    mcp.run()


if __name__ == "__main__":
    main()
