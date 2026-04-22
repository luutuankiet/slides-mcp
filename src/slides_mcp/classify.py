"""Classify a slide's flat shape list into one of the bundled archetype names.

Element topology > layout name (which is unreliable in Google Slides).
Order rules from most specific to most generic; first match wins.
"""
from __future__ import annotations

from collections import Counter

from .normalize import FlatShape, flatten


def _big_texts(flat: list[FlatShape], min_chars: int = 30) -> list[FlatShape]:
    return [s for s in flat if s.kind == "text" and (s.text and len(s.text) > min_chars)]


def _small_texts(flat: list[FlatShape], max_chars: int = 30) -> list[FlatShape]:
    return [s for s in flat if s.kind == "text" and s.text and 0 < len(s.text) <= max_chars]


def _group_by_top(items: list[FlatShape], tol: float = 0.5) -> list[list[FlatShape]]:
    """Cluster shapes by their top_in within tol. Used to find rows."""
    rows: list[list[FlatShape]] = []
    for it in sorted(items, key=lambda s: s.top_in):
        for r in rows:
            if abs(r[0].top_in - it.top_in) < tol:
                r.append(it)
                break
        else:
            rows.append([it])
    return rows


def _dominant_col_count(texts: list[FlatShape]) -> int:
    """Max number of side-by-side big texts in any row."""
    if not texts:
        return 0
    rows = _group_by_top(texts)
    return max(len(r) for r in rows)


def classify(shapes: list[FlatShape]) -> str:
    """Return an archetype name from the bundled registry."""
    flat = flatten(shapes)
    texts = [s for s in flat if s.kind == "text" and s.text]
    pics = [s for s in flat if s.kind == "picture"]
    tables = [s for s in flat if s.kind == "table"]
    charts = [s for s in flat if s.kind == "chart"]
    lines = [s for s in flat if s.kind == "line"]

    big = _big_texts(flat)
    small = _small_texts(flat)
    total_chars = sum(len(s.text or "") for s in texts)
    cols = _dominant_col_count(big)

    # Order: most specific first
    if tables:
        return "table_slide"
    if charts:
        return "chart_slide"  # archetype not yet defined — will fall back to generic
    if len(pics) >= 4 and small:
        return "logo_strip"

    # Cover: one large hero picture anchored at top, spanning most of slide height
    if pics and any(
        p.w_in > 6 and p.h_in > 6 and p.top_in < 1.0 for p in pics
    ) and len(texts) <= 4:
        return "cover_with_hero"

    # 4-col with vertical separator lines = numbered flow
    if cols == 4 and any(_line_is_vertical(ln) for ln in lines):
        return "4_col_numbered_flow"

    # 4-col with images = card-with-image
    if cols == 4 and pics:
        return "4col_card_with_image"

    # 3-col pill cards: three aligned big-text columns at similar top
    if cols == 3:
        return "3col_pill_cards"

    # text + image (dominant): 1 or 2 cols of text with a picture
    if pics and cols <= 2 and len(texts) >= 2:
        return "text_left_image_right"

    # Long-form body
    if total_chars > 300 and not pics and cols <= 1:
        return "text_heavy_body"

    return "generic_layout"


def _line_is_vertical(ln: FlatShape) -> bool:
    return ln.h_in > ln.w_in and ln.h_in > 1.0


def classify_with_debug(shapes: list[FlatShape]) -> dict[str, object]:
    """Classify + return the signal counts used, for test debugging."""
    flat = flatten(shapes)
    big = _big_texts(flat)
    small = _small_texts(flat)
    return {
        "archetype": classify(shapes),
        "element_counts": dict(Counter(s.kind for s in flat)),
        "big_text_count": len(big),
        "small_text_count": len(small),
        "dominant_col_count": _dominant_col_count(big),
        "total_chars": sum(len(s.text or "") for s in flat if s.kind == "text"),
    }
