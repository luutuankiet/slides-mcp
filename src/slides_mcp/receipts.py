"""Receipts: thumbnails of the slides a write touched, returned with the write.

A write tool hands over the slides it touched; this renders up to
`MAX_SLIDES` of them at the size the caller picked and lists the rest. A
thumbnail that fails becomes a warning: the write has already landed.
"""
from __future__ import annotations

import contextvars
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Literal

from . import slides_api

Receipt = Literal["off", "medium", "large"]

MAX_SLIDES = 3
# Google's own thumbnail sizes. SMALL (200 px wide) is too small to read.
SIZES = {"medium": "MEDIUM", "large": "LARGE"}


def check(receipt: str) -> None:
    if receipt not in ("off", *SIZES):
        raise ValueError(f"receipt must be off|medium|large; got {receipt!r}")


def in_deck_order(slide_ids: list[str], prez: dict[str, Any]) -> list[str]:
    """`slide_ids` that still exist after the write, in deck order."""
    wanted = set(slide_ids)
    return [s["objectId"] for s in prez.get("slides", []) or [] if s["objectId"] in wanted]


def _failure(slide_id: str, err: Exception) -> str:
    if getattr(err, "status", None) == 429:
        return (f"thumbnail of {slide_id} skipped: Google's thumbnail limit was hit "
                f"(60 requests a minute per user, shared with render_thumbnail). "
                f"The write succeeded; call render_thumbnail later to see it.")
    return f"thumbnail of {slide_id} failed: {err or type(err).__name__}. The write succeeded."


def render(
    deck_id: str,
    slide_ids: list[str],
    receipt: str,
    *,
    limit: int = MAX_SLIDES,
) -> tuple[dict[str, Any], list[bytes], list[str]]:
    """Render up to `limit` slides concurrently, each tried once.

    Returns (reply field, PNG bytes in slide order, warnings).
    """
    shown, rest = slide_ids[:limit], slide_ids[limit:]
    size = SIZES[receipt]

    def fetch(sid: str) -> bytes | Exception:
        try:
            return slides_api.get_thumbnail_bytes(deck_id, sid, size=size)
        except Exception as e:  # noqa: BLE001 - a receipt never fails the write
            return e

    pngs: list[bytes] = []
    rendered: list[str] = []
    warnings: list[str] = []
    if shown:
        # Each worker runs in a copy of the caller's context: HTTP mode finds
        # the signed-in caller's token there.
        with ThreadPoolExecutor(max_workers=len(shown)) as pool:
            futures = [pool.submit(contextvars.copy_context().run, fetch, sid)
                       for sid in shown]
            results = [f.result() for f in futures]
        for sid, res in zip(shown, results, strict=True):
            if isinstance(res, Exception):
                warnings.append(_failure(sid, res))
            else:
                pngs.append(res)
                rendered.append(sid)
    field: dict[str, Any] = {"size": size, "slide_ids": rendered}
    if rest:
        field["not_shown_slide_ids"] = rest
        field["hint"] = (f"Only {limit} thumbnails per write; call render_thumbnail "
                         f"for the others.")
    return field, pngs, warnings
