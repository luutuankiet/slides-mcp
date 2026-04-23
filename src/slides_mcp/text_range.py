"""Text range resolution + style normalization.

Shared helpers for `update_text_style`, `update_paragraph_style`, and future
character-scoped tools. All pure — no network, no Slides API client, no side
effects — so unit tests cover the whole surface.

## Range language

All four tools (and `get_slide(include_styles=True)`) use the same range spec:

  - None or "all"            → entire text of the shape
  - {"paragraph": N}          → 0-indexed paragraph (split on "\n")
  - {"chars": [start, end]}   → raw UTF-16 code-unit indices, end exclusive
  - {"match": "substring"}    → unique substring; ValueError on 0 or >1 hits

Server resolves the spec against the shape's real text and emits a Slides API
`textRange` dict ({"type": "ALL"} or {"type": "FIXED_RANGE", startIndex, endIndex}).
Agent never computes UTF-16 indices.

## Style normalization

User passes a friendly dict (hex strings, pt numbers, bools). We return
(api_style, fields) where api_style is Slides-API-shaped and fields is the
fields-mask list.

Unknown keys raise ValueError loudly — typos surface at server side, not in
Google's opaque error messages.
"""
from __future__ import annotations

import re
from typing import Any

# Subset the bespoke tools expose. Slides API supports more; we reject unknowns
# to keep the tool surface predictable (`exec_batch_update` remains the escape hatch).
CHARACTER_STYLE_KEYS = frozenset({
    "bold", "italic", "underline", "strikethrough", "smallCaps",
    "fontFamily", "fontSize",
    "foregroundColor", "backgroundColor",
    "baselineOffset", "weightedFontFamily",
})

PARAGRAPH_STYLE_KEYS = frozenset({
    "alignment", "direction", "spacingMode",
    "indentStart", "indentEnd", "indentFirstLine",
    "lineSpacing", "spaceAbove", "spaceBelow",
})


def _hex_to_rgb_fracs(hex_value: str) -> dict[str, float]:
    """Convert '#RRGGBB' or 'RRGGBB' to Slides API rgbColor fracs."""
    s = hex_value.lstrip("#").strip()
    if len(s) != 6:
        raise ValueError(f"expected 6-digit hex; got {hex_value!r}")
    try:
        r = int(s[0:2], 16) / 255.0
        g = int(s[2:4], 16) / 255.0
        b = int(s[4:6], 16) / 255.0
    except ValueError as e:
        raise ValueError(f"invalid hex digits in {hex_value!r}") from e
    return {"red": round(r, 6), "green": round(g, 6), "blue": round(b, 6)}


def resolve_range(text: str, range_spec: Any) -> dict[str, Any]:
    """Resolve a user range spec against shape text to a Slides API textRange dict.

    text: full text content of the target shape (Python str; Slides indexes in UTF-16
          code units — BMP chars are 1 unit, emoji/surrogates are 2. Python str
          counts characters, NOT code units. For ASCII/BMP the two agree.)

    Returns {"type": "ALL"} or {"type": "FIXED_RANGE", "startIndex": s, "endIndex": e}.
    """
    if range_spec is None or range_spec == "all":
        return {"type": "ALL"}

    if not isinstance(range_spec, dict):
        raise ValueError(
            f"range must be 'all' or a dict; got {type(range_spec).__name__}: {range_spec!r}"
        )

    keys = set(range_spec.keys())
    if len(keys) != 1:
        raise ValueError(
            f"range dict must have exactly one key ('paragraph' | 'chars' | 'match'); got {sorted(keys)}"
        )

    if "paragraph" in range_spec:
        n = int(range_spec["paragraph"])
        if n < 0:
            raise ValueError(f"paragraph index must be >= 0; got {n}")
        # Index VISIBLE paragraphs only — blank separators ("\n\n") don't count.
        # Rationale: intern says "paragraph 1" meaning the 2nd visible block, not
        # the empty string between two blocks. Slides API also rejects zero-length
        # textRanges ("startIndex must be less than endIndex"), so skipping empties
        # is load-bearing, not cosmetic.
        visible = [(m.start(), m.end()) for m in re.finditer(r"[^\n]+", text)]
        if n >= len(visible):
            raise ValueError(
                f"paragraph index {n} out of range (text has {len(visible)} visible paragraph(s))"
            )
        start, end = visible[n]
        return {"type": "FIXED_RANGE", "startIndex": start, "endIndex": end}

    if "chars" in range_spec:
        span = range_spec["chars"]
        if not isinstance(span, (list, tuple)) or len(span) != 2:
            raise ValueError(f"chars must be [start, end]; got {span!r}")
        s, e = int(span[0]), int(span[1])
        if s < 0 or e < 0:
            raise ValueError(f"chars indices must be >= 0; got [{s}, {e}]")
        if s >= e:
            raise ValueError(f"chars start must be < end; got [{s}, {e}]")
        # If text is provided (non-empty), validate bounds.
        if text and e > len(text):
            raise ValueError(f"chars end {e} exceeds text length {len(text)}")
        return {"type": "FIXED_RANGE", "startIndex": s, "endIndex": e}

    if "match" in range_spec:
        needle = range_spec["match"]
        if not isinstance(needle, str) or not needle:
            raise ValueError(f"match must be a non-empty string; got {needle!r}")
        count = text.count(needle)
        if count == 0:
            snippet = text[:80] + ("…" if len(text) > 80 else "")
            raise ValueError(
                f"match {needle!r} not found in shape text. Text starts with: {snippet!r}"
            )
        if count > 1:
            raise ValueError(
                f"match {needle!r} is ambiguous: found {count} occurrences. "
                "Use {'chars': [start, end]} to target a specific occurrence, "
                "or make the substring unique."
            )
        start = text.index(needle)
        end = start + len(needle)
        return {"type": "FIXED_RANGE", "startIndex": start, "endIndex": end}

    raise ValueError(
        f"unknown range key(s) {sorted(keys)}; expected 'paragraph', 'chars', or 'match'"
    )


def normalize_text_style(style: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Normalize a friendly character-style dict into Slides API TextStyle + fields list.

    Returns (api_style, fields). Unknown keys raise ValueError.
    """
    if not isinstance(style, dict) or not style:
        raise ValueError("style must be a non-empty dict")

    api: dict[str, Any] = {}
    fields: list[str] = []

    for key, val in style.items():
        if key not in CHARACTER_STYLE_KEYS:
            raise ValueError(
                f"unknown text style key {key!r}; allowed: {sorted(CHARACTER_STYLE_KEYS)}"
            )
        fields.append(key)
        if key in {"bold", "italic", "underline", "strikethrough", "smallCaps"}:
            api[key] = bool(val)
        elif key == "fontFamily":
            api[key] = str(val)
        elif key == "fontSize":
            api[key] = {"magnitude": float(val), "unit": "PT"}
        elif key == "baselineOffset":
            allowed = {"NONE", "SUPERSCRIPT", "SUBSCRIPT"}
            sval = str(val).upper()
            if sval not in allowed:
                raise ValueError(
                    f"baselineOffset must be one of {sorted(allowed)}; got {val!r}"
                )
            api[key] = sval
        elif key in {"foregroundColor", "backgroundColor"}:
            if isinstance(val, str):
                api[key] = {"opaqueColor": {"rgbColor": _hex_to_rgb_fracs(val)}}
            elif isinstance(val, dict):
                # Trust caller for advanced patterns (themeColor, pre-formed OptionalColor).
                api[key] = val
            else:
                raise ValueError(
                    f"{key} must be a hex string or a Color dict; got {type(val).__name__}"
                )
        elif key == "weightedFontFamily":
            if not isinstance(val, dict) or "fontFamily" not in val:
                raise ValueError(
                    "weightedFontFamily must be {'fontFamily': str, 'weight': int}"
                )
            api[key] = {
                "fontFamily": str(val["fontFamily"]),
                "weight": int(val.get("weight", 400)),
            }

    return api, fields


def normalize_paragraph_style(style: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Normalize a friendly paragraph-style dict into Slides API ParagraphStyle + fields list.

    Accepted keys:
      - str enum: alignment (START|CENTER|END|JUSTIFIED),
                  direction (LEFT_TO_RIGHT|RIGHT_TO_LEFT),
                  spacingMode (NEVER_COLLAPSE|COLLAPSE_LISTS)
      - pt number: indentStart, indentEnd, indentFirstLine, spaceAbove, spaceBelow
      - % number:  lineSpacing (100 = single spacing, 150 = 1.5×)

    Returns (api_style, fields).
    """
    if not isinstance(style, dict) or not style:
        raise ValueError("style must be a non-empty dict")

    api: dict[str, Any] = {}
    fields: list[str] = []

    for key, val in style.items():
        if key not in PARAGRAPH_STYLE_KEYS:
            raise ValueError(
                f"unknown paragraph style key {key!r}; allowed: {sorted(PARAGRAPH_STYLE_KEYS)}"
            )
        fields.append(key)
        if key == "alignment":
            allowed = {"START", "CENTER", "END", "JUSTIFIED"}
            sval = str(val).upper()
            if sval not in allowed:
                raise ValueError(
                    f"alignment must be one of {sorted(allowed)}; got {val!r}"
                )
            api[key] = sval
        elif key == "direction":
            allowed = {"LEFT_TO_RIGHT", "RIGHT_TO_LEFT"}
            sval = str(val).upper()
            if sval not in allowed:
                raise ValueError(
                    f"direction must be one of {sorted(allowed)}; got {val!r}"
                )
            api[key] = sval
        elif key == "spacingMode":
            allowed = {"NEVER_COLLAPSE", "COLLAPSE_LISTS"}
            sval = str(val).upper()
            if sval not in allowed:
                raise ValueError(
                    f"spacingMode must be one of {sorted(allowed)}; got {val!r}"
                )
            api[key] = sval
        elif key in {"indentStart", "indentEnd", "indentFirstLine", "spaceAbove", "spaceBelow"}:
            api[key] = {"magnitude": float(val), "unit": "PT"}
        elif key == "lineSpacing":
            api[key] = float(val)

    return api, fields


def extract_shape_text(page: dict[str, Any], object_id: str) -> str:
    """Extract the full text of the target shape on a Slides page.

    Walks `page.pageElements`, finds the shape by `objectId`, joins every
    `textElement.textRun.content` in order. Returns empty string if shape has
    no text. Raises KeyError if object_id isn't on the page.
    """
    for elem in page.get("pageElements") or []:
        if elem.get("objectId") != object_id:
            continue
        shape = elem.get("shape") or {}
        text_obj = shape.get("text") or {}
        elements = text_obj.get("textElements") or []
        parts: list[str] = []
        for el in elements:
            run = el.get("textRun")
            if run:
                parts.append(run.get("content", ""))
        return "".join(parts)
    raise KeyError(f"object_id {object_id!r} not found on slide")
