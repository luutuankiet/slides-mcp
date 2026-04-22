"""Google Slides REST wrapper.

Public surface mirrors what the MCP tool layer needs:
  - deck_id_from_url(url)          → parsed ID
  - get_presentation(deck_id)      → whole presentation w/ minimal FieldMask
  - get_slide(deck_id, slide_id)   → one slide + its notes
  - batch_update(deck_id, reqs)    → apply requests, return reply
  - get_thumbnail(deck_id, slide_id, size) → thumbnail PNG URL

All calls use a cached googleapiclient service built from token.json.
"""
from __future__ import annotations

import re
import urllib.request
from functools import cache
from typing import Any
from urllib.parse import urlparse

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .auth import load_credentials

# Minimal field masks — what we actually need for projection + classification.
DECK_OUTLINE_FIELDS = (
    "presentationId,title,revisionId,"
    "slides.objectId,"
    "slides.slideProperties.layoutObjectId,"
    "slides.pageElements("
    "objectId,size,transform,"
    "shape.shapeType,"
    "shape.shapeProperties.shapeBackgroundFill.solidFill.color,"
    "shape.shapeProperties.outline.outlineFill.solidFill.color,"
    "shape.text.textElements.textRun,"
    "image.contentUrl,image.sourceUrl,"
    "line.lineType,line.lineProperties.lineFill.solidFill.color,"
    "table.rows,table.columns,"
    "elementGroup.children"
    ")"
)

SLIDE_FULL_FIELDS = (
    "objectId,"
    "slideProperties.notesPage.pageElements("
    "objectId,shape.placeholder,shape.text.textElements.textRun.content"
    "),"
    "pageElements("
    "objectId,size,transform,"
    "shape.shapeType,"
    "shape.shapeProperties.shapeBackgroundFill.solidFill.color,"
    "shape.shapeProperties.outline.outlineFill.solidFill.color,"
    "shape.text.textElements.textRun,"
    "image.contentUrl,image.sourceUrl,"
    "line.lineType,line.lineProperties.lineFill.solidFill.color,"
    "table.rows,table.columns,"
    "elementGroup.children"
    ")"
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
    # assume it's already an ID (Google uses 44-char IDs but be lenient)
    if re.fullmatch(r"[a-zA-Z0-9_-]+", url_or_id):
        return url_or_id
    raise ValueError(f"Unrecognized deck url or id: {url_or_id}")


@cache
def _slides_service():
    creds = load_credentials()
    return build("slides", "v1", credentials=creds, cache_discovery=False)


@cache
def _drive_service():
    creds = load_credentials()
    return build("drive", "v3", credentials=creds, cache_discovery=False)


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
        raise SlidesApiError(
            f"Slides API error {status}: {e.reason or str(e)}",
            status=int(status) if status else None,
            reason=e.reason,
        ) from e


def get_presentation(deck_id: str, fields: str = DECK_OUTLINE_FIELDS) -> dict[str, Any]:
    """Fetch presentation with the given FieldMask."""
    svc = _slides_service()
    return _call(svc.presentations().get, presentationId=deck_id, fields=fields)


def get_slide(deck_id: str, slide_id: str) -> dict[str, Any]:
    """Fetch one slide with full geometry + text + notes fields."""
    svc = _slides_service()
    page = _call(svc.presentations().pages().get,
                 presentationId=deck_id, pageObjectId=slide_id, fields=SLIDE_FULL_FIELDS)
    return page


def batch_update(deck_id: str, requests: list[dict[str, Any]]) -> dict[str, Any]:
    """Apply a list of Slides API Request objects."""
    svc = _slides_service()
    return _call(
        svc.presentations().batchUpdate,
        presentationId=deck_id,
        body={"requests": requests},
    )


def get_thumbnail(
    deck_id: str,
    slide_id: str,
    mime: str = "PNG",
    size: str = "MEDIUM",
) -> str:
    """Return the contentUrl for a rendered slide thumbnail. Valid ~30min."""
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
    """Fetch rendered thumbnail PNG as raw bytes. Used for MCP ImageContent."""
    url = get_thumbnail(deck_id, slide_id, mime="PNG", size=size)
    with urllib.request.urlopen(url, timeout=30) as resp:
        return resp.read()


def copy_deck(deck_id: str, new_title: str) -> str:
    """Copy a deck via Drive API. Returns new deck ID."""
    drive = _drive_service()
    result = _call(drive.files().copy, fileId=deck_id, body={"name": new_title})
    return result["id"]
