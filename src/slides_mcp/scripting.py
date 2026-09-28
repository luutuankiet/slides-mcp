"""Runs one deck script end to end: snapshot, sandbox, validate, apply.

The sandbox only ever collects requests. Everything that can touch the deck
happens here, in Python, through `writes.apply_batch`.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import queue
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from . import deck_model, normalize, notes_md, slides_api, writes

# (default, ceiling)
LIMITS = {
    "timeout_s": (300.0, 1800.0),
    "cpu_timeout_s": (30.0, 300.0),
    "max_requests": (5000, 20000),
    "max_return_bytes": (8192, 65536),
}
MAX_THUMBNAILS = 6


class ScriptFailure(Exception):
    def __init__(self, kind: str, message: str, **extra: Any):
        super().__init__(message)
        self.kind = kind
        self.extra = extra


def clamp_limits(opts: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    out: dict[str, Any] = {}
    notes: list[str] = []
    for key, (default, ceiling) in LIMITS.items():
        val = opts.get(key)
        if val is None:
            val = default
        if val <= 0:
            raise ValueError(f"{key} must be positive; got {val}")
        if val > ceiling:
            notes.append(f"{key}={val} is above the ceiling; using {ceiling}")
            val = ceiling
        out[key] = type(default)(val)
    return out, notes


def parse_input(value: Any) -> Any:
    """`input` arrives native, or as a JSON string from clients that
    stringify arguments. A string that parses as JSON is parsed once."""
    if isinstance(value, str):
        s = value.strip()
        if s[:1] in ("{", "[", '"') or s in ("true", "false", "null") or _is_number(s):
            try:
                return json.loads(s)
            except ValueError:
                return value
    return value


def _is_number(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


# ---- the worker process -----------------------------------------------------


class Worker:
    """One sandbox process. `next(timeout)` waits for its next message and
    kills the process if the script computes for longer than that."""

    def __init__(self) -> None:
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(p for p in sys.path if p)
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "slides_mcp.sandbox.worker"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", env=env,
        )
        self._q: queue.Queue[str | None] = queue.Queue()
        self._stderr: list[str] = []
        threading.Thread(target=self._pump_out, daemon=True).start()
        threading.Thread(target=self._pump_err, daemon=True).start()

    def _pump_out(self) -> None:
        assert self.proc.stdout
        for line in self.proc.stdout:
            self._q.put(line)
        self._q.put(None)

    def _pump_err(self) -> None:
        assert self.proc.stderr
        for line in self.proc.stderr:
            if sum(map(len, self._stderr)) < 4000:
                self._stderr.append(line)

    def send(self, obj: dict[str, Any]) -> None:
        assert self.proc.stdin
        try:
            self.proc.stdin.write(json.dumps(obj) + "\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            raise ScriptFailure("sandbox", f"sandbox process exited early: {self.stderr_tail()}") from e

    def next(self, timeout: float, why: str) -> dict[str, Any]:
        try:
            line = self._q.get(timeout=max(timeout, 0.01))
        except queue.Empty:
            self.kill()
            raise ScriptFailure("timeout", why) from None
        if line is None:
            self.proc.wait(timeout=5)
            raise ScriptFailure(
                "sandbox",
                f"sandbox process exited (code {self.proc.returncode}): {self.stderr_tail()}")
        return json.loads(line)

    def stderr_tail(self) -> str:
        return "".join(self._stderr)[-800:].strip() or "no output"

    def kill(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass


# ---- checks on a batch ------------------------------------------------------

# Average glyph advance as a fraction of the font size, for overflow guesses.
_GLYPH_EM = {
    "arial": 0.52, "helvetica": 0.52, "calibri": 0.47, "roboto": 0.51, "inter": 0.55,
    "open sans": 0.54, "montserrat": 0.6, "lato": 0.5, "nunito sans": 0.53,
    "rubik": 0.55, "poppins": 0.6, "georgia": 0.53, "times new roman": 0.45,
    "source sans pro": 0.48, "raleway": 0.55,
}
_GLYPH_FALLBACK = 0.53
_INSET_PT = 7.2  # Slides' default text inset on each side


def _glyph(font: str | None, weight: int | None, bold: bool) -> float:
    em = _GLYPH_EM.get((font or "").lower(), _GLYPH_FALLBACK)
    if bold or (weight or 400) >= 600:
        em *= 1.07
    return em


def _est_height_pt(runs: list[dict[str, Any]], w_pt: float) -> float:
    inner = max(w_pt - 2 * _INSET_PT, 1.0)
    total = 0.0
    line_w = 0.0
    line_size = 0.0
    for r in runs:
        size = r.get("sizePt") or 12.0
        em = _glyph(r.get("fontFamily"), r.get("weight"), r.get("bold", False))
        for ch in r.get("text", ""):
            if ch == "\n":
                total += max(1, math.ceil(line_w / inner)) * (line_size or size) * 1.2
                line_w, line_size = 0.0, 0.0
                continue
            line_w += em * size
            line_size = max(line_size, size)
    if line_w:
        total += math.ceil(line_w / inner) * line_size * 1.2
    return total + 2 * _INSET_PT


def _overlaps(r: dict[str, Any], rng: dict[str, Any]) -> bool:
    if rng.get("type", "ALL") == "ALL":
        return True
    a = int(rng.get("startIndex", 0))
    b = int(rng.get("endIndex", 1 << 30))
    return r["start"] < b and r["end"] > a


def batch_warnings(requests: list[dict[str, Any]], snap: dict[str, Any]) -> list[str]:
    """Weight-drop (exact) and likely-overflow (estimated) warnings."""
    by_id: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    for s in snap["slides"]:
        for e in s["elements"]:
            by_id[e["id"]] = (s, e)
    warnings: list[str] = []
    for i, req in enumerate(requests):
        body = req.get("updateTextStyle")
        if not body:
            continue
        hit = by_id.get(body.get("objectId", ""))
        if not hit:
            continue
        slide, el = hit
        fields = {f.strip() for f in (body.get("fields") or "").split(",")}
        style = body.get("style") or {}
        rng = body.get("textRange") or {"type": "ALL"}
        runs = [r for r in el["runs"] if r["text"].strip() and _overlaps(r, rng)]
        where = f"slide {slide['position']} {el['id']}"

        if "fontFamily" in fields and "weightedFontFamily" not in fields:
            lost = sorted({
                r["weight"] for r in runs
                if r.get("weight") and r["weight"] != 400
                and not (r["weight"] == 700 and r.get("bold"))
            })
            if lost:
                sample = next(r["text"].strip() for r in runs if r.get("weight") in lost)[:40]
                warnings.append(
                    f"request #{i}: font change on {where} drops weight {lost} to the family "
                    f"default (e.g. \"{sample}\"); set weightedFontFamily "
                    f"{{fontFamily, weight}} to keep it")

        if ({"fontSize", "fontFamily", "weightedFontFamily"} & fields) and el.get("autofit") in (
                None, "NONE") and el["kind"] == "text":
            new_runs = []
            for r in el["runs"]:
                nr = dict(r)
                if _overlaps(r, rng):
                    if "fontSize" in fields and style.get("fontSize"):
                        nr["sizePt"] = float(style["fontSize"].get("magnitude", nr.get("sizePt") or 12))
                    if "fontFamily" in fields and style.get("fontFamily"):
                        nr["fontFamily"] = style["fontFamily"]
                    if "weightedFontFamily" in fields and style.get("weightedFontFamily"):
                        nr["fontFamily"] = style["weightedFontFamily"].get("fontFamily", nr["fontFamily"])
                        nr["weight"] = style["weightedFontFamily"].get("weight", nr.get("weight"))
                new_runs.append(nr)
            w_pt = el["w"] / 12700
            h_pt = el["h"] / 12700
            before = _est_height_pt(el["runs"], w_pt)
            after = _est_height_pt(new_runs, w_pt)
            if after > h_pt * 1.05 and after > before * 1.02 and before <= h_pt * 1.05:
                warnings.append(
                    f"request #{i}: likely overflow on {where}: text needs ~{after:.0f}pt of "
                    f"height in a {h_pt:.0f}pt box with autofit NONE (\"{el['text'].strip()[:40]}\")")
    return warnings


def _hexes(obj: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(obj, dict):
        if "rgbColor" in obj and isinstance(obj["rgbColor"], dict):
            h = normalize._rgb_to_hex(obj["rgbColor"])
            if h:
                found.add(h)
        for v in obj.values():
            found |= _hexes(v)
    elif isinstance(obj, list):
        for v in obj:
            found |= _hexes(v)
    return found


def _fonts(obj: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(obj, dict):
        if isinstance(obj.get("fontFamily"), str):
            w = obj.get("weight")
            found.add(f"{obj['fontFamily']} {w}" if w else obj["fontFamily"])
        for v in obj.values():
            found |= _fonts(v)
    elif isinstance(obj, list):
        for v in obj:
            found |= _fonts(v)
    return found


def summarise(requests: list[dict[str, Any]], prez: dict[str, Any],
              snap: dict[str, Any]) -> list[dict[str, Any]]:
    """Per slide: request kinds with counts, colours and fonts before/after."""
    pos = {s["id"]: s["position"] for s in snap["slides"]}
    elements = {e["id"]: e for s in snap["slides"] for e in s["elements"]}
    rows: dict[str, dict[str, Any]] = {}
    deck_wide: dict[str, int] = {}
    for req in requests:
        kind = next(iter(req))
        sids = writes.affected_slide_ids([req], [], prez)
        if not sids or len(sids) == len(pos) > 1 and kind == "replaceAllText":
            deck_wide[kind] = deck_wide.get(kind, 0) + 1
            continue
        body = req[kind]
        ids = writes.referenced_ids(req)
        for sid in sids:
            row = rows.setdefault(sid, {"slide_id": sid, "position": pos.get(sid), "kinds": {},
                                        "_cb": set(), "_ca": set(), "_fb": set(), "_fa": set()})
            row["kinds"][kind] = row["kinds"].get(kind, 0) + 1
            row["_ca"] |= _hexes(body)
            row["_fa"] |= _fonts(body)
            for oid in ids:
                el = elements.get(oid)
                if not el:
                    continue
                if (el.get("fill") or {}).get("kind") == "solid" and "Fill" in json.dumps(body):
                    row["_cb"].add(el["fill"]["hex"])
                if kind == "updateTextStyle":
                    rng = body.get("textRange") or {}
                    for r in el["runs"]:
                        if r["text"].strip() and _overlaps(r, rng):
                            if isinstance(r.get("color"), str) and r["color"].startswith("#"):
                                row["_cb"].add(r["color"])
                            if r.get("fontFamily"):
                                row["_fb"].add(f"{r['fontFamily']} {r['weight']}" if r.get(
                                    "weight") else r["fontFamily"])
    out = []
    for sid in sorted(rows, key=lambda s: pos.get(s, 1 << 30)):
        row = rows[sid]
        entry: dict[str, Any] = {"slide_id": sid, "position": row["position"],
                                 "requests": sum(row["kinds"].values()), "kinds": row["kinds"]}
        if row["_cb"] or row["_ca"]:
            entry["colors"] = {"before": sorted(row["_cb"])[:8], "after": sorted(row["_ca"])[:8]}
        if row["_fb"] or row["_fa"]:
            entry["fonts"] = {"before": sorted(row["_fb"])[:8], "after": sorted(row["_fa"])[:8]}
        out.append(entry)
    if deck_wide:
        out.append({"slide_id": None, "scope": "deck", "kinds": deck_wide,
                    "requests": sum(deck_wide.values())})
    return out


# ---- notes expansion ---------------------------------------------------------


def expand_notes(requests: list[dict[str, Any]], prez: dict[str, Any],
                 notes_state: dict[str, dict[str, Any] | None],
                 exempt: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Replace `{__setNotes: ...}` markers with real requests, tracking each
    notes shape so a second setNotes on the same slide sees the first."""
    slides = {s["objectId"]: s for s in prez.get("slides") or []}
    out: list[dict[str, Any]] = []
    for req in requests:
        marker = req.get("__setNotes") if isinstance(req, dict) else None
        if marker is None:
            out.append(req)
            continue
        sid = marker.get("slideId")
        slide = slides.get(sid)
        if slide is None:
            raise ScriptFailure("validation", f"setNotes: unknown slide id {sid!r}")
        oid, shape = normalize.notes_shape(slide)
        if not oid:
            raise ScriptFailure("validation", f"setNotes: slide {sid} has no speaker notes shape")
        current = notes_state.get(oid, shape)
        reqs = notes_md.build_requests(oid, marker.get("markdown", ""),
                                       mode=marker.get("mode", "replace"), existing=current)
        notes_state[oid] = notes_md.apply_to_shape(current, reqs)
        if exempt is not None and marker.get("mode") == "append" and notes_md.append_is_safe(reqs):
            exempt.extend(r for r in reqs if "deleteParagraphBullets" in r)
        out.extend(reqs)
    return out


# ---- orchestration ------------------------------------------------------------


@dataclass
class Receipt:
    applied_request_count: int = 0
    kinds: dict[str, int] = field(default_factory=dict)
    affected_slide_ids: list[str] = field(default_factory=list)
    destructive_kinds: list[str] = field(default_factory=list)
    phases: int = 0

    def add(self, requests: list[dict[str, Any]], affected: list[str]) -> None:
        self.applied_request_count += len(requests)
        for k in writes.request_kinds(requests):
            self.kinds[k] = self.kinds.get(k, 0) + 1
        self.affected_slide_ids = sorted(set(self.affected_slide_ids) | set(affected))
        self.destructive_kinds = sorted(set(self.destructive_kinds)
                                        | set(writes.destructive_kinds(requests)))
        self.phases += 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "applied_request_count": self.applied_request_count,
            "request_kinds": self.kinds,
            "affected_slide_ids": self.affected_slide_ids,
            "destructive_kinds": self.destructive_kinds,
            "phases": self.phases,
        }


def run(
    deck_id: str,
    script: str,
    input_value: Any,
    *,
    dry_run: bool,
    confirm_destructive: bool,
    include_requests: bool,
    limits: dict[str, Any],
) -> dict[str, Any]:
    t0 = time.monotonic()
    deadline = t0 + limits["timeout_s"]
    cpu = limits["cpu_timeout_s"]
    script_hash = hashlib.sha256(script.encode("utf-8")).hexdigest()[:16]
    receipt = Receipt()
    warnings: list[str] = []
    logs: list[str] = []
    all_requests: list[dict[str, Any]] = []
    result_json = "null"
    notes_state: dict[str, dict[str, Any] | None] = {}

    def remaining() -> float:
        return deadline - time.monotonic()

    def wait(worker: Worker) -> dict[str, Any]:
        budget = min(cpu, remaining())
        why = (f"script ran for more than cpu_timeout_s={cpu:g}s without yielding "
               "(an infinite loop?)" if budget == cpu else
               f"overall timeout_s={limits['timeout_s']:g}s reached")
        msg = worker.next(budget, why)
        logs.extend(msg.get("logs") or [])
        return msg

    base: dict[str, Any] = {"deck_id": deck_id, "dry_run": dry_run, "script_hash": script_hash}

    prez = slides_api.get_presentation(deck_id)
    snap = deck_model.build(prez)
    worker = Worker()
    try:
        worker.send({"op": "start", "script": script, "input": input_value, "deck": snap,
                     "max_requests": limits["max_requests"]})
        stopped_at_commit = False
        while True:
            msg = wait(worker)
            op = msg["op"]
            if op == "error":
                err = msg["error"]
                kind = err.pop("kind", "script")
                raise ScriptFailure(kind, f"{err.get('name', 'Error')}: {err.get('message', '')}",
                                    **{k: v for k, v in err.items() if k in ("line", "column", "stack")})
            exempt: list[dict[str, Any]] = []
            phase = expand_notes(msg.get("requests") or [], prez, notes_state, exempt)
            if phase:
                problems = writes.unknown_ids(phase, prez)
                if problems:
                    raise ScriptFailure("validation", "; ".join(problems[:5]) + (
                        f" (+{len(problems) - 5} more)" if len(problems) > 5 else ""))
                warnings.extend(batch_warnings(phase, snap))
                if len(all_requests) + len(phase) > limits["max_requests"]:
                    raise ScriptFailure("limit", f"more than max_requests={limits['max_requests']} requests")
            if dry_run:
                all_requests.extend(phase)
                if op == "commit":
                    stopped_at_commit = True
                    break
                result_json = msg.get("result", "null")
                break
            # real apply of this phase
            if phase:
                exempt_ids = {id(r) for r in exempt}
                destructive = writes.destructive_kinds([r for r in phase if id(r) not in exempt_ids])
                if destructive and not confirm_destructive:
                    raise ScriptFailure(
                        "destructive",
                        f"refused: destructive kinds {destructive} in phase {receipt.phases + 1}; "
                        "re-run with confirm_destructive=true")
                if remaining() <= 0:
                    raise ScriptFailure("timeout", f"overall timeout_s={limits['timeout_s']:g}s reached")
                try:
                    resp = writes.apply_batch(deck_id, phase, tool="run_deck_script", extra_audit={
                        "script_hash": script_hash, "phase": receipt.phases + 1})
                except slides_api.SlidesApiError as e:
                    idx = writes.failing_request_index(str(e))
                    extra: dict[str, Any] = {"status": e.status}
                    if idx is not None:
                        extra["request_index"] = idx
                        extra["request_index_overall"] = receipt.applied_request_count + idx
                        if idx < len(phase):
                            extra["request"] = phase[idx]
                    raise ScriptFailure("api", str(e), **extra) from e
                receipt.add(phase, writes.affected_slide_ids(phase, resp.get("replies") or [], prez))
                all_requests.extend(phase)
            if op == "commit":
                prez = slides_api.get_presentation(deck_id)
                snap = deck_model.build(prez)
                notes_state.clear()
                worker.send({"op": "resume", "deck": snap})
                continue
            result_json = msg.get("result", "null")
            break
    except ScriptFailure as f:
        worker.kill()
        out = {**base, "isError": True,
               "error": {"kind": f.kind, "message": str(f), **f.extra},
               "warnings": warnings, "logs": logs[-50:]}
        if receipt.phases:
            out["receipt"] = receipt.as_dict()
            out["error"]["note"] = (f"{receipt.phases} earlier phase(s) were applied before "
                                    "this failure; the failing phase was not.")
        out["elapsed_s"] = round(time.monotonic() - t0, 2)
        return out
    finally:
        worker.kill()

    out = {**base, "isError": False}
    if dry_run:
        result_value: Any = None
        if not stopped_at_commit:
            result_value, trunc = _bounded(result_json, limits["max_return_bytes"])
            if trunc:
                warnings.append(trunc)
        out["result"] = result_value
        out["preview"] = {
            "request_count": len(all_requests),
            "request_kinds": _counts(all_requests),
            "destructive_kinds": writes.destructive_kinds(all_requests),
            "slides": summarise(all_requests, prez, snap),
        }
        if include_requests:
            out["preview"]["requests"] = all_requests
        if stopped_at_commit:
            out["preview"]["stopped_at_commit"] = True
            out["preview"]["note"] = (
                "Dry run stopped at the first commit(): later phases depend on writes that "
                "did not happen. The preview covers phase 1 only; run with dry_run=false "
                "to execute every phase.")
        if writes.destructive_kinds(all_requests) and not confirm_destructive:
            out["preview"]["note_destructive"] = (
                "Applying this needs confirm_destructive=true.")
    else:
        result_value, trunc = _bounded(result_json, limits["max_return_bytes"])
        if trunc:
            warnings.append(trunc)
        out["result"] = result_value
        out["receipt"] = receipt.as_dict()
        if include_requests:
            out["requests"] = all_requests
    out["warnings"] = warnings
    if logs:
        out["logs"] = logs[-50:]
    out["elapsed_s"] = round(time.monotonic() - t0, 2)
    out["_deck_after"] = prez if not dry_run else None
    return out


def _counts(requests: list[dict[str, Any]]) -> dict[str, int]:
    c: dict[str, int] = {}
    for k in writes.request_kinds(requests):
        c[k] = c.get(k, 0) + 1
    return c


def _bounded(result_json: str, limit: int) -> tuple[Any, str | None]:
    raw = result_json.encode("utf-8")
    if len(raw) <= limit:
        return json.loads(result_json), None
    preview = raw[: max(limit - 256, 64)].decode("utf-8", errors="ignore")
    return ({"truncated": True, "bytes": len(raw), "preview": preview},
            f"return value was {len(raw)} bytes, over max_return_bytes={limit}; "
            "truncated to a preview. Return a smaller summary.")
