"""The one write path. Every tool that changes a deck ends up in `apply_batch`.

It owns the 403 re-consent message, the audit line, and the helpers that
look at a request list without sending it: destructive kinds, affected
slides, unknown object ids, sizes too small to be EMU.
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
import time
from typing import Any

from . import auth, deck_model, slides_api

# Slides API Request kinds that mutate or destroy existing content. Applying
# any of these needs `confirm_destructive=True`.
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

WRITE_SCOPES = frozenset({
    "https://www.googleapis.com/auth/presentations",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/drive.file",
})

AUDIT_ENV = "SLIDES_MCP_AUDIT_LOG"


def request_kinds(requests: list[dict[str, Any]]) -> list[str]:
    return [next(iter(r.keys())) for r in requests if r]


def destructive_kinds(requests: list[dict[str, Any]]) -> list[str]:
    return sorted({k for k in request_kinds(requests) if k in DESTRUCTIVE_KINDS})


def write_scope_error() -> str | None:
    """A message when token.json's granted scopes cannot write, else None.

    An unreadable or scope-less token returns None: the API call itself is
    then the judge, and its 403 carries the same advice. HTTP mode always
    returns None: the deployer's OAuth app grants write scope to every caller.
    """
    if auth.http_mode():
        return None
    try:
        scopes = set(auth.credentials_info().get("scopes") or [])
    except Exception:  # noqa: BLE001 - diagnostics only
        return None
    if scopes and not scopes & WRITE_SCOPES:
        return (
            "token.json is read-only (granted scopes: "
            f"{sorted(scopes)}). Re-run `slides-mcp-auth` to grant "
            "`https://www.googleapis.com/auth/presentations`, then retry."
        )
    return None


def audit(record: dict[str, Any]) -> None:
    """One JSON line per applied batch: stderr always, plus $SLIDES_MCP_AUDIT_LOG."""
    line = json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **record})
    print(f"slides-mcp audit {line}", file=sys.stderr, flush=True)
    if path := os.environ.get(AUDIT_ENV):
        try:
            with open(os.path.expanduser(path), "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError as e:
            print(f"slides-mcp audit: cannot append to {path}: {e}", file=sys.stderr)


def log_call(tool: str, seconds: float, ok: bool) -> None:
    """One stderr line per tool call, for usage questions. No arguments, no content."""
    line = json.dumps({"tool": tool, "ms": round(seconds * 1000), "ok": ok})
    print(f"slides-mcp call {line}", file=sys.stderr, flush=True)


_REQ_INDEX_RE = re.compile(r"requests\[(\d+)\]")


def failing_request_index(message: str) -> int | None:
    m = _REQ_INDEX_RE.search(message or "")
    return int(m.group(1)) if m else None


def apply_batch(
    deck_id: str,
    requests: list[dict[str, Any]],
    *,
    tool: str,
    extra_audit: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One atomic batchUpdate. A failing request leaves the deck unchanged."""
    problems = size_problems(requests)
    if problems:
        raise ValueError("Refused, nothing was written: " + "; ".join(problems))
    try:
        resp = slides_api.batch_update(deck_id, requests)
    except slides_api.SlidesApiError as e:
        if e.status == 403 and auth.http_mode():
            raise slides_api.SlidesApiError(
                f"{e}. Check the caller can edit this deck. If they can, ask the user "
                f"to sign in to this MCP server again from their MCP client.",
                status=e.status,
                reason=e.reason,
            ) from e
        if e.status == 403:
            raise slides_api.SlidesApiError(
                f"{e}. If your token was minted by slides-mcp v2.0+, it likely "
                f"has `presentations.readonly` scope only. Re-run "
                f"`slides-mcp-auth` to mint a token with write scope.",
                status=e.status,
                reason=e.reason,
            ) from e
        raise
    kinds = request_kinds(requests)
    counts: dict[str, int] = {}
    for k in kinds:
        counts[k] = counts.get(k, 0) + 1
    audit({"tool": tool, "deck_id": deck_id, "request_count": len(requests),
           "kinds": counts, **(extra_audit or {})})
    return resp


# ---- looking at a request list ------------------------------------------------

_CREATE_KINDS = {
    "createShape", "createImage", "createLine", "createTable", "createVideo",
    "createSheetsChart", "createSlide",
}
_REF_LIST_KEYS = ("objectIds", "childrenObjectIds", "pageObjectIds", "slideObjectIds")


def created_ids(req: dict[str, Any]) -> list[str]:
    (kind, body), = req.items()
    if not isinstance(body, dict):
        return []
    out: list[str] = []
    if kind in _CREATE_KINDS and body.get("objectId"):
        out.append(body["objectId"])
    if kind == "groupObjects" and body.get("groupObjectId"):
        out.append(body["groupObjectId"])
    if kind == "duplicateObject":
        out.extend((body.get("objectIds") or {}).values())
    return out


def referenced_ids(req: dict[str, Any]) -> list[str]:
    (kind, body), = req.items()
    if not isinstance(body, dict):
        return []
    refs: list[str] = []
    if kind not in _CREATE_KINDS and kind != "groupObjects" and body.get("objectId"):
        refs.append(body["objectId"])
    if pid := body.get("pageObjectId"):
        refs.append(pid)
    ep = body.get("elementProperties")
    if isinstance(ep, dict) and ep.get("pageObjectId"):
        refs.append(ep["pageObjectId"])
    for key in _REF_LIST_KEYS:
        val = body.get(key)
        if isinstance(val, list):
            refs.extend(v for v in val if isinstance(v, str))
    if kind == "duplicateObject":
        refs.extend((body.get("objectIds") or {}).keys())
    return refs


def unknown_ids(requests: list[dict[str, Any]], prez: dict[str, Any]) -> list[str]:
    """Problems, one string per request that names an id not in the deck and
    not created earlier in the same batch."""
    known = set(deck_model.id_index(prez))
    problems: list[str] = []
    for i, req in enumerate(requests):
        if not req:
            continue
        kind = next(iter(req))
        missing = [r for r in referenced_ids(req) if r not in known]
        if missing:
            problems.append(f"request #{i} ({kind}) names unknown object id(s) {missing}")
        known.update(created_ids(req))
    return problems


EMU_PER_IN = 914400
EMU_PER_PT = 12700
# createLine is exempt: a horizontal or vertical rule has a zero dimension.
_SIZED_CREATES = ("createShape", "createImage", "createVideo", "createSheetsChart")
UNITS_HINT = "Geometry is EMU: 1 in = 914400 EMU, 1 pt = 12700 EMU."


def size_problems(requests: list[dict[str, Any]]) -> list[str]:
    """One string per create whose width or height is not a number or is under
    1 pt. Google stores a tiny size as its 3,000,000 EMU default square without
    an error, which is what inches passed as EMU produce."""
    problems: list[str] = []
    for i, req in enumerate(requests):
        if not req:
            continue
        (kind, body), = req.items()
        if kind not in _SIZED_CREATES or not isinstance(body, dict):
            continue
        size = (body.get("elementProperties") or {}).get("size")
        if not isinstance(size, dict):
            continue
        oid = body.get("objectId")
        label = f"request #{i} ({kind} {oid})" if oid else f"request #{i} ({kind})"
        for dim in ("width", "height"):
            d = size.get(dim)
            mag = d.get("magnitude") if isinstance(d, dict) else None
            unit = d.get("unit", "EMU") if isinstance(d, dict) else "EMU"
            if not isinstance(mag, (int, float)) or isinstance(mag, bool) or not math.isfinite(mag):
                problems.append(f"{label}: {dim} is {mag!r}, not a number. {UNITS_HINT}")
                continue
            emu = mag * EMU_PER_PT if unit == "PT" else mag
            if emu < EMU_PER_PT:
                problems.append(f"{label}: {dim} {mag:g} {unit} is under 1 pt. "
                                f"Did you pass inches? {UNITS_HINT}")
    return problems


def affected_slide_ids(
    requests: list[dict[str, Any]],
    replies: list[dict[str, Any]],
    prez: dict[str, Any],
) -> list[str]:
    """Slides a batch touches, in id order. Element and notes-shape ids map
    to their slide; `replaceAllText` without a page scope means every slide."""
    slide_ids = {s["objectId"] for s in prez.get("slides", []) or []}
    idx = {k: v for k, v in deck_model.id_index(prez).items() if v}
    created_on: dict[str, str] = {}
    affected: set[str] = set()

    def touch(oid: Any) -> None:
        if not isinstance(oid, str):
            return
        if oid in slide_ids:
            affected.add(oid)
        elif oid in idx:
            affected.add(idx[oid])
        elif oid in created_on:
            affected.add(created_on[oid])

    for req in requests:
        if not req:
            continue
        (kind, body), = req.items()
        if kind == "replaceAllText":
            scope = body.get("pageObjectIds")
            if not scope:
                return sorted(slide_ids)
        if not isinstance(body, dict):
            continue
        for oid in referenced_ids(req):
            touch(oid)
        ep = body.get("elementProperties")
        page = ep.get("pageObjectId") if isinstance(ep, dict) else None
        for cid in created_ids(req):
            if page in slide_ids:
                created_on[cid] = page
            elif kind == "createSlide":
                created_on[cid] = cid

    for reply in replies or []:
        if cs := reply.get("createSlide"):
            if oid := cs.get("objectId"):
                affected.add(oid)
        if dup := reply.get("duplicateObject"):
            touch(dup.get("objectId"))
    return sorted(affected)
