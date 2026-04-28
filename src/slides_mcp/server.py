"""FastMCP server: Google Slides read-only context primitives.

v2 vision: minimal, token-efficient READ surface. Mirrors `read_files`
philosophy — one primary tool with rich modes, plus narrow companions.

Tool surface (5 tools):
  auth_status()                                       — token health
  get_deck_outline(deck_url)                          — ~20 tok/slide index
  read_slides(deck_url, slides?, detail?, ...)        — primary read primitive
  search_deck(deck_url, query, slides?)               — substring/regex search
  render_thumbnail(deck_url, slide_id, size?)         — rendered PNG (one slide)

See README and skills/slides-mcp/SKILL.md for usage patterns.
"""
from __future__ import annotations

import re
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.utilities.types import Image

from . import auth, classify, normalize, projection, slides_api

mcp = FastMCP("slides-mcp")

Detail = Literal["outline", "summary", "full", "raw"]
ImageMode = Literal["ref", "none"]
ThumbSize = Literal["SMALL", "MEDIUM", "LARGE"]


# ---- helpers --------------------------------------------------------


def _resolve_slide_ids(prez: dict[str, Any], selector: Any) -> list[str]:
    """Convert flexible selector → ordered list of slide objectIds.

    Accepts:
      None                    → all slides (in deck order)
      slide_id string         → ['<id>']
      "3-7"                   → 1-indexed inclusive range
      ["id1", "id2"]          → list of objectIds
      [1, 3, 5]               → list of 1-indexed positions
      {"first": N}            → first N slides
      {"last": N}             → last N slides
      {"with_notes": true}    → slides with non-empty speaker notes
      {"with_image": true}    → slides containing at least one picture
    """
    all_slides = prez.get("slides", []) or []
    all_ids = [s["objectId"] for s in all_slides]

    if selector is None:
        return all_ids

    if isinstance(selector, str):
        if "-" in selector and selector.replace("-", "").isdigit():
            a, b = selector.split("-", 1)
            lo = max(0, int(a) - 1)
            hi = int(b)
            return all_ids[lo:hi]
        if selector in all_ids:
            return [selector]
        raise ValueError(f"Unknown slide_id: {selector!r}")

    if isinstance(selector, list):
        out: list[str] = []
        for s in selector:
            if isinstance(s, int):
                if 1 <= s <= len(all_ids):
                    out.append(all_ids[s - 1])
                else:
                    raise ValueError(f"slide index out of range: {s}")
            elif isinstance(s, str) and s in all_ids:
                out.append(s)
            else:
                raise ValueError(f"unknown slide selector entry: {s!r}")
        return out

    if isinstance(selector, dict):
        if "first" in selector:
            return all_ids[: int(selector["first"])]
        if "last" in selector:
            n = int(selector["last"])
            return all_ids[-n:] if n else []
        if "hidden" in selector:
            want = bool(selector["hidden"])
            return [
                s["objectId"] for s in all_slides
                if normalize.is_hidden(s) == want
            ]
        if selector.get("with_notes"):
            return [
                s["objectId"]
                for s in all_slides
                if normalize.extract_notes_text(s)
            ]
        if selector.get("with_image"):
            out2: list[str] = []
            for s in all_slides:
                shapes = normalize.normalize_page(s)
                if any(x.kind == "picture" for x in normalize.flatten(shapes)):
                    out2.append(s["objectId"])
            return out2

    raise ValueError(f"unsupported slide selector: {selector!r}")


# ---- tools ----------------------------------------------------------


@mcp.tool()
def auth_status() -> dict[str, Any]:
    """Diagnostic: report token.json state without exposing secrets."""
    return auth.credentials_info()


@mcp.tool()
def get_deck_outline(deck_url: str) -> dict[str, Any]:
    """Cheap whole-deck index. ~20 tok/slide. First call on any new deck.

    Returns metadata sufficient to decide which slides to drill into next:
      {deck_id, title, slide_count,
       slides: [{slide_id, title, archetype, has_notes, has_image,
                 element_count}]}

    `archetype` is a topology-derived label from `classify.classify` —
    e.g. "3col_pill_cards", "cover_with_hero", "text_heavy_body",
    "text_left_image_right", "4_col_numbered_flow", "table_slide",
    "logo_strip", or "generic_layout" when no pattern matched. Useful as a
    visual-structure signal: if you need to read a hero slide, you can pick
    `cover_with_hero`; if you need data, pick `table_slide` etc.
    """
    deck_id = slides_api.deck_id_from_url(deck_url)
    prez = slides_api.get_presentation(deck_id)
    slides_out: list[dict[str, Any]] = []
    for idx, slide in enumerate(prez.get("slides", []), start=1):
        shapes = normalize.normalize_page(slide)
        notes = normalize.extract_notes_text(slide)
        archetype = classify.classify(shapes)
        slides_out.append(
            projection.project(
                slide["objectId"], shapes, archetype, notes,
                detail="outline",
                position=idx,
                hidden=normalize.is_hidden(slide),
                layout_id=normalize.layout_id(slide),
            )
        )
    return {
        "deck_id": deck_id,
        "title": prez.get("title", ""),
        "slide_count": len(slides_out),
        "slides": slides_out,
    }


@mcp.tool()
def read_slides(
    deck_url: str,
    slides: Any = None,
    detail: Detail = "summary",
    include_notes: bool = True,
    include_images: ImageMode = "ref",
) -> dict[str, Any]:
    """Read one or more slides at the requested level of detail.

    Mirrors the `read_files` philosophy — single tool, multi-mode, batched.
    Pick the cheapest detail mode that answers the question.

    Args:
      deck_url:        Slides URL or raw deck ID.
      slides:          Slide selector. None=all. See `_resolve_slide_ids`
                       docstring for accepted forms (string id, range
                       "3-7", list of ids, list of 1-indexed ints,
                       {"first": N}, {"last": N}, {"with_notes": true},
                       {"with_image": true}).
      detail:          Detail level (token cost is per-slide, approx):
                         "outline"  → title + arch + counts (~20 tok)
                         "summary"  → title + body + notes preview (~80 tok)
                         "full"     → all text + image refs + notes (~150 tok)
                         "raw"      → faithful: geometry + style (debug, ~400 tok)
      include_notes:   Include speaker notes in summary/full. Default True.
      include_images:  "ref" → emit `ref://<object_id>` for picture elements
                                (use `render_thumbnail` to actually see them).
                       "none" → omit image references entirely.

    Returns:
      {deck_id, title, slide_count, detail, slides: [...]}

    For visual content (the actual rendered pixels), call `render_thumbnail`
    explicitly — keeping it out of this tool prevents accidental token blow-up.
    """
    if detail not in ("outline", "summary", "full", "raw"):
        raise ValueError(f"detail must be outline|summary|full|raw; got {detail!r}")
    if include_images not in ("ref", "none"):
        raise ValueError(f"include_images must be ref|none; got {include_images!r}")

    deck_id = slides_api.deck_id_from_url(deck_url)
    prez = slides_api.get_presentation(deck_id)
    all_slides = prez.get("slides", []) or []
    deck_positions = {s["objectId"]: i for i, s in enumerate(all_slides, start=1)}
    target_ids = _resolve_slide_ids(prez, slides)
    by_id = {s["objectId"]: s for s in all_slides}

    out: list[dict[str, Any]] = []
    for sid in target_ids:
        slide = by_id.get(sid)
        if not slide:
            continue
        shapes = normalize.normalize_page(slide)
        archetype = classify.classify(shapes)
        notes = normalize.extract_notes_text(slide) if include_notes else ""
        out.append(
            projection.project(
                sid,
                shapes,
                archetype,
                notes,
                detail=detail,
                include_images=(include_images == "ref"),
                position=deck_positions.get(sid),
                hidden=normalize.is_hidden(slide),
                layout_id=normalize.layout_id(slide),
            )
        )

    return {
        "deck_id": deck_id,
        "title": prez.get("title", ""),
        "slide_count": len(out),
        "detail": detail,
        "slides": out,
    }


@mcp.tool()
def search_deck(
    deck_url: str,
    query: str,
    slides: Any = None,
    regex: bool = False,
    case_sensitive: bool = False,
    include_notes: bool = True,
) -> dict[str, Any]:
    """Find slides whose title / body / notes match `query`.

    Args:
      deck_url:        Slides URL or ID.
      query:           Substring (default) or regex pattern (`regex=True`).
      slides:          Slide selector — same shape as `read_slides`.
                       None = search the whole deck.
      regex:           Treat `query` as a Python regex when True.
      case_sensitive:  Default False (ignore case).
      include_notes:   Search speaker notes too. Default True.

    Returns:
      {deck_id, query, hit_count,
       hits: [{slide_id, where, snippet}]}
      `where` ∈ {"title", "body", "notes"} indicates the first match locus.
      Snippet is truncated to ~200 chars.
    """
    deck_id = slides_api.deck_id_from_url(deck_url)
    prez = slides_api.get_presentation(deck_id)
    target_ids = _resolve_slide_ids(prez, slides)
    by_id = {s["objectId"]: s for s in prez.get("slides", [])}

    if regex:
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            pat = re.compile(query, flags)
        except re.error as e:
            raise ValueError(f"invalid regex {query!r}: {e}") from e

        def matches(text: str) -> bool:
            return bool(pat.search(text))
    else:
        needle = query if case_sensitive else query.lower()

        def matches(text: str) -> bool:
            hay = text if case_sensitive else text.lower()
            return needle in hay

    hits: list[dict[str, Any]] = []
    for sid in target_ids:
        slide = by_id.get(sid)
        if not slide:
            continue
        shapes = normalize.normalize_page(slide)
        flat = normalize.flatten(shapes)
        title_shape = projection.best_title(flat)
        title_text = (title_shape.text or "").strip() if title_shape else ""

        if title_text and matches(title_text):
            hits.append({"slide_id": sid, "where": "title", "snippet": title_text[:200]})
            continue

        body_hit: str | None = None
        for s in flat:
            if s.kind == "text" and s.text and s is not title_shape and matches(s.text):
                body_hit = s.text.strip()
                break
        if body_hit:
            hits.append({"slide_id": sid, "where": "body", "snippet": body_hit[:200]})
            continue

        if include_notes:
            notes = normalize.extract_notes_text(slide)
            if notes and matches(notes):
                hits.append({"slide_id": sid, "where": "notes", "snippet": notes.strip()[:200]})

    return {
        "deck_id": deck_id,
        "query": query,
        "hit_count": len(hits),
        "hits": hits,
    }


@mcp.tool()
def render_thumbnail(
    deck_url: str,
    slide_id: str,
    size: ThumbSize = "MEDIUM",
) -> Image:
    """Render one slide as a PNG and return as native MCP ImageContent.

    Expensive — opt in deliberately. Each thumbnail costs ~640-2700 tokens
    depending on size + model. Prefer `read_slides` for text content; reach
    for `render_thumbnail` only when the visual layout matters.

    For multiple slides: call this tool in parallel (the MCP transport
    supports concurrent calls) — keeping the response single-image keeps the
    return type clean and predictable.

    Args:
      slide_id: Slides API object ID for the target page.
      size:     "SMALL" (200×112) | "MEDIUM" (800×450, default) | "LARGE" (1600×900)
    """
    if size not in ("SMALL", "MEDIUM", "LARGE"):
        raise ValueError(f"size must be SMALL|MEDIUM|LARGE; got {size!r}")
    deck_id = slides_api.deck_id_from_url(deck_url)
    png = slides_api.get_thumbnail_bytes(deck_id, slide_id, size=size)
    return Image(data=png, format="png")


def main() -> None:
    """Entry point used by both the CLI and `python -m slides_mcp.server`."""
    mcp.run()


if __name__ == "__main__":
    main()
