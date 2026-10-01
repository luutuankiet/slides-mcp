"""Google Slides REST wrapper — read surface + curated write (v2.1).

Public surface mirrors what the MCP tool layer needs:
  - deck_id_from_url(url)          → parsed ID
  - get_presentation(deck_id)      → whole presentation w/ minimal FieldMask
  - get_slide(deck_id, slide_id)   → one slide + its notes
  - get_thumbnail(deck_id, ...)    → thumbnail PNG URL
  - get_thumbnail_bytes(...)       → thumbnail PNG bytes
  - batch_update(deck_id, requests) → raw Slides API batchUpdate (v2.1)

v1's `copy_deck` (Drive scope) stays dropped — v2.1 doesn't restore deck cloning.
Every call gets its client from `_slides_service()`: stdio caches one built
from token.json; HTTP mode builds one per call from the caller's token.
"""
from __future__ import annotations

import re
import urllib.request
from functools import cache
from typing import Any
from urllib.parse import urlparse

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from . import auth

# Field masks. One element mask serves every read path so the read tools and
# `run_deck_script` see the same model. Paragraph styles are deliberately left
# out (they were most of the payload); only bullets are kept, for notes.
_TEXT_FIELDS = (
    "text.textElements(startIndex,endIndex,paragraphMarker.bullet,"
    "textRun(content,style(bold,italic,underline,fontFamily,fontSize,"
    "foregroundColor,weightedFontFamily,link)))"
)
_ELEMENT_LEAF_FIELDS = (
    "objectId,size,transform,"
    "shape(shapeType,placeholder,"
    "shapeProperties(shapeBackgroundFill,outline(outlineFill,weight,propertyState),autofit),"
    f"{_TEXT_FIELDS}),"
    "image(contentUrl,sourceUrl),"
    "line(lineType,lineProperties(lineFill,weight)),"
    "table(rows,columns),"
    "sheetsChart.chartId"
)
# Groups nest: two levels get the narrow mask, anything deeper comes back whole.
ELEMENT_FIELDS = (
    f"{_ELEMENT_LEAF_FIELDS},"
    f"elementGroup.children({_ELEMENT_LEAF_FIELDS},elementGroup)"
)
_NOTES_FIELDS = (
    "notesPage(notesProperties.speakerNotesObjectId,"
    f"pageElements(objectId,shape(placeholder,{_TEXT_FIELDS})))"
)
_PAGE_PROPS = "pageProperties(pageBackgroundFill,colorScheme)"

DECK_FIELDS = (
    "presentationId,title,revisionId,pageSize,"
    f"masters(objectId,{_PAGE_PROPS}),"
    f"layouts(objectId,layoutProperties(masterObjectId,name),{_PAGE_PROPS}),"
    f"slides(objectId,{_PAGE_PROPS},"
    f"slideProperties(isSkipped,layoutObjectId,masterObjectId,{_NOTES_FIELDS}),"
    f"pageElements({ELEMENT_FIELDS}))"
)
# Kept for callers that imported the old name.
DECK_OUTLINE_FIELDS = DECK_FIELDS

SLIDE_FULL_FIELDS = (
    f"objectId,{_PAGE_PROPS},"
    f"slideProperties(isSkipped,layoutObjectId,masterObjectId,{_NOTES_FIELDS}),"
    f"pageElements({ELEMENT_FIELDS})"
)

_URL_PATTERNS = [
    re.compile(r"/presentation/d/([a-zA-Z0-9_-]+)"),
    re.compile(r"[?&]id=([a-zA-Z0-9_-]+)"),
]


def deck_id_from_url(url_or_id: str) -> str:
    """Accept either a Slides URL or a raw deck ID. Returns the ID."""
    if not url_or_id:
        raise ValueError("empty deck url/id")
    parsed = urlparse(url_or_id)
    if parsed.scheme in ("http", "https"):
        for pat in _URL_PATTERNS:
            if m := pat.search(url_or_id):
                return m.group(1)
        raise ValueError(f"Cannot parse deck id from URL: {url_or_id}")
    if re.fullmatch(r"[a-zA-Z0-9_-]+", url_or_id):
        return url_or_id
    raise ValueError(f"Unrecognized deck url or id: {url_or_id}")


def _slides_service():
    """The Slides client for the current caller."""
    if auth.http_mode():
        # Per call, never cached: one caller's client must not serve another.
        return build("slides", "v1", credentials=auth.caller_credentials(),
                     cache_discovery=False)
    return _stdio_service()


@cache
def _stdio_service():
    creds = auth.load_credentials()
    return build("slides", "v1", credentials=creds, cache_discovery=False)


class SlidesApiError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, reason: str | None = None):
        super().__init__(message)
        self.status = status
        self.reason = reason


def _call(fn, **kwargs):
    try:
        return fn(**kwargs).execute()
    except HttpError as e:
        status = getattr(e.resp, "status", None)
        message = f"Slides API error {status}: {e.reason or str(e)}"
        if auth.http_mode() and str(status) == "401":
            message += (". Google no longer accepts this caller's sign-in. Ask the user "
                        "to re-authenticate this MCP server in their MCP client, then retry.")
        raise SlidesApiError(
            message,
            status=int(status) if status else None,
            reason=e.reason,
        ) from e


def get_presentation(deck_id: str, fields: str = DECK_FIELDS) -> dict[str, Any]:
    """Fetch presentation with the given FieldMask."""
    svc = _slides_service()
    return _call(svc.presentations().get, presentationId=deck_id, fields=fields)


def get_slide(deck_id: str, slide_id: str) -> dict[str, Any]:
    """Fetch one slide with full text + notes fields."""
    svc = _slides_service()
    return _call(
        svc.presentations().pages().get,
        presentationId=deck_id,
        pageObjectId=slide_id,
        fields=SLIDE_FULL_FIELDS,
    )


def get_thumbnail(
    deck_id: str,
    slide_id: str,
    mime: str = "PNG",
    size: str = "MEDIUM",
) -> str:
    """Return the contentUrl for a rendered slide thumbnail. URL valid ~30 min."""
    svc = _slides_service()
    resp = _call(
        svc.presentations().pages().getThumbnail,
        presentationId=deck_id,
        pageObjectId=slide_id,
        thumbnailProperties_mimeType=mime,
        thumbnailProperties_thumbnailSize=size,
    )
    return resp["contentUrl"]


def get_thumbnail_bytes(
    deck_id: str,
    slide_id: str,
    size: str = "MEDIUM",
) -> bytes:
    """Fetch rendered thumbnail PNG as raw bytes for MCP ImageContent."""
    url = get_thumbnail(deck_id, slide_id, mime="PNG", size=size)
    with urllib.request.urlopen(url, timeout=30) as resp:
        return resp.read()


def batch_update(deck_id: str, requests: list[dict]) -> dict:
    """Apply a list of Slides API Requests to the deck (v2.1 write surface).

    Returns the raw `BatchUpdatePresentationResponse`:
      {presentationId, replies[], writeControl}
    where `replies` is positionally aligned 1:1 with `requests` and contains
    server-generated identifiers for `createShape`, `createSlide`,
    `duplicateObject`, etc. Empty dict for no-op replies.

    This is a THIN REST wrapper. Caller (server.py `exec_batch_update`) owns:
      - dry-run preview
      - destructive-kind allowlist (`deleteObject`, `replaceAllText`, etc.)
      - post-state re-read + projection (the v2.1 differentiator)
      - OAuth scope error mapping (403 → actionable re-consent message)

    Args:
      deck_id: Slides deck ID (use `deck_id_from_url` to parse a URL).
      requests: Ordered list of Slides API Request dicts. Each must match the
                Slides API Request schema (one top-level kind per dict, e.g.
                {"createShape": {...}}, {"replaceAllText": {...}}).

    Raises:
      SlidesApiError: wraps any HttpError from the Slides API. The wrapped
                      error includes status + reason for caller mapping.
    """
    svc = _slides_service()
    return _call(
        svc.presentations().batchUpdate,
        presentationId=deck_id,
        body={"requests": requests},
    )
