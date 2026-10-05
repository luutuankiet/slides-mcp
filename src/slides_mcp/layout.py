"""Layout warnings: plain-text checks on the elements a write just created.

Run on the deck as re-read after the write. Text, not a thumbnail, is the one
check every client's model can read: ChatGPT's model does not see MCP images.
Only elements this write created are checked, so existing clutter in a deck
does not repeat on every write.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from . import normalize, writes

# Google's own size for a shape whose requested size it ignores (missing, or
# far too small, as inches passed as EMU are): 3,000,000 EMU square.
DEFAULT_SIDE_IN = 3000000 / 914400
_TOL_IN = 0.01
STACK_MIN = 3


def _at(s: normalize.FlatShape) -> str:
    return f"at [{s.left_in:.2f}, {s.top_in:.2f}, {s.w_in:.2f}, {s.h_in:.2f}] in"


def created_ids(requests: list[dict[str, Any]], replies: list[dict[str, Any]]) -> list[str]:
    """Ids the batch named on creates, plus ids Google assigned in its replies."""
    ids: list[str] = []
    for req in requests:
        if req:
            ids.extend(writes.created_ids(req))
    for rep in replies or []:
        for body in (rep or {}).values():
            if isinstance(body, dict) and isinstance(body.get("objectId"), str):
                ids.append(body["objectId"])
    return list(dict.fromkeys(ids))


def check(created: list[str], prez: dict[str, Any]) -> list[str]:
    """One sentence per finding about the `created` elements in `prez`."""
    wanted = set(created)
    if not wanted:
        return []
    page = prez.get("pageSize") or {}
    pw = (normalize._dim_emu(page.get("width")) or 9144000.0) / 914400
    ph = (normalize._dim_emu(page.get("height")) or 5143500.0) / 914400
    out: list[str] = []
    for slide in prez.get("slides") or []:
        mine = [s for s in normalize.flatten(normalize.normalize_page(slide))
                if s.object_id in wanted]
        stacks: dict[tuple[float, ...], list[str]] = defaultdict(list)
        for s in mine:
            if (abs(s.w_in - DEFAULT_SIDE_IN) < _TOL_IN
                    and abs(s.h_in - DEFAULT_SIDE_IN) < _TOL_IN):
                out.append(f"{s.object_id} is a {DEFAULT_SIDE_IN:.3f} in square, Google's default "
                           f"size; it uses that when the size sent is missing or too small "
                           f"(inches passed as EMU?). {s.object_id} {_at(s)}.")
            right, bottom = s.left_in + s.w_in, s.top_in + s.h_in
            if right < 0 or bottom < 0 or s.left_in > pw or s.top_in > ph:
                out.append(f"{s.object_id} is fully off the {pw:.2f} x {ph:.2f} in page, "
                           f"{_at(s)}.")
            elif (s.left_in < -_TOL_IN or s.top_in < -_TOL_IN
                  or right > pw + _TOL_IN or bottom > ph + _TOL_IN):
                out.append(f"{s.object_id} is partly off the {pw:.2f} x {ph:.2f} in page, "
                           f"{_at(s)}.")
            key = tuple(round(v, 2) for v in (s.left_in, s.top_in, s.w_in, s.h_in))
            stacks[key].append(s.object_id)
        for key, ids in stacks.items():
            if len(ids) >= STACK_MIN:
                shown = ", ".join(ids[:5]) + (f" (+{len(ids) - 5} more)" if len(ids) > 5 else "")
                out.append(f"{len(ids)} new elements on slide {slide['objectId']} sit exactly on "
                           f"top of each other at [{', '.join(f'{v:.2f}' for v in key)}] in: "
                           f"{shown}.")
    return out
