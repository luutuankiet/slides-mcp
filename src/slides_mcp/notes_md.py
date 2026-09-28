"""Speaker notes <-> Markdown, for exactly this subset:

  paragraphs; **bold**; *italic*; # / ## / ### headers; `-` bullets nested
  by two spaces.

The Slides API has no rich-text input, so Markdown is only the agent-facing
format. Writing produces plain `insertText` plus `updateTextStyle` ranges plus
`createParagraphBullets`; what lands in the notes is real formatting, never
literal asterisks. Notes have no heading styles, so a header is written as a
bold paragraph at 18 / 16 / 14 pt and read back from exactly that.

Everything outside the subset is written as literal text, and styling the
subset cannot express (underline, colour, links) reads back as plain text.

All indices are UTF-16 code units, the unit Slides uses.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from .normalize import utf16_len

HEADER_PT = {1: 18.0, 2: 16.0, 3: 14.0}
_PT_HEADER = {v: k for k, v in HEADER_PT.items()}
BULLET_PRESET = "BULLET_DISC_CIRCLE_SQUARE"


# ---- read: notes shape JSON -> Markdown --------------------------------------


@dataclass
class _Para:
    bullet_level: int | None = None
    runs: list[tuple[str, dict[str, Any]]] = field(default_factory=list)


def _paragraphs(shape: dict[str, Any] | None) -> list[_Para]:
    elements = ((shape or {}).get("text") or {}).get("textElements") or []
    paras: list[_Para] = []
    cur: _Para | None = None
    for el in elements:
        if "paragraphMarker" in el:
            bullet = (el.get("paragraphMarker") or {}).get("bullet")
            level = None if bullet is None else int(bullet.get("nestingLevel", 0) or 0)
            cur = _Para(bullet_level=level)
            paras.append(cur)
        elif run := el.get("textRun"):
            if cur is None:
                cur = _Para()
                paras.append(cur)
            cur.runs.append((run.get("content", ""), run.get("style") or {}))
    return paras


def _size(style: dict[str, Any]) -> float | None:
    fs = style.get("fontSize") or {}
    return float(fs["magnitude"]) if fs.get("unit") == "PT" and "magnitude" in fs else None


def _wrap(text: str, bold: bool, italic: bool) -> str:
    if not text.strip() or not (bold or italic):
        return text
    lead = text[: len(text) - len(text.lstrip())]
    trail = text[len(text.rstrip()):]
    core = text.strip()
    mark = "***" if bold and italic else "**" if bold else "*"
    return f"{lead}{mark}{core}{mark}{trail}"


def to_markdown(shape: dict[str, Any] | None) -> str:
    """Render a notes shape as Markdown in the supported subset."""
    lines: list[str] = []
    for p in _paragraphs(shape):
        runs = [(t.replace("\n", ""), s) for t, s in p.runs]
        runs = [(t, s) for t, s in runs if t]
        plain = "".join(t for t, _ in runs)
        visible = [(t, s) for t, s in runs if t.strip()]
        if p.bullet_level is None and visible:
            sizes = {_size(s) for _, s in visible}
            if all(s.get("bold") for _, s in visible) and len(sizes) == 1:
                level = _PT_HEADER.get(next(iter(sizes)) or 0.0)
                if level:
                    lines.append(f"{'#' * level} {plain.strip()}")
                    continue
        # merge adjacent runs sharing bold/italic, then wrap
        merged: list[list[Any]] = []
        for t, s in runs:
            key = (bool(s.get("bold")), bool(s.get("italic")))
            if merged and merged[-1][1] == key:
                merged[-1][0] += t
            else:
                merged.append([t, key])
        body = "".join(_wrap(t, b, i) for t, (b, i) in merged)
        if p.bullet_level is not None and body.strip():
            lines.append(f"{'  ' * p.bullet_level}- {body.strip()}")
        elif p.bullet_level is not None:
            lines.append("")  # an empty bulleted paragraph reads as a blank line
        else:
            lines.append(body)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def to_text(shape: dict[str, Any] | None) -> str:
    return "".join(t for p in _paragraphs(shape) for t, _ in p.runs).strip()


def base_size(shape: dict[str, Any] | None) -> float | None:
    """Most common font size among non-header runs; the notes' body size."""
    c: Counter[float] = Counter()
    for p in _paragraphs(shape):
        for t, s in p.runs:
            sz = _size(s)
            if sz and t.strip() and not (s.get("bold") and sz in _PT_HEADER):
                c[sz] += len(t)
    return c.most_common(1)[0][0] if c else None


def utf16_text_len(shape: dict[str, Any] | None) -> int:
    elements = ((shape or {}).get("text") or {}).get("textElements") or []
    return max((int(e.get("endIndex", 0) or 0) for e in elements), default=0)


def has_bullets(shape: dict[str, Any] | None) -> bool:
    return any(p.bullet_level is not None for p in _paragraphs(shape))


# ---- write: Markdown -> requests ---------------------------------------------


_HEADER_RE = re.compile(r"^(#{1,3})\s+(.*)$")
_BULLET_RE = re.compile(r"^( *)- (.*)$")
_MARK_RE = re.compile(r"\*\*\*|\*\*|\*")


@dataclass
class _Line:
    text: str                        # final text, bullets carry leading tabs
    spans: list[tuple[int, int, bool, bool]]  # (start, end, bold, italic), UTF-16, in-line
    header: int | None = None
    bullet: int | None = None


def _inline(src: str) -> tuple[str, list[tuple[int, int, bool, bool]]]:
    """Strip **/* markers that close on the same line; record styled spans."""
    out: list[str] = []
    spans: list[tuple[int, int, bool, bool]] = []
    bold = italic = False
    pos = 0            # UTF-16 position in output
    seg_start = 0
    i = 0
    markers = list(_MARK_RE.finditer(src))

    def closes(idx: int, mark: str) -> bool:
        return any(m.group() == mark or (mark != "***" and m.group() == "***")
                   for m in markers[idx + 1:])

    def flush() -> None:
        nonlocal seg_start
        if (bold or italic) and pos > seg_start:
            spans.append((seg_start, pos, bold, italic))
        seg_start = pos

    for idx, m in enumerate(markers):
        chunk = src[i:m.start()]
        out.append(chunk)
        pos += utf16_len(chunk)
        mark = m.group()
        want_bold = mark in ("**", "***")
        want_italic = mark in ("*", "***")
        opening = (want_bold and not bold) or (want_italic and not italic)
        if opening and not closes(idx, mark):
            out.append(mark)          # unmatched: literal
            pos += len(mark)
        else:
            flush()
            if want_bold:
                bold = not bold
            if want_italic:
                italic = not italic
        i = m.end()
    tail = src[i:]
    out.append(tail)
    pos += utf16_len(tail)
    flush()
    return "".join(out), spans


def parse(markdown: str) -> list[_Line]:
    lines: list[_Line] = []
    for raw in markdown.replace("\r\n", "\n").split("\n"):
        if m := _HEADER_RE.match(raw):
            text, _ = _inline(m.group(2).strip())
            lines.append(_Line(text=text, spans=[], header=len(m.group(1))))
        elif m := _BULLET_RE.match(raw):
            level = min(len(m.group(1)) // 2, 8)
            text, spans = _inline(m.group(2))
            shift = level  # leading tabs, one UTF-16 unit each
            lines.append(_Line(text="\t" * level + text,
                               spans=[(a + shift, b + shift, x, y) for a, b, x, y in spans],
                               bullet=level))
        else:
            text, spans = _inline(raw)
            lines.append(_Line(text=text, spans=spans))
    while lines and not lines[-1].text.strip() and lines[-1].header is None:
        lines.pop()
    return lines


def _style_req(object_id: str, start: int, end: int, style: dict[str, Any],
               fields: str) -> dict[str, Any]:
    return {"updateTextStyle": {
        "objectId": object_id,
        "textRange": {"type": "FIXED_RANGE", "startIndex": start, "endIndex": end},
        "style": style,
        "fields": fields,
    }}


def build_requests(
    object_id: str,
    markdown: str,
    *,
    mode: str = "replace",
    existing: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Requests that write `markdown` into the notes shape `object_id`.

    `existing` is the current notes shape JSON (for its length, body font size
    and bullets). `mode="replace"` deletes current text first when there is
    any; that `deleteText` is what makes a non-empty replace destructive.

    `mode="append"` never removes existing text. When the notes end in a
    bullet, it emits `deleteParagraphBullets` scoped to the appended plain
    lines only; see `append_is_safe`.
    """
    if mode not in ("replace", "append"):
        raise ValueError(f"mode must be replace|append; got {mode!r}")
    lines = parse(markdown)
    if not lines:
        return []
    has_text = bool(to_text(existing))
    visible = [p for p in _paragraphs(existing) if "".join(t for t, _ in p.runs).strip()]
    # New text inherits the paragraph it is inserted into, bullet included.
    inherits_bullet = mode == "append" and bool(visible) and visible[-1].bullet_level is not None
    reqs: list[dict[str, Any]] = []

    if mode == "replace":
        if has_text:
            if has_bullets(existing):
                reqs.append({"deleteParagraphBullets": {
                    "objectId": object_id, "textRange": {"type": "ALL"}}})
            reqs.append({"deleteText": {"objectId": object_id, "textRange": {"type": "ALL"}}})
        base = 0
        prefix = ""
    else:
        # Insert right after the last visible character, as a new paragraph.
        raw = "".join(t for p in _paragraphs(existing) for t, _ in p.runs)
        kept = raw.rstrip("\n")
        base = utf16_len(kept) if kept else 0
        prefix = "\n" if kept else ""

    body = "\n".join(ln.text for ln in lines)
    text = prefix + body
    reqs.append({"insertText": {"objectId": object_id, "insertionIndex": base, "text": text}})

    start0 = base + utf16_len(prefix)
    end_all = base + utf16_len(text)
    plain_style: dict[str, Any] = {"bold": False, "italic": False}
    plain_fields = "bold,italic"
    if (sz := base_size(existing)) is not None:
        plain_style["fontSize"] = {"magnitude": sz, "unit": "PT"}
        plain_fields += ",fontSize"
    if end_all > start0:
        reqs.append(_style_req(object_id, start0, end_all, plain_style, plain_fields))

    # Offsets per line, then headers and inline spans.
    offsets: list[int] = []
    cursor = start0
    for ln in lines:
        offsets.append(cursor)
        cursor += utf16_len(ln.text) + 1
    for ln, off in zip(lines, offsets, strict=True):
        n = utf16_len(ln.text)
        if ln.header and n:
            reqs.append(_style_req(object_id, off, off + n, {
                "bold": True, "fontSize": {"magnitude": HEADER_PT[ln.header], "unit": "PT"},
            }, "bold,fontSize"))
        for a, b, bold, italic in ln.spans:
            style: dict[str, Any] = {}
            fields = []
            if bold:
                style["bold"] = True
                fields.append("bold")
            if italic:
                style["italic"] = True
                fields.append("italic")
            reqs.append(_style_req(object_id, off + a, off + b, style, ",".join(fields)))

    if inherits_bullet:
        for ln, off in zip(lines, offsets, strict=True):
            if ln.bullet is None:
                reqs.append({"deleteParagraphBullets": {"objectId": object_id, "textRange": {
                    "type": "FIXED_RANGE", "startIndex": off,
                    "endIndex": off + max(utf16_len(ln.text), 1)}}})

    # Bullet blocks last, and last-to-first: each call strips its leading
    # tabs, which would shift every range after it.
    blocks: list[tuple[int, int]] = []
    run_start: int | None = None
    run_end = 0
    for ln, off in zip(lines, offsets, strict=True):
        if ln.bullet is not None:
            if run_start is None:
                run_start = off
            run_end = off + utf16_len(ln.text)
        elif run_start is not None:
            blocks.append((run_start, run_end))
            run_start = None
    if run_start is not None:
        blocks.append((run_start, run_end))
    for a, b in reversed(blocks):
        reqs.append({"createParagraphBullets": {
            "objectId": object_id,
            "textRange": {"type": "FIXED_RANGE", "startIndex": a, "endIndex": max(b, a + 1)},
            "bulletPreset": BULLET_PRESET,
        }})
    return reqs


def apply_to_shape(shape: dict[str, Any] | None, requests: list[dict[str, Any]]) -> dict[str, Any]:
    """Simulate `requests` on a notes shape JSON. Used by tests' fake API and
    by the script runtime to keep its snapshot current after a dry-run
    `setNotes`. Handles only the request kinds `build_requests` emits."""
    # Represent text as a list of (char, style, bullet_level_of_paragraph).
    chars: list[list[Any]] = []
    for p in _paragraphs(shape):
        for t, s in p.runs:
            for ch in t:
                chars.append([ch, dict(s), p.bullet_level])
    # UTF-16 aware: expand astral chars into two slots.
    def expand(cs: list[list[Any]]) -> list[list[Any]]:
        out: list[list[Any]] = []
        for c in cs:
            out.append(c)
            if len(c[0]) == 1 and ord(c[0]) > 0xFFFF:
                out.append(["", c[1], c[2]])
        return out
    chars = expand(chars)

    def rng(tr: dict[str, Any]) -> tuple[int, int]:
        if tr.get("type") == "ALL":
            return 0, len(chars)
        return int(tr.get("startIndex", 0)), int(tr.get("endIndex", len(chars)))

    for req in requests:
        (kind, body), = req.items()
        if kind == "deleteText":
            a, b = rng(body["textRange"])
            del chars[a:b]
        elif kind == "deleteParagraphBullets":
            a, b = rng(body["textRange"])
            for c in chars[a:b]:
                c[2] = None
        elif kind == "insertText":
            idx = int(body.get("insertionIndex", 0))
            neighbour = chars[idx - 1][1] if 0 < idx <= len(chars) else (
                chars[0][1] if chars else {})
            new = expand([[ch, dict(neighbour), None] for ch in body["text"]])
            chars[idx:idx] = new
        elif kind == "updateTextStyle":
            a, b = rng(body["textRange"])
            for f in body["fields"].split(","):
                for c in chars[a:b]:
                    if f in body["style"]:
                        c[1][f] = body["style"][f]
                    else:
                        c[1].pop(f, None)
        elif kind == "createParagraphBullets":
            a, b = rng(body["textRange"])
            # extend to whole paragraphs
            while a > 0 and chars[a - 1][0] != "\n":
                a -= 1
            while 0 < b < len(chars) and chars[b - 1][0] != "\n":
                b += 1
            i = a
            out: list[list[Any]] = chars[:a]
            while i < b:
                j = i
                while j < b and chars[j][0] != "\n":
                    j += 1
                para = chars[i:j + 1] if j < b else chars[i:j]
                level = 0
                while para and para[0][0] == "\t":
                    para = para[1:]
                    level += 1
                for c in para:
                    c[2] = level
                out.extend(para)
                i = j + 1 if j < b else j
            chars = out + chars[b:]
    # rebuild textElements
    elements: list[dict[str, Any]] = []
    pos = 0
    text = "".join(c[0] for c in chars)
    if not text.endswith("\n"):
        chars.append(["\n", chars[-1][1] if chars else {}, chars[-1][2] if chars else None])
    i = 0
    while i < len(chars):
        j = i
        while j < len(chars) and chars[j][0] != "\n":
            j += 1
        para = chars[i:j + 1]
        pstart = pos
        plen = len(para)
        marker: dict[str, Any] = {}
        if para[0][2] is not None:
            marker["bullet"] = {"listId": "list", "nestingLevel": para[0][2]}
        elements.append({"startIndex": pstart, "endIndex": pstart + plen, "paragraphMarker": marker})
        k = 0
        while k < plen:
            m = k
            while m < plen and para[m][1] == para[k][1]:
                m += 1
            content = "".join(c[0] for c in para[k:m])
            elements.append({"startIndex": pstart + k, "endIndex": pstart + m,
                             "textRun": {"content": content, "style": para[k][1]}})
            k = m
        pos += plen
        i = j + 1
    new_shape = dict(shape or {})
    new_shape["text"] = {"textElements": elements}
    return new_shape


def append_is_safe(requests: list[dict[str, Any]]) -> bool:
    """True for an append batch from `build_requests`: its only destructive
    kind is `deleteParagraphBullets` on the text it just inserted, so the
    destructive guard does not apply."""
    return not any(k in req for req in requests for k in ("deleteText",))
