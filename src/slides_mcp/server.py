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

import json
import re
import time
from typing import Any, Literal

import anyio
from fastmcp import FastMCP
from fastmcp.server.middleware import Middleware
from fastmcp.utilities.types import Image

from . import (
    __version__,
    auth,
    classify,
    layout,
    normalize,
    notes_md,
    projection,
    receipts,
    scripting,
    skill_bundle,
    slides_api,
    state_store,
    svg_native,
    svg_raster,
    writes,
)
from .writes import DESTRUCTIVE_KINDS

# Some clients show these before any tool is loaded (tool search), so they
# route an agent that has found only one tool.
INSTRUCTIONS = (
    "Start with `get_deck_outline`. For edits that depend on what is in the deck, "
    "use `run_deck_script`. Before composing raw `exec_batch_update` requests, the "
    "slides-mcp skill has the Request cheat sheet and worked examples: load it with "
    "your own skill loader if installed, otherwise call `install_skill`."
)

mcp = FastMCP("slides-mcp", version=__version__, instructions=INSTRUCTIONS)
# fastmcp advertises tools.listChanged; this server's tool list never changes.
mcp._mcp_server.notification_options.tools_changed = False
_fastmcp_tool = mcp.tool


def _tool(hints: dict[str, bool]):
    # Keep the whole docstring as the description; fastmcp would drop Returns.
    # Clients treat a tool with no hints as destructive, so every tool sets all four.
    return lambda fn: _fastmcp_tool(description=fn.__doc__, annotations=hints)(fn)


def _hints(read_only: bool, destructive: bool, idempotent: bool) -> dict[str, bool]:
    return {"readOnlyHint": read_only, "destructiveHint": destructive,
            "idempotentHint": idempotent, "openWorldHint": True}


_READ = _hints(read_only=True, destructive=False, idempotent=True)
_ADD = _hints(read_only=False, destructive=False, idempotent=False)
_REPLACE = _hints(read_only=False, destructive=True, idempotent=True)
_EDIT = _hints(read_only=False, destructive=True, idempotent=False)


class _CallLog(Middleware):
    """One content-free stderr line per tool call: name, duration, outcome."""

    async def on_call_tool(self, context, call_next):
        start = time.monotonic()
        ok = False
        try:
            result = await call_next(context)
            ok = not getattr(result, "is_error", False)
            return result
        finally:
            writes.log_call(context.message.name, time.monotonic() - start, ok)


mcp.add_middleware(_CallLog())


Detail = Literal["outline", "summary", "full", "raw"]
ImageMode = Literal["ref", "none"]
ThumbSize = Literal["SMALL", "MEDIUM", "LARGE"]
PostStateMode = Literal["full", "summary", "outline", "none"]

NotesFormat = Literal["text", "markdown"]
NotesMode = Literal["replace", "append"]

__all__ = ["DESTRUCTIVE_KINDS", "mcp"]


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


def _extract_affected_slide_ids(
    requests: list[dict[str, Any]],
    replies: list[dict[str, Any]],
    prez: dict[str, Any],
) -> list[str]:
    """Slides a batchUpdate touches; see `writes.affected_slide_ids`."""
    return writes.affected_slide_ids(requests, replies, prez)


def _slide_shapes(ctx: normalize.DeckContext, slide: dict[str, Any]) -> list[normalize.FlatShape]:
    return normalize.normalize_page(slide, ctx.theme_for(slide))


# ---- tools ----------------------------------------------------------


@_tool(_READ)
def auth_status() -> dict[str, Any]:
    """Diagnostic: report sign-in state without exposing secrets.

    stdio: the token.json path, scopes and expiry. Remote (HTTP) mode:
    `mode: "http"`, the caller's email, granted scopes and token expiry.
    """
    return auth.credentials_info()


@_tool(_READ)
def install_skill() -> dict[str, Any]:
    """Return the slides-mcp agent skill (every file, with its path) plus install steps.

    Call this to load the skill's Request cheat sheet and worked examples
    before composing raw `exec_batch_update` requests, if your harness does
    not already have the skill, and when the user asks to install, set up or
    update the slides-mcp skill. Needs no deck and no Google sign-in. Follow
    the `instructions` in the response.
    """
    return skill_bundle.bundle()


@_tool(_READ)
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


@_tool(_READ)
def read_slides(
    deck_url: str,
    slides: Any = None,
    detail: Detail = "summary",
    include_notes: bool = True,
    include_images: ImageMode = "ref",
    notes_format: NotesFormat = "text",
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
                         "raw"      → faithful: geometry + style (debug, ~400 tok).
                                      `at` is in INCHES; writes take EMU
                                      (1 in = 914400 EMU). The reply's `units`
                                      key says so too.
      include_notes:   Include speaker notes in summary/full. Default True.
      include_images:  "ref" → emit `ref://<object_id>` for picture elements
                                (use `render_thumbnail` to actually see them).
                       "none" → omit image references entirely.
      notes_format:    "text" (default) → notes as plain text.
                       "markdown" → notes with structure kept: **bold**,
                       *italic*, #/##/### headers, `-` bullets nested by two
                       spaces. The same subset `write_speaker_notes` accepts.

    Raw detail reports each element's page-space box (rotation and groups
    applied) and, only when they differ from the default: `fill` (kind
    none|solid|image|inherit|other, hex, alpha, theme slot), `outline`,
    `autofit`, `rotation_deg`, `parent_id`; runs add `weight`, UTF-16
    `start`/`end` and `color_theme`. Each slide adds `background`.

    Returns:
      {deck_id, title, slide_count, detail, slides: [...]}

    For visual content (the actual rendered pixels), call `render_thumbnail`
    explicitly — keeping it out of this tool prevents accidental token blow-up.
    """
    if detail not in ("outline", "summary", "full", "raw"):
        raise ValueError(f"detail must be outline|summary|full|raw; got {detail!r}")
    if include_images not in ("ref", "none"):
        raise ValueError(f"include_images must be ref|none; got {include_images!r}")
    if notes_format not in ("text", "markdown"):
        raise ValueError(f"notes_format must be text|markdown; got {notes_format!r}")

    deck_id = slides_api.deck_id_from_url(deck_url)
    prez = slides_api.get_presentation(deck_id)
    ctx = normalize.DeckContext(prez)
    all_slides = prez.get("slides", []) or []
    deck_positions = {s["objectId"]: i for i, s in enumerate(all_slides, start=1)}
    target_ids = _resolve_slide_ids(prez, slides)
    by_id = {s["objectId"]: s for s in all_slides}

    out: list[dict[str, Any]] = []
    for sid in target_ids:
        slide = by_id.get(sid)
        if not slide:
            continue
        shapes = _slide_shapes(ctx, slide)
        archetype = classify.classify(shapes)
        notes = ""
        if include_notes:
            notes = (notes_md.to_markdown(normalize.notes_shape(slide)[1])
                     if notes_format == "markdown" else normalize.extract_notes_text(slide))
        row = projection.project(
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
        if detail == "raw":
            bg = ctx.background_for(slide)
            if bg != {"kind": "solid", "hex": "#FFFFFF", "alpha": 1.0}:
                row["background"] = bg
        out.append(row)

    reply: dict[str, Any] = {
        "deck_id": deck_id,
        "title": prez.get("title", ""),
        "slide_count": len(out),
        "detail": detail,
    }
    if detail == "raw":
        reply["units"] = RAW_UNITS
    reply["slides"] = out
    return reply


RAW_UNITS = ("at is [left, top, width, height] in inches. Writes and script helpers take "
             "EMU: 1 in = 914400 EMU (helpers.inch(n) converts).")


@_tool(_READ)
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


@_tool(_READ)
def render_thumbnail(
    deck_url: str,
    slide_id: str,
    size: ThumbSize = "MEDIUM",
) -> Any:
    """Render one slide as a PNG and return as native MCP ImageContent.

    Expensive — opt in deliberately. Each thumbnail costs ~640-2700 tokens
    depending on size + model. Prefer `read_slides` for text content; reach
    for `render_thumbnail` only when the visual layout matters.

    For multiple slides: call this tool in parallel (the MCP transport
    supports concurrent calls); each reply carries one image.

    Returns: one line of text, then the image. Some clients do not show the
    image to the model; the text line asks you to say so rather than describe it.

    Args:
      slide_id: Slides API object ID for the target page.
      size:     "SMALL" (200×112) | "MEDIUM" (800×450, default) | "LARGE" (1600×900)
    """
    if size not in ("SMALL", "MEDIUM", "LARGE"):
        raise ValueError(f"size must be SMALL|MEDIUM|LARGE; got {size!r}")
    deck_id = slides_api.deck_id_from_url(deck_url)
    png = slides_api.get_thumbnail_bytes(deck_id, slide_id, size=size)
    return [receipts.CANT_SEE_NOTE, Image(data=png, format="png")]


@_tool(_ADD)
def create_deck(title: str) -> dict[str, Any]:
    """Create a new, empty Google Slides deck with this title.

    The deck lands in the root of the signed-in user's Google Drive, owned by
    them, with Google's default theme and one title slide. There is no folder
    or template option. Edit it next with `run_deck_script` or
    `exec_batch_update`, passing `deck_id`.

    Args:
      title: The deck's name. Required, non-empty.

    Returns:
      {deck_id, deck_url, title, first_slide_id, deck_outline, isError}
      `deck_outline` is what `get_deck_outline` returns for the new deck.
    """
    title = (title or "").strip()
    if not title:
        raise ValueError("title must be a non-empty string")
    prez = slides_api.create_presentation(title)
    deck_id = prez["presentationId"]
    writes.audit({"tool": "create_deck", "deck_id": deck_id})
    slides = prez.get("slides") or []
    return {
        "deck_id": deck_id,
        "deck_url": f"https://docs.google.com/presentation/d/{deck_id}/edit",
        "title": prez.get("title", title),
        "first_slide_id": slides[0]["objectId"] if slides else None,
        "deck_outline": _project_deck_outline(prez, deck_id=deck_id),
        "isError": False,
    }


@_tool(_EDIT)
def exec_batch_update(
    deck_url: str,
    requests: list[dict],
    dry_run: bool = False,
    confirm_destructive: bool = False,
    post_state: PostStateMode = "summary",
    receipt: receipts.Receipt = "medium",
) -> Any:
    """Apply Slides API Requests; return result + multi-granularity post-state.

    For edits that depend on what is in the deck (restyle every title, swap a
    palette), use `run_deck_script` instead: it reads the deck server-side.
    Use this for a handful of requests whose ids you already know.

    The agent composes `requests` directly from the Slides API Request reference:
      https://developers.google.com/slides/api/reference/rest/v1/presentations/request
    The server forwards verbatim, then re-reads the deck and projects post-state:
    `post_state.deck_outline` (unless `post_state="none"`) plus
    `post_state.slides[]` for each touched slide (`summary` or `full`).

    Composing requests:
      - Slide ids come from `get_deck_outline` (`slide_id`); element ids from
        `read_slides(..., detail="raw")` (`id`).
      - Sizes are EMU: 1 in = 914400, 1 pt = 12700; a 16:9 page is
        9144000 x 5143500.
      - After `insertText` into a new TEXT_BOX, send `updateShapeProperties`
        with `autofit.autofitType = "NONE"` before any other
        `updateShapeProperties`, or those fail with an opaque error.
      - Worked examples and the full cheat sheet: the slides-mcp skill (your
        own skill loader, or `install_skill`).

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
                              "none"     — apply result only (no re-read
                                           unless a receipt is attached)
      receipt:              Thumbnails of the touched slides, attached as
                            images after a real apply:
                              "medium"   — 800x450, about 480 tokens each
                                           (default)
                              "large"    — 1600x900, for reading small text
                              "off"      — none; use for bulk edits you
                                           already trust
                            At most 3 slides; the rest are listed in
                            `thumbnails.not_shown_slide_ids`. A failed
                            thumbnail is a warning, never an error: the
                            write has landed.

    A create whose width or height is under 1 pt (12700 EMU), or not a number,
    is refused before anything is sent: geometry is EMU, and inches passed as
    EMU make Google silently use its 3,000,000 EMU default square instead.
    After a write, `warnings` names any element it created that sits at that
    default size, is stacked exactly on two or more other new elements, or is
    partly or fully off the page. Read them: some clients cannot show the
    receipt image to the model, so these lines may be your only check.

    Returns (always):
      {applied_request_count, request_kinds, replies, warnings, isError}
    Returns (when post_state != "none"):
      + {affected_slide_ids, post_state: {deck_outline, slides?}}
    Returns (when a receipt is attached):
      + {thumbnails: {size, slide_ids, not_shown_slide_ids?, hint?, note}}, sent
      as a JSON text block followed by one image per rendered slide.

    Raises:
      ValueError on empty `requests` or invalid `post_state`.
      slides_api.SlidesApiError on Slides API failure. 403 PERMISSION_DENIED
        usually means the OAuth token has `presentations.readonly` scope only
        (the v2 default) — re-run `slides-mcp-auth` with a fresh consent
        prompt to mint a token with write scope.
    """
    receipts.check(receipt)
    out, touched = _apply_requests(
        deck_url, requests, dry_run=dry_run, confirm_destructive=confirm_destructive,
        post_state=post_state, want_slides=receipt != "off",
    )
    return _attach_receipt(out, deck_url, touched, receipt)


def _apply_requests(
    deck_url: str,
    requests: list[dict],
    *,
    dry_run: bool,
    confirm_destructive: bool,
    post_state: str,
    want_slides: bool,
    tool: str = "exec_batch_update",
) -> tuple[dict[str, Any], list[str]]:
    """`exec_batch_update` without the receipt.

    Returns the reply and, after a real apply that re-read the deck, the
    touched slides that still exist, in deck order.
    """
    if post_state not in ("full", "summary", "outline", "none"):
        raise ValueError(
            f"post_state must be full|summary|outline|none; got {post_state!r}"
        )
    if not requests:
        raise ValueError("`requests` must be non-empty")

    request_kinds = writes.request_kinds(requests)
    destructive = [k for k in request_kinds if k in DESTRUCTIVE_KINDS]

    if sizes := writes.size_problems(requests):
        return {
            "applied_request_count": 0,
            "request_kinds": request_kinds,
            "replies": [],
            "warnings": [f"Refused, nothing was written: {p}" for p in sizes],
            "isError": True,
        }, []

    if dry_run:
        return {
            "dry_run": True,
            "applied_request_count": 0,
            "request_kinds": request_kinds,
            "preview": requests[:5],
            "destructive_kinds_detected": sorted(set(destructive)),
            "warnings": [],
            "isError": False,
        }, []

    if destructive and not confirm_destructive:
        return _refusal(request_kinds), []

    deck_id = slides_api.deck_id_from_url(deck_url)
    api_response = writes.apply_batch(deck_id, requests, tool=tool)

    replies = api_response.get("replies", []) or []
    created = layout.created_ids(requests, replies)

    if post_state == "none" and not want_slides:
        return {
            "applied_request_count": len(requests),
            "request_kinds": request_kinds,
            "replies": replies,
            "warnings": [_LAYOUT_SKIPPED] if created else [],
            "isError": False,
        }, []

    # Re-read deck for post-state projection (single FieldMask GET serves both layers)
    prez = slides_api.get_presentation(deck_id)
    affected_slide_ids = _extract_affected_slide_ids(requests, replies, prez)
    touched = receipts.in_deck_order(affected_slide_ids, prez)
    layout_warnings = layout.check(created, prez)
    if post_state == "none":
        return {
            "applied_request_count": len(requests),
            "request_kinds": request_kinds,
            "replies": replies,
            "warnings": layout_warnings,
            "isError": False,
        }, touched

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
        "warnings": layout_warnings,
        "affected_slide_ids": affected_slide_ids,
        "post_state": post_state_envelope,
        "isError": False,
    }, touched


_LAYOUT_SKIPPED = ("layout check skipped: no post-write read (post_state=\"none\" with "
                   "receipt=\"off\"), so new elements were not checked for position or size.")


def _refusal(request_kinds: list[str]) -> dict[str, Any]:
    """The reply to a destructive write sent without confirm_destructive."""
    destructive = sorted({k for k in request_kinds if k in DESTRUCTIVE_KINDS})
    return {
        "applied_request_count": 0,
        "request_kinds": request_kinds,
        "replies": [],
        "warnings": [
            f"Refused — destructive kinds detected: {destructive}. "
            f"Re-call with confirm_destructive=True to proceed."
        ],
        "isError": True,
    }


def _attach_receipt(
    out: dict[str, Any],
    deck_url: str,
    slide_ids: list[str],
    receipt: str,
    *,
    limit: int = receipts.MAX_SLIDES,
) -> Any:
    """`out` plus thumbnails of `slide_ids`, or `out` alone when there are none."""
    if receipt == "off" or out.get("isError") or not slide_ids:
        return out
    deck_id = slides_api.deck_id_from_url(deck_url)
    field, pngs, warnings = receipts.render(deck_id, slide_ids, receipt, limit=limit)
    out["thumbnails"] = field
    out.setdefault("warnings", []).extend(warnings)
    if not pngs:
        return out
    field["note"] = receipts.CANT_SEE_NOTE
    return [json.dumps(out, ensure_ascii=False), *(Image(data=p, format="png") for p in pngs)]


# 16:9 Google Slides standard deck dimensions in EMU (1 inch = 914400 EMU)
_SLIDE_WIDTH_EMU = 9144000   # 10 in
_SLIDE_HEIGHT_EMU = 5143500  # 5.625 in
_FOOTER_WIDTH_EMU = 4500000  # half-deck wide; covers prev/section/next text
_FOOTER_HEIGHT_EMU = 280000  # ~22pt tall band
_FOOTER_MARGIN_EMU = 100000  # ~8pt margin from slide edge
_FOOTER_FONT_PT = 9
_FOOTER_OBJID_PREFIX = "slides_mcp_footer_"


@_tool(_ADD)
def add_section_footers(
    deck_url: str,
    sections: list[dict],
    template: str = "{section_name} · {position}/{total} · prev: {prev_name} · next: {next_name}",
    footer_position: Literal["bottom-left", "bottom-center", "bottom-right"] = "bottom-right",
    overwrite_existing: bool = True,
    confirm_destructive: bool = False,
    post_state: PostStateMode = "summary",
    receipt: receipts.Receipt = "medium",
) -> Any:
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
      receipt:            Thumbnails of up to 3 footered slides, as in
                          `exec_batch_update`: "medium" (default, about 480
                          tokens each), "large" or "off".

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
    receipts.check(receipt)
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
        # autofit NONE first, or later updateShapeProperties calls on this box fail
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

    # Same path as exec_batch_update: fire + post-state, then the receipt
    result, touched = _apply_requests(
        deck_url,
        requests,
        dry_run=False,
        confirm_destructive=confirm_destructive,
        post_state=post_state,
        want_slides=receipt != "off",
    )
    result["_proof_tool"] = "add_section_footers"
    result["sections_applied"] = len(sections)
    result["footers_added"] = footers_added
    result["skipped_slide_ids"] = skipped
    return _attach_receipt(result, deck_url, touched, receipt)


@_tool(_REPLACE)
def write_speaker_notes(
    deck_url: str,
    notes: dict[str, str],
    mode: NotesMode = "replace",
    dry_run: bool = False,
    confirm_destructive: bool = False,
) -> dict[str, Any]:
    """Write speaker notes from Markdown for one or many slides in one batch.

    Markdown here is only the input format: the notes get real formatting,
    never literal symbols. Supported: paragraphs, **bold**, *italic*,
    `#`/`##`/`###` headers (a bold paragraph at 18/16/14 pt, since notes have
    no heading styles), and `-` bullets nested by two spaces (real Slides
    bullets). Anything else is written as literal text. Read notes back with
    `read_slides(..., notes_format="markdown")`; what you write reads back
    the same.

    Args:
      deck_url:            Slides URL or raw deck ID.
      notes:               {slide selector: markdown}. A selector is a slide
                           id, or a 1-based position as a string ("3").
      mode:                "replace" (default) or "append" (adds a new
                           paragraph after the existing notes).
      dry_run:             True → return the requests without applying.
      confirm_destructive: Required True to replace notes that are not empty
                           (that deletes text). Empty notes need no
                           confirmation.

    Returns the `exec_batch_update` envelope (post_state summary of the
    touched slides) plus `slides_written`.
    """
    if not notes:
        raise ValueError("`notes` must map at least one slide to markdown")
    if mode not in ("replace", "append"):
        raise ValueError(f"mode must be replace|append; got {mode!r}")
    deck_id = slides_api.deck_id_from_url(deck_url)
    prez = slides_api.get_presentation(deck_id)
    slides = prez.get("slides", []) or []
    by_id = {s["objectId"]: s for s in slides}
    requests: list[dict[str, Any]] = []
    written: list[str] = []
    for selector, markdown in notes.items():
        sel: Any = int(selector) if isinstance(selector, str) and selector.isdigit() else selector
        ids = _resolve_slide_ids(prez, [sel] if isinstance(sel, int) else sel)
        for sid in ids:
            oid, shape = normalize.notes_shape(by_id[sid])
            if not oid:
                raise ValueError(f"slide {sid} has no speaker notes shape")
            requests.extend(notes_md.build_requests(oid, markdown, mode=mode, existing=shape))
            written.append(sid)
    if not requests:
        return {"slides_written": [], "applied_request_count": 0,
                "warnings": ["Nothing to write: every markdown value was empty"], "isError": False}
    # An append never deletes existing text; its only possible destructive
    # kind is un-bulleting the lines it just inserted.
    safe_append = mode == "append" and notes_md.append_is_safe(requests)
    # No receipt: a thumbnail cannot show speaker notes.
    result, _ = _apply_requests(
        deck_id, requests, dry_run=dry_run,
        confirm_destructive=confirm_destructive or safe_append, post_state="summary",
        want_slides=False,
    )
    result["slides_written"] = written
    if dry_run:
        result["preview"] = requests
    return result


_PLAN_GONE = (
    "No plan with that plan_id for this deck and caller: plans expire after 1 hour, "
    "are single use, and apply only to the deck (and, on the hosted server, the "
    "person) they were made for. Run the dry run again to get a new plan_id.")


def _plan_error(deck_id: str, dry_run: bool, message: str) -> dict[str, Any]:
    return {"deck_id": deck_id, "dry_run": dry_run, "isError": True,
            "error": {"kind": "plan", "message": message}}


@_tool(_EDIT)
async def run_deck_script(
    deck_url: str,
    script: str | None = None,
    input: Any = None,  # noqa: A002 - the name agents see
    dry_run: bool = True,
    confirm_destructive: bool = False,
    include_requests: bool = False,
    render_slides: Any = None,
    timeout_s: float = 300,
    cpu_timeout_s: float = 30,
    max_requests: int = 5000,
    max_return_bytes: int = 8192,
    receipt: receipts.Receipt = "medium",
    plan_id: str | None = None,
) -> Any:
    """Run a JavaScript script against one deck: read, compute and edit in one call.

    Use this when the edits depend on what is in the deck (re-theme every
    slide, swap a palette, restyle every title, write notes from data). The
    deck never enters your context; only what the script returns does.

    The script is the body of an async function in a V8 sandbox with no
    filesystem, network, imports or timers. It sees:
      input    your `input` argument as native JSON (pass palettes, mappings
               and copy here, not inside the script string)
      deck     {id, title, pageSize{w,h,in}, slides[]} read once and shared.
               slide: {id, position, hidden, layoutId, background{kind,hex,
               alpha,luminance}, isDark, theme{ACCENT1:"#..",...},
               notes{objectId, text, markdown}, elements[]}.
               element: {id, kind, shapeType, placeholder, parentId, x,y,w,h
               (EMU, page space, rotation and groups applied), in{x,y,w,h},
               rotation, size, transform, fill{kind none|solid|image|inherit|
               other, hex, alpha, theme}, outline, autofit, text, runs[{start,
               end (UTF-16), text, fontFamily, weight, bold, italic, sizePt,
               color ("#hex" or "inherited"), colorTheme}]}.
               deck.select(sel) / deck.slide(sel): 1-based position, id,
               "3-7", array, predicate fn, {first}, {last}, {hidden},
               {with_notes}. slide.element(id), slide.find(pred),
               deck.element(id), deck.slideOf(elementId).
      emit(req | [reqs])   queue Slides API requests (nothing is sent yet)
      await commit()       apply everything queued so far as one batch,
                           re-read the deck and return it (also updates
                           `deck`). Only needed when a later phase must see
                           the result of an earlier one.
      helpers (also top-level names): rgb, color, themeColor, solid, inch,
               pt, toIn, toPt, newId, textStyle, styleText(el, style,
               range?), styleRuns(el, pred, style) (keeps each run's weight
               across a font change), setFill(el, hex|null, alpha?),
               setBackground(slide, hex), resize(el, {w,h,x,y,pin:"left"|
               "right"|"center", pinY:"top"|"bottom"|"middle"}), move(el, x,
               y), textBox(slide, {text,x,y,w,h,style,id?}) (sets autofit
               NONE), setNotes(slide, markdown, {mode}) (returns a request to
               emit). Friendly style keys: fontFamily, weight, bold, italic,
               underline, sizePt, color ("#hex" or {theme}).
               Geometry in textBox, resize and move is EMU: inch(n) converts.
               A width or height under 1 pt (inches passed as EMU) throws.
      console.log          captured into `logs`.
    `return x` delivers x as native JSON in `result` (truncated past
    `max_return_bytes`). Return a small summary, not the deck.

    Safety: `dry_run` is true by default and sends nothing; it returns a
    per-slide summary (request kinds, colours and fonts before/after) and
    stops at the first `commit()`, because later phases would build on writes
    that did not happen. A real apply sends each phase as ONE atomic
    batchUpdate (a failing request leaves that phase unapplied). Requests
    naming ids that are not in the deck, and not created earlier in the
    batch, are refused before any API call. Destructive kinds (deleteObject,
    deleteText, replaceAllText, ...) need confirm_destructive=true. Warnings
    flag font changes that drop an existing weight and size or font changes
    that likely overflow a fixed-size (autofit NONE) box.

    To apply a dry run, pass its `preview.plan_id` with dry_run=false and no
    script: the stored script and input re-run against the deck as it is now.

    Args:
      deck_url:            Slides URL or raw deck ID; the script is bound to it.
      script:              JavaScript source (function body; top-level
                           `return` and `await` allowed). Omit with plan_id.
      input:               Any JSON value; a JSON string is parsed once.
      dry_run:             Default true. Set false to apply.
      plan_id:             From a dry run's preview; applies that script and
                           input (and its limits) once, within 1 hour, to the
                           same deck. confirm_destructive is still needed here.
      confirm_destructive: Allow destructive request kinds.
      include_requests:    Include the full request list in the response.
      render_slides:       Slide selector (as in read_slides) overriding which
                           slides the receipt shows; up to 6.
      receipt:             After a real apply (never a dry run), attach
                           thumbnails of up to 3 touched slides: "medium"
                           (default, 800x450, about 480 tokens each),
                           "large" (1600x900) or "off". Others are listed in
                           `thumbnails.not_shown_slide_ids`. A failed
                           thumbnail is a warning; the write has landed.
      timeout_s:           Overall wall clock incl. every commit and
                           thumbnail. Default 300, max 1800.
      cpu_timeout_s:       Max pure script time between commits; kills
                           infinite loops. Default 30, max 300.
      max_requests:        Cap across all phases. Default 5000, max 20000.
      max_return_bytes:    Cap on the returned value. Default 8192, max 65536.

    Your MCP client has its own tool-call timeout; if it is shorter than
    `timeout_s`, the client's limit wins and the call may still be running
    server-side when the client gives up.

    Returns {result, receipt{applied_request_count, request_kinds,
    affected_slide_ids, destructive_kinds, phases} | preview{plan_id, ...},
    warnings, logs, isError, error{kind, message, line, column, request_index}}, plus
    `thumbnails{size, slide_ids, not_shown_slide_ids?, hint?}` when
    thumbnails are attached (then sent as a JSON text block followed by the
    images).
    """
    limits, limit_notes = scripting.clamp_limits({
        "timeout_s": timeout_s, "cpu_timeout_s": cpu_timeout_s,
        "max_requests": max_requests, "max_return_bytes": max_return_bytes,
    })
    receipts.check(receipt)
    deck_id = slides_api.deck_id_from_url(deck_url)
    if plan_id is not None and script is not None:
        return _plan_error(deck_id, dry_run, (
            "Pass either `script` (to run or dry-run it) or `plan_id` (to apply an "
            "earlier dry run), not both."))
    if plan_id is not None and dry_run:
        return _plan_error(deck_id, dry_run, (
            "`plan_id` applies an earlier dry run: call again with dry_run=false. "
            "To preview again, send the script instead."))
    if plan_id is None:
        if not isinstance(script, str) or not script.strip():
            raise ValueError("`script` must be non-empty JavaScript")
        value = scripting.parse_input(input)
    if not dry_run and (msg := writes.write_scope_error()):
        return {"deck_id": deck_id, "dry_run": False, "isError": True,
                "error": {"kind": "auth", "message": msg}}
    # The hosted store is a blocking Firestore round trip: keep it off the loop.
    store, caller = state_store.current(), auth.caller_id()
    if plan_id is not None:
        plan = await anyio.to_thread.run_sync(lambda: store.load_plan(
            plan_id, deck_id=deck_id, caller=caller))
        if plan is None:
            return _plan_error(deck_id, dry_run, _PLAN_GONE)
        script, value, limits = plan["script"], plan["input"], plan["limits"]

    out = await anyio.to_thread.run_sync(lambda: scripting.run(
        deck_id, script, value,
        dry_run=dry_run, confirm_destructive=confirm_destructive,
        include_requests=include_requests, limits=limits,
    ))
    deck_after = out.pop("_deck_after", None)
    if limit_notes:
        out.setdefault("warnings", []).extend(limit_notes)
    if dry_run and not out.get("isError"):
        out["preview"]["plan_id"] = await anyio.to_thread.run_sync(lambda: store.save_plan(
            {"script": script, "input": value, "limits": limits},
            deck_id=deck_id, caller=caller))
    if plan_id is not None and not out.get("isError"):
        # Applied: a second apply would repeat the edits on top of themselves.
        await anyio.to_thread.run_sync(lambda: store.expire_plan(plan_id))
    if dry_run or out.get("isError") or receipt == "off":
        return out

    def attach() -> Any:
        prez = deck_after or slides_api.get_presentation(deck_id)
        if render_slides is None:
            touched = (out.get("receipt") or {}).get("affected_slide_ids") or []
            return _attach_receipt(out, deck_id, receipts.in_deck_order(touched, prez), receipt)
        ids = _resolve_slide_ids(prez, render_slides)[:scripting.MAX_THUMBNAILS]
        return _attach_receipt(out, deck_id, ids, receipt, limit=scripting.MAX_THUMBNAILS)

    try:
        return await anyio.to_thread.run_sync(attach)
    except Exception as e:  # noqa: BLE001 - thumbnails are best effort after a write
        out.setdefault("warnings", []).append(f"thumbnails failed: {e}")
        return out


Fit = Literal["contain", "cover"]
_FIT_METHODS = {"contain": "CENTER_INSIDE", "cover": "CENTER_CROP"}
_GOOGLE_PREFIX_RE = re.compile(r"^(?:Slides API error \d+: )?Invalid requests\[\d+\]\.\w+: ")


def _place_error(kind: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"applied_request_count": 0, "warnings": [], "isError": True,
            "error": {"kind": kind, "message": message}, **extra}


def _marker_missing(marker: str, slide_id: str) -> str:
    return (f"Marker {marker!r} was not found in any shape on slide {slide_id}. Check the "
            f"text and the slide id. The marker must be in a shape on the slide itself: "
            f"a marker on the slide's layout or master is drawn on the slide but cannot "
            f"be replaced from it.")


def _box_pt(box: Any) -> tuple[float, float, float, float]:
    try:
        x, y, w, h = (float(box[k]) for k in ("x", "y", "width", "height"))
    except (TypeError, KeyError, ValueError) as e:
        raise ValueError(
            f"`box` must be {{x, y, width, height}} in points; got {box!r}") from e
    if w <= 0 or h <= 0:
        raise ValueError(f"`box` width and height must be positive; got {box!r}")
    return x, y, w, h


@_tool(_ADD)
async def place_image(
    deck_url: str,
    slide_id: str,
    svg: str | None = None,
    image_url: str | None = None,
    placeholder: str | None = None,
    box: dict[str, float] | None = None,
    fit: Fit | None = None,
    editable: bool = False,
    confirm_destructive: bool = False,
    receipt: receipts.Receipt = "medium",
) -> Any:
    """Put an SVG diagram or a public image onto a slide, in a placeholder or a box.

    Preferred workflow (placeholder first): the user prepares a reference
    slide with a box holding marker text such as `{{DIAGRAM}}`, laid out the
    way they want visuals to sit. Duplicate that slide (`exec_batch_update`
    with `duplicateObject`), then call this with the new slide's id and the
    marker: the image replaces the box and keeps its object id. With no
    placeholder, copy a box from a reference slide (`read_slides(...,
    detail="raw")`) and pass it as `box`.

    Placeholder rules:
      - The marker box must be a shape on the slide itself. Markers on a
        layout or master are not reachable and are reported as not found.
      - Give the marker box no outline: the placed image inherits it.
      - Every shape on the slide containing the marker is replaced.
      - Destructive (replaceAllShapesWithImage): needs confirm_destructive.

    SVG: rasterised to PNG at the target box's aspect ratio (the drawing is
    centred in it), hosted briefly for Google to fetch, then deleted. Needs
    the hosted server (`slides-mcp serve-http` with an image bucket). Use
    generic font families (sans-serif, serif, monospace); named fonts fall
    back, CJK renders as empty boxes, and text with no font is reported in
    `warnings`. Only in-document references (#id) and data: URIs are allowed.

    image_url: a PNG, JPEG or GIF Google can fetch (public, under 50 MB and
    25 megapixels). Works over stdio too. Google keeps the image's own
    aspect ratio, so in a box the stored size can be smaller than the box.

    editable=true: the SVG becomes native Slides shapes the user can edit,
    fitted into the box (centred, aspect kept) and grouped when there are two
    or more. No bucket needed; works over stdio. A placeholder box is deleted
    (deleteObject, needs confirm_destructive) and the drawing takes its place.
    Supported subset, nothing else:
      - `svg` with viewBox (or numeric width/height); `g` with translate()
        and scale() only; presentation attributes inherit from `g`.
      - `rect` (rx/ry gives a rounded rectangle), `circle`, `ellipse`.
      - `line`, with marker-start / marker-end pointing at a `defs > marker`
        holding exactly one path, polygon, polyline or circle: it picks the
        arrowhead style (filled or open arrow, filled or open circle).
      - `text` with plain text only: font-family, font-size, font-weight,
        font-style, text-anchor, dominant-baseline. The first font family is
        used; generic names become Arial, Times New Roman or Courier New.
      - fill, stroke, stroke-width, stroke-dasharray, fill-opacity,
        stroke-opacity, opacity, also inside style=""; colours as hex,
        rgb() or basic names.
    Known losses: corner radius is Slides' fixed one; any dash array becomes
    one dash style; arrowhead size follows the line weight.
    Anything else (path, polyline, polygon, gradients, patterns, filters,
    clip paths, masks, rotate/skew/matrix, tspan, image, use, zero-size
    elements) is refused before any write, every problem listed in
    `error.message`; nothing is drawn. Simplify the SVG or use
    editable=false.

    Args:
      deck_url:            Slides URL or raw deck ID.
      slide_id:            The slide to place on (from `get_deck_outline`).
      svg:                 SVG markup. Exactly one of svg / image_url.
      image_url:           Public image URL.
      placeholder:         Marker text in a box on that slide. Exactly one
                           of placeholder / box.
      box:                 {x, y, width, height} in points (a 16:9 slide is
                           720 x 405).
      fit:                 Placeholder only. "contain" (default): no crop;
                           the box shrinks to the image's aspect ratio,
                           centred. "cover": the box is kept and the image
                           cropped to fill it. Editable output is always
                           "contain".
      editable:            SVG only. True draws native, editable shapes
                           (subset above) instead of an image.
      confirm_destructive: Required True for a placeholder target.
      receipt:             Thumbnail of the slide after the write: "medium"
                           (default), "large" or "off".

    Returns the `exec_batch_update` reply (post-state summary, thumbnails)
    plus `placed{slide_id, object_ids, source, target}`; editable output adds
    `editable: true`, and object_ids are the groups (or the lone element).
    Errors come back with `isError: true` and `error{kind, message}`; kind is
    hosting, svg, target, marker or google (Google's reason for rejecting it).
    """
    receipts.check(receipt)
    if (svg is None) == (image_url is None):
        raise ValueError("Pass exactly one of `svg` or `image_url`.")
    if editable and svg is None:
        raise ValueError("`editable=true` converts SVG into native shapes; it needs `svg`, "
                         "not `image_url`.")
    if (placeholder is None) == (box is None):
        raise ValueError("Pass exactly one target with `slide_id`: `placeholder` "
                         "(marker text) or `box` ({x, y, width, height} in points).")
    if fit is not None and placeholder is None:
        raise ValueError("`fit` applies to placeholder targets only; a box always keeps "
                         "the image's aspect ratio inside the box.")
    if fit is not None and fit not in _FIT_METHODS:
        raise ValueError(f"fit must be contain|cover; got {fit!r}")
    if editable and fit == "cover":
        raise ValueError("`fit=\"cover\"` crops an image; editable shapes always fit inside "
                         "the placeholder box (contain).")
    box_pt = _box_pt(box) if box is not None else None
    if placeholder is not None and not confirm_destructive:
        return _refusal(["deleteObject" if editable else "replaceAllShapesWithImage"])
    if msg := writes.write_scope_error():
        return _place_error("auth", msg)
    if editable:
        assert svg is not None
        return await anyio.to_thread.run_sync(lambda: _place_editable(
            deck_url, slide_id, svg=svg, placeholder=placeholder, box_pt=box_pt,
            receipt=receipt))
    return await anyio.to_thread.run_sync(lambda: _place_image(
        deck_url, slide_id, svg=svg, image_url=image_url, placeholder=placeholder,
        box_pt=box_pt, fit=fit or "contain", receipt=receipt))


def _place_image(
    deck_url: str,
    slide_id: str,
    *,
    svg: str | None,
    image_url: str | None,
    placeholder: str | None,
    box_pt: tuple[float, float, float, float] | None,
    fit: str,
    receipt: str,
) -> Any:
    """`place_image` after its arguments are checked; runs off the event loop."""
    store = state_store.current()
    if svg is not None:
        try:
            store.check_image_hosting()
        except state_store.ImageHostingUnavailable as e:
            return _place_error("hosting", str(e))

    deck_id = slides_api.deck_id_from_url(deck_url)
    prez = slides_api.get_presentation(deck_id)
    slide = next((s for s in prez.get("slides", []) or [] if s["objectId"] == slide_id), None)
    if slide is None:
        return _place_error("target", f"No slide {slide_id!r} in this deck; slide ids come "
                                      f"from get_deck_outline.")
    if placeholder is not None:
        boxes = [s for s in normalize.flatten(_slide_shapes(normalize.DeckContext(prez), slide))
                 if placeholder in (s.text or "")]
        if not boxes:
            return _place_error("marker", _marker_missing(placeholder, slide_id))
        width_pt, height_pt = boxes[0].w_in * 72, boxes[0].h_in * 72
    else:
        assert box_pt is not None
        width_pt, height_pt = box_pt[2], box_pt[3]

    warnings: list[str] = []
    hosted: state_store.HostedImage | None = None
    try:
        if svg is not None:
            try:
                raster = svg_raster.rasterise(svg, width_pt, height_pt)
                hosted = store.put_image(raster.png, content_type="image/png")
            except svg_raster.SvgError as e:
                return _place_error("svg", str(e))
            except state_store.ImageHostingUnavailable as e:
                return _place_error("hosting", str(e))
            warnings.extend(raster.warnings)
            url = hosted.url
        else:
            assert image_url is not None
            url = image_url

        if placeholder is not None:
            request: dict[str, Any] = {"replaceAllShapesWithImage": {
                "containsText": {"text": placeholder, "matchCase": True},
                "imageUrl": url,
                "imageReplaceMethod": _FIT_METHODS[fit],
                "pageObjectIds": [slide_id],
            }}
        else:
            assert box_pt is not None
            x, y, w, h = box_pt
            request = {"createImage": {"url": url, "elementProperties": {
                "pageObjectId": slide_id,
                "size": {"width": {"magnitude": w, "unit": "PT"},
                         "height": {"magnitude": h, "unit": "PT"}},
                "transform": {"scaleX": 1, "scaleY": 1, "translateX": x,
                              "translateY": y, "unit": "PT"},
            }}}
        try:
            out, touched = _apply_requests(
                deck_url, [request], dry_run=False, confirm_destructive=True,
                post_state="summary", want_slides=receipt != "off", tool="place_image")
        except slides_api.SlidesApiError as e:
            return _place_error("google", _GOOGLE_PREFIX_RE.sub("", str(e)))
    finally:
        if hosted is not None:
            try:
                store.delete_image(hosted.handle)
            except Exception as e:  # noqa: BLE001 - the bucket's expiry rule is the backstop
                warnings.append(f"The temporary image was not deleted ({e}); the bucket's "
                                f"expiry rule removes it.")

    reply = out["replies"][0] if out.get("replies") else {}
    if placeholder is not None:
        if not reply.get("replaceAllShapesWithImage", {}).get("occurrencesChanged"):
            return _place_error("marker", _marker_missing(placeholder, slide_id),
                                warnings=warnings)
        object_ids = [b.object_id for b in boxes]
    else:
        object_ids = [oid for oid in [reply.get("createImage", {}).get("objectId")] if oid]
        if image_url is not None:
            warnings.append("Google fits the image inside the box at the image's own aspect "
                            "ratio, centred, so its stored size can be smaller than the box.")
    out["placed"] = {"slide_id": slide_id, "object_ids": object_ids,
                     "source": "svg" if svg is not None else "image_url",
                     "target": "placeholder" if placeholder is not None else "box"}
    out["warnings"] = [*out.get("warnings", []), *warnings]
    return _attach_receipt(out, deck_url, touched, receipt)


def _place_editable(
    deck_url: str,
    slide_id: str,
    *,
    svg: str,
    placeholder: str | None,
    box_pt: tuple[float, float, float, float] | None,
    receipt: str,
) -> Any:
    """`place_image(editable=True)` after its arguments are checked.

    Converts before reading the deck, so an unsupported SVG costs no API call.
    For a placeholder, every shape on the slide holding the marker is deleted
    and gets its own drawing in its box, all in one batch.
    """
    try:
        svg_native.convert(svg, slide_id, box_pt or (0, 0, 1, 1))  # refuse early
    except svg_raster.SvgError as e:
        return _place_error("svg", str(e))
    deck_id = slides_api.deck_id_from_url(deck_url)
    prez = slides_api.get_presentation(deck_id)
    slide = next((s for s in prez.get("slides", []) or [] if s["objectId"] == slide_id), None)
    if slide is None:
        return _place_error("target", f"No slide {slide_id!r} in this deck; slide ids come "
                                      f"from get_deck_outline.")
    requests: list[dict[str, Any]] = []
    object_ids: list[str] = []
    if placeholder is not None:
        boxes = [s for s in normalize.flatten(_slide_shapes(normalize.DeckContext(prez), slide))
                 if placeholder in (s.text or "")]
        if not boxes:
            return _place_error("marker", _marker_missing(placeholder, slide_id))
        targets = [(b.left_in * 72, b.top_in * 72, b.w_in * 72, b.h_in * 72) for b in boxes]
        requests += [{"deleteObject": {"objectId": b.object_id}} for b in boxes]
    else:
        assert box_pt is not None
        targets = [box_pt]
    for target in targets:
        drawing = svg_native.convert(svg, slide_id, target)
        requests += drawing.requests
        object_ids += drawing.object_ids
    try:
        out, touched = _apply_requests(
            deck_url, requests, dry_run=False, confirm_destructive=True,
            post_state="summary", want_slides=receipt != "off", tool="place_image")
    except slides_api.SlidesApiError as e:
        return _place_error("google", _GOOGLE_PREFIX_RE.sub("", str(e)))
    out["placed"] = {"slide_id": slide_id, "object_ids": object_ids, "source": "svg",
                     "target": "placeholder" if placeholder is not None else "box",
                     "editable": True}
    return _attach_receipt(out, deck_url, touched, receipt)


def main() -> None:
    """Entry point used by both the CLI and `python -m slides_mcp.server`."""
    mcp.run(show_banner=False)


if __name__ == "__main__":
    main()
