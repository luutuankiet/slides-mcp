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
PostStateMode = Literal["full", "summary", "outline", "none"]

# Slides API Request kinds that mutate or destroy existing content.
# `exec_batch_update` requires `confirm_destructive=True` to apply any of these.
DESTRUCTIVE_KINDS = frozenset({
    "deleteObject",
    "deleteSlide",
    "deleteText",
    "deleteTableRow",
    "deleteTableColumn",
    "deleteParagraphBullets",
    "replaceAllText",
    "replaceAllShapesWithImage",
    "replaceAllShapesWithSheetsChart",
})


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


def _project_deck_outline(
    prez: dict[str, Any],
    deck_id: str | None = None,
) -> dict[str, Any]:
    """Build deck-level outline projection.

    Shared by `get_deck_outline` (read tool) and `exec_batch_update`'s
    post-state envelope (write tool). ~20 tok/slide.
    """
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
        "deck_id": deck_id or prez.get("presentationId", ""),
        "title": prez.get("title", ""),
        "slide_count": len(slides_out),
        "slides": slides_out,
    }


def _walk_element_objectids(
    el: dict[str, Any],
    sid: str,
    idx: dict[str, str],
) -> None:
    """DFS: index every nested element objectId in `el` to its owning slide id.

    Used by `_extract_affected_slide_ids` to map element-scoped requests
    (updateTextStyle, updateShapeProperties, etc.) back to a slide.
    """
    if oid := el.get("objectId"):
        idx[oid] = sid
    children = el.get("elementGroup", {}).get("children", []) or []
    for c in children:
        _walk_element_objectids(c, sid, idx)


def _extract_affected_slide_ids(
    requests: list[dict[str, Any]],
    replies: list[dict[str, Any]],
    prez: dict[str, Any],
) -> list[str]:
    """Best-effort derivation of slide ids touched by a batchUpdate.

    Walks request bodies for `pageObjectId` (slide refs) and `objectId`
    (slide id OR element id mapped via `prez`). Walks replies for
    `createSlide`/`duplicateObject` server-generated ids.

    Special case: `replaceAllText` without a `pageObjectIds` scope is
    deck-wide — returns ALL slide ids.

    Returns sorted list of slide objectIds known to the deck.
    """
    slide_ids = {s["objectId"] for s in prez.get("slides", []) or []}

    elem_to_slide: dict[str, str] = {}
    for slide in prez.get("slides", []) or []:
        sid = slide["objectId"]
        for el in slide.get("pageElements", []) or []:
            _walk_element_objectids(el, sid, elem_to_slide)

    affected: set[str] = set()

    for req in requests:
        if rat := req.get("replaceAllText"):
            scope = rat.get("pageObjectIds")
            if scope:
                affected.update(s for s in scope if s in slide_ids)
            else:
                # whole-deck scope
                return sorted(slide_ids)
        for body in req.values():
            if not isinstance(body, dict):
                continue
            # Top-level pageObjectId (most update* requests)
            pid = body.get("pageObjectId")
            if pid in slide_ids:
                affected.add(pid)
            # Top-level pageObjectIds list (updatePageElementsZOrder, etc.)
            for sid in body.get("pageObjectIds", []) or []:
                if sid in slide_ids:
                    affected.add(sid)
            # Nested elementProperties.pageObjectId (createShape/Image/Line/Video/Table)
            ep = body.get("elementProperties")
            if isinstance(ep, dict):
                nested_pid = ep.get("pageObjectId")
                if nested_pid in slide_ids:
                    affected.add(nested_pid)
            # Top-level objectId (slide id OR element id mapped via prez)
            oid = body.get("objectId")
            if oid in slide_ids:
                affected.add(oid)
            elif oid and oid in elem_to_slide:
                affected.add(elem_to_slide[oid])
            # Top-level objectIds / childrenObjectIds lists (group/ungroup, etc.)
            related_lists = (
                body.get("objectIds") or [],
                body.get("childrenObjectIds") or [],
            )
            for lst in related_lists:
                for related in lst:
                    if related in slide_ids:
                        affected.add(related)
                    elif related in elem_to_slide:
                        affected.add(elem_to_slide[related])

    for reply in replies or []:
        if cs := reply.get("createSlide"):
            if oid := cs.get("objectId"):
                affected.add(oid)
        if dup := reply.get("duplicateObject"):
            oid = dup.get("objectId")
            if oid in slide_ids:
                affected.add(oid)
            elif oid and oid in elem_to_slide:
                affected.add(elem_to_slide[oid])

    return sorted(affected)


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
    return _project_deck_outline(prez, deck_id=deck_id)


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


@mcp.tool()
def exec_batch_update(
    deck_url: str,
    requests: list[dict],
    dry_run: bool = False,
    confirm_destructive: bool = False,
    post_state: PostStateMode = "summary",
) -> dict[str, Any]:
    """Apply Slides API Requests; return result + multi-granularity post-state.

    The agent composes `requests` directly from the Slides API Request reference:
      https://developers.google.com/slides/api/reference/rest/v1/presentations/request
    The server forwards verbatim, then re-reads the deck and projects post-state.

    The novelty (verify-after-write multi-granularity return): every successful
    call returns `post_state.deck_outline` (always, unless `post_state="none"`)
    plus `post_state.slides[]` for each touched slide (when `post_state` ∈
    {summary, full}). Cuts the fire-then-read round-trip the agent would otherwise
    need. No production MCP server (verified across `matteoantoci/google-slides-mcp`
    177★, `mcp/git`, `notion-mcp-server`) bundles a verify-read into write returns
    by default.

    Args:
      deck_url:             Slides URL or raw deck ID.
      requests:             Slides API Request list. Each is a dict with one
                            top-level kind, e.g. `{"createShape": {...}}`,
                            `{"replaceAllText": {...}}`, etc.
      dry_run:              True → returns `{request_kinds, preview}` without
                            firing.
      confirm_destructive:  Required True if any request kind is destructive:
                            deleteObject, deleteSlide, deleteText,
                            deleteTableRow, deleteTableColumn,
                            deleteParagraphBullets, replaceAllText,
                            replaceAllShapesWithImage,
                            replaceAllShapesWithSheetsChart.
      post_state:           Post-state envelope verbosity:
                              "full"     — deck_outline + full-detail slides[touched]
                              "summary"  — deck_outline + summary slides[touched]
                                           (default)
                              "outline"  — deck_outline only
                              "none"     — action receipt only (no re-read)

    Returns (always):
      {applied_request_count, request_kinds, replies, warnings, isError}
    Returns (when post_state != "none"):
      + {affected_slide_ids, post_state: {deck_outline, slides?}}

    Raises:
      ValueError on empty `requests` or invalid `post_state`.
      slides_api.SlidesApiError on Slides API failure. 403 PERMISSION_DENIED
        usually means the OAuth token has `presentations.readonly` scope only
        (the v2 default) — re-run `slides-mcp-auth` with a fresh consent
        prompt to mint a token with write scope.
    """
    if post_state not in ("full", "summary", "outline", "none"):
        raise ValueError(
            f"post_state must be full|summary|outline|none; got {post_state!r}"
        )
    if not requests:
        raise ValueError("`requests` must be non-empty")

    request_kinds = [next(iter(r.keys())) for r in requests if r]
    destructive = [k for k in request_kinds if k in DESTRUCTIVE_KINDS]

    if dry_run:
        return {
            "dry_run": True,
            "applied_request_count": 0,
            "request_kinds": request_kinds,
            "preview": requests[:5],
            "destructive_kinds_detected": sorted(set(destructive)),
            "warnings": [],
            "isError": False,
        }

    if destructive and not confirm_destructive:
        return {
            "applied_request_count": 0,
            "request_kinds": request_kinds,
            "replies": [],
            "warnings": [
                f"Refused — destructive kinds detected: {sorted(set(destructive))}. "
                f"Re-call with confirm_destructive=True to proceed."
            ],
            "isError": True,
        }

    deck_id = slides_api.deck_id_from_url(deck_url)

    try:
        api_response = slides_api.batch_update(deck_id, requests)
    except slides_api.SlidesApiError as e:
        if e.status == 403:
            raise slides_api.SlidesApiError(
                f"{e}. If your token was minted by slides-mcp v2.0+, it likely "
                f"has `presentations.readonly` scope only. Re-run "
                f"`slides-mcp-auth` to mint a token with write scope.",
                status=e.status,
                reason=e.reason,
            ) from e
        raise

    replies = api_response.get("replies", []) or []

    if post_state == "none":
        return {
            "applied_request_count": len(requests),
            "request_kinds": request_kinds,
            "replies": replies,
            "warnings": [],
            "isError": False,
        }

    # Re-read deck for post-state projection (single FieldMask GET serves both layers)
    prez = slides_api.get_presentation(deck_id)
    affected_slide_ids = _extract_affected_slide_ids(requests, replies, prez)

    deck_outline = _project_deck_outline(prez, deck_id=deck_id)
    post_state_envelope: dict[str, Any] = {"deck_outline": deck_outline}

    if post_state in ("summary", "full"):
        all_slides = prez.get("slides", []) or []
        by_id = {s["objectId"]: s for s in all_slides}
        positions = {s["objectId"]: i for i, s in enumerate(all_slides, start=1)}
        slide_states: list[dict[str, Any]] = []
        for sid in affected_slide_ids:
            slide = by_id.get(sid)
            if not slide:
                continue
            shapes = normalize.normalize_page(slide)
            archetype = classify.classify(shapes)
            notes = normalize.extract_notes_text(slide)
            slide_states.append(
                projection.project(
                    sid, shapes, archetype, notes,
                    detail=post_state,
                    position=positions.get(sid),
                    hidden=normalize.is_hidden(slide),
                    layout_id=normalize.layout_id(slide),
                )
            )
        post_state_envelope["slides"] = slide_states

    return {
        "applied_request_count": len(requests),
        "request_kinds": request_kinds,
        "replies": replies,
        "warnings": [],
        "affected_slide_ids": affected_slide_ids,
        "post_state": post_state_envelope,
        "isError": False,
    }


# 16:9 Google Slides standard deck dimensions in EMU (1 inch = 914400 EMU)
_SLIDE_WIDTH_EMU = 9144000   # 10 in
_SLIDE_HEIGHT_EMU = 5143500  # 5.625 in
_FOOTER_WIDTH_EMU = 4500000  # half-deck wide; covers prev/section/next text
_FOOTER_HEIGHT_EMU = 280000  # ~22pt tall band
_FOOTER_MARGIN_EMU = 100000  # ~8pt margin from slide edge
_FOOTER_FONT_PT = 9
_FOOTER_OBJID_PREFIX = "slides_mcp_footer_"


@mcp.tool()
def add_section_footers(
    deck_url: str,
    sections: list[dict],
    template: str = "{section_name} · {position}/{total} · prev: {prev_name} · next: {next_name}",
    footer_position: Literal["bottom-left", "bottom-center", "bottom-right"] = "bottom-right",
    overwrite_existing: bool = True,
    confirm_destructive: bool = False,
    post_state: PostStateMode = "summary",
) -> dict[str, Any]:
    """Add chapter/section footer to every slide.

    Proof tool for the v2.1 write-wedge: takes a section map, builds the
    Slides API request list internally, delegates to `exec_batch_update`.
    Same multi-granularity post-state envelope returned.

    Layout caveat (per v2.1 mandate): footer position is approximate, not
    pixel-perfect. The agent is doing legwork humans hate; visual fine-tuning
    stays a human-in-the-Slides-UI task.

    Args:
      deck_url:           Slides URL or raw deck ID.
      sections:           Ordered list of section dicts. Each must have:
                            `name`        — section name (str).
                          And EXACTLY ONE of:
                            `slide_range` — "N-M" 1-indexed inclusive,
                            `slide_ids`   — list[str] of slide objectIds,
                            `slide_positions` — list[int] of 1-indexed positions.
      template:           Format string. Available keys: `{section_name}`,
                          `{position}` (1-indexed within section),
                          `{total}` (slide count in section), `{prev_name}`,
                          `{next_name}` (empty string for first/last sections).
      footer_position:    Where to anchor the footer band (default bottom-right).
      overwrite_existing: True → deletes any prior `slides_mcp_footer_*` shapes
                          before recreating (idempotent re-runs). False → skips
                          slides that already have a footer (returns them in
                          `skipped_slide_ids`).
      confirm_destructive: Required True if `overwrite_existing=True` AND any
                           prior footers exist (deleteObject is destructive).
      post_state:         Forwarded to `exec_batch_update`.

    Returns:
      `exec_batch_update` envelope plus:
        `_proof_tool: "add_section_footers"`
        `sections_applied: int`
        `footers_added: int`
        `skipped_slide_ids: list[str]`

    Raises:
      ValueError on empty sections, missing slide selectors, or unknown
        footer_position.
      slides_api.SlidesApiError on Slides API failure.
    """
    if not sections:
        raise ValueError("`sections` must be non-empty")
    if footer_position not in ("bottom-left", "bottom-center", "bottom-right"):
        raise ValueError(
            f"footer_position must be bottom-left|bottom-center|bottom-right; "
            f"got {footer_position!r}"
        )

    deck_id = slides_api.deck_id_from_url(deck_url)
    prez = slides_api.get_presentation(deck_id)

    # Resolve sections → [(slide_id, section_idx, pos_in_section, section_total), ...]
    section_names = [s.get("name", f"Section {i + 1}") for i, s in enumerate(sections)]
    slide_assignments: list[tuple[str, int, int, int]] = []
    for sec_idx, sec in enumerate(sections):
        if rng := sec.get("slide_range"):
            sec_slide_ids = _resolve_slide_ids(prez, rng)
        elif ids := sec.get("slide_ids"):
            sec_slide_ids = _resolve_slide_ids(prez, ids)
        elif pos := sec.get("slide_positions"):
            sec_slide_ids = _resolve_slide_ids(prez, pos)
        else:
            raise ValueError(
                f"section {sec_idx} (`{section_names[sec_idx]}`) missing one of "
                f"`slide_range`, `slide_ids`, `slide_positions`"
            )
        for pos_in_sec, sid in enumerate(sec_slide_ids, start=1):
            slide_assignments.append((sid, sec_idx, pos_in_sec, len(sec_slide_ids)))

    # Find existing slides_mcp_footer_* objectIds (for overwrite/skip decision)
    existing_footers: set[str] = set()
    for slide in prez.get("slides", []) or []:
        for el in slide.get("pageElements", []) or []:
            oid = el.get("objectId", "") or ""
            if oid.startswith(_FOOTER_OBJID_PREFIX):
                existing_footers.add(oid)

    # Footer x position
    if footer_position == "bottom-left":
        footer_x = _FOOTER_MARGIN_EMU
    elif footer_position == "bottom-center":
        footer_x = (_SLIDE_WIDTH_EMU - _FOOTER_WIDTH_EMU) // 2
    else:  # bottom-right
        footer_x = _SLIDE_WIDTH_EMU - _FOOTER_WIDTH_EMU - _FOOTER_MARGIN_EMU
    footer_y = _SLIDE_HEIGHT_EMU - _FOOTER_HEIGHT_EMU - _FOOTER_MARGIN_EMU

    requests: list[dict[str, Any]] = []
    skipped: list[str] = []
    footers_added = 0

    for sid, sec_idx, pos_in_sec, sec_total in slide_assignments:
        # Deterministic, slide-scoped objectId. Suffix bounded to last 12 chars
        # of slide_id; collision-free in practice (Slides slide ids are unique).
        footer_oid = f"{_FOOTER_OBJID_PREFIX}{sid[-12:]}"

        footer_text = template.format(
            section_name=section_names[sec_idx],
            position=pos_in_sec,
            total=sec_total,
            prev_name=section_names[sec_idx - 1] if sec_idx > 0 else "",
            next_name=section_names[sec_idx + 1] if sec_idx < len(section_names) - 1 else "",
        )

        if footer_oid in existing_footers:
            if overwrite_existing:
                requests.append({"deleteObject": {"objectId": footer_oid}})
            else:
                skipped.append(sid)
                continue

        requests.append({
            "createShape": {
                "objectId": footer_oid,
                "shapeType": "TEXT_BOX",
                "elementProperties": {
                    "pageObjectId": sid,
                    "size": {
                        "width": {"magnitude": _FOOTER_WIDTH_EMU, "unit": "EMU"},
                        "height": {"magnitude": _FOOTER_HEIGHT_EMU, "unit": "EMU"},
                    },
                    "transform": {
                        "scaleX": 1,
                        "scaleY": 1,
                        "translateX": footer_x,
                        "translateY": footer_y,
                        "unit": "EMU",
                    },
                },
            }
        })
        requests.append({
            "insertText": {
                "objectId": footer_oid,
                "text": footer_text,
                "insertionIndex": 0,
            }
        })
        # LOG-015 invariant: explicit autofit:NONE before any further updateShapeProperties
        requests.append({
            "updateShapeProperties": {
                "objectId": footer_oid,
                "shapeProperties": {"autofit": {"autofitType": "NONE"}},
                "fields": "autofit.autofitType",
            }
        })
        requests.append({
            "updateTextStyle": {
                "objectId": footer_oid,
                "textRange": {"type": "ALL"},
                "style": {
                    "fontSize": {"magnitude": _FOOTER_FONT_PT, "unit": "PT"},
                    "foregroundColor": {
                        "opaqueColor": {
                            "rgbColor": {"red": 0.45, "green": 0.45, "blue": 0.45}
                        }
                    },
                },
                "fields": "fontSize,foregroundColor",
            }
        })
        footers_added += 1

    if not requests:
        return {
            "_proof_tool": "add_section_footers",
            "sections_applied": len(sections),
            "footers_added": 0,
            "skipped_slide_ids": skipped,
            "applied_request_count": 0,
            "warnings": [
                f"No footers added; {len(skipped)} slide(s) had existing footers "
                f"and overwrite_existing=False"
            ] if skipped else ["No slides matched the section map"],
            "isError": False,
        }

    # Delegate to exec_batch_update for fire + post-state
    result = exec_batch_update(
        deck_url=deck_url,
        requests=requests,
        dry_run=False,
        confirm_destructive=confirm_destructive,
        post_state=post_state,
    )
    result["_proof_tool"] = "add_section_footers"
    result["sections_applied"] = len(sections)
    result["footers_added"] = footers_added
    result["skipped_slide_ids"] = skipped
    return result


def main() -> None:
    """Entry point used by both the CLI and `python -m slides_mcp.server`."""
    mcp.run()


if __name__ == "__main__":
    main()
