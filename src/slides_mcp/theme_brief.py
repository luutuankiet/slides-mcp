"""Theme brief — the "deck DNA" carried inside the deck itself.

Decision R (Phase 2): cross-slide visual coherence is expressed via a **meta-slide**
embedded in the deck. The slide is title-marked, `isSkipped`-hidden from presentation,
and carries a YAML brief in a visible body text box.

The agent sets the brief once per deck from the user's intent; every subsequent
`create_slide` resolves missing content fields from the brief (per-slide content
still wins — Decision O extended one scope upward).

Rationale for in-deck storage (vs external config/file):
  - Fork-safe: a fresh agent session reading the deck *sees* the brief.
  - Server-stateless: the server is still a renderer, never a brand-holder.
  - Consumer-driven: the deck itself carries its identity, authored by the agent
    from the user's prompt.
  - Inspectable: the brief is human-readable YAML; a dev looking at the deck
    in the Slides editor sees warnings + the data.
  - Deletion-recoverable: Google Slides version history restores the slide.

Safety:
  - Title = `__SLIDES_MCP_THEME_BRIEF__ — DO NOT DELETE` (unambiguous marker)
  - `isSkipped=True` → hidden from presentation mode, still visible in editor
  - Visible warning preamble in the body explains what deleting costs
  - All MCP tools that touch the brief are tolerant of absence (fall through to
    theme YAML, matching Phase 1 fallback semantics)
"""
from __future__ import annotations

import copy as _copy_mod
from typing import Any

import yaml

# ---------------------------------------------------------------------------
# Constants — the marker shape a meta slide carries
# ---------------------------------------------------------------------------

BRIEF_TITLE_MARKER: str = "__SLIDES_MCP_THEME_BRIEF__"
"""Unambiguous literal that identifies a meta-slide. Scanned via `get_deck_outline`
title lookup (exact prefix match)."""

BRIEF_TITLE: str = f"{BRIEF_TITLE_MARKER} — DO NOT DELETE"
"""Full visible title text on the meta slide's marker text box."""

WARNING_PREAMBLE: str = (
    "⚠ slides-mcp metadata — deleting this slide resets cross-slide theme coherence.\n"
    "This is a hidden slide (isSkipped=True) used by slides-mcp to store the deck's\n"
    "visual brief. Google Slides version history restores it if accidentally removed.\n"
    "Edit via MCP tools (set_theme_brief, update_theme_brief), not by hand.\n"
    "---\n"
)
"""Human-facing warning rendered above the YAML body so a dev opening the deck
sees the explanation in situ."""

SCHEMA_VERSION: int = 1
"""Brief schema version. Bump when the shape of the brief dict changes in a way
that would break parse-back. Parsers should tolerate older versions or error clearly."""

YAML_ROOT_KEY: str = "__slides_mcp_theme_brief"
"""Root YAML key on the serialized brief. Doubles as a 'is this a brief' guard when
parsing the body text box."""

# Default object-id prefixes so the meta slide's text boxes are recognizable.
# Non-authoritative for discovery (we re-scan by content) but useful for debug.
META_SLIDE_ID_PREFIX: str = "theme_brief_"
MARKER_BOX_ID_PREFIX: str = "tb_brief_marker_"
BODY_BOX_ID_PREFIX: str = "tb_brief_body_"


# ---------------------------------------------------------------------------
# Defaults + shape
# ---------------------------------------------------------------------------

DEFAULT_BRIEF: dict[str, Any] = {
    "version": SCHEMA_VERSION,
    "palette": {
        "surface": "#0F1A4A",
        "accent": "#E8612E",
        "text": "#000000",
        "category_set": ["#E8612E", "#0F1A4A", "#888888"],
    },
    "shape_language": "sharp",
    "numbering_style": "bold",
    "tone": "clean editorial",
    "image_prompt_style": "photography, documentary, warm light",
    # font_family is OPTIONAL. When present, overrides theme YAML font.family
    # for the matching role axis (heading or body). Size/weight stay from
    # theme YAML. Back-compat: absent = current behavior.
    "font_family": {
        "heading": "Inter",
        "body": "Inter",
    },
}
"""Illustrative default. The agent overrides per-deck from the user's intent —
this is the *shape* a brief takes, not a brand. Bundled example.yaml style."""

_ALLOWED_SHAPE_LANG = frozenset({"sharp", "rounded", "mixed"})
_ALLOWED_NUMBERING = frozenset({"bold", "outlined", "dot", "hidden"})


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def _hex_or_none(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    v = value.strip()
    if len(v) == 7 and v.startswith("#"):
        try:
            int(v[1:], 16)
        except ValueError:
            return None
        return v.upper()
    return None


def validate_brief(brief: dict[str, Any]) -> tuple[bool, list[str]]:
    """Return (ok, errors). Non-exhaustive: catches shape, enum, hex-format bugs."""
    errors: list[str] = []

    if not isinstance(brief, dict):
        return False, ["brief must be a dict"]

    version = brief.get("version")
    if version is not None and version != SCHEMA_VERSION:
        errors.append(
            f"schema version mismatch: got {version!r}, expected {SCHEMA_VERSION}"
        )

    palette = brief.get("palette")
    if not isinstance(palette, dict):
        errors.append("palette must be a dict")
    else:
        for role in ("surface", "accent", "text"):
            val = palette.get(role)
            if val is not None and _hex_or_none(val) is None:
                errors.append(f"palette.{role} is not a #RRGGBB hex: {val!r}")
        cat_set = palette.get("category_set")
        if cat_set is not None:
            if not isinstance(cat_set, list):
                errors.append("palette.category_set must be a list")
            else:
                for i, hx in enumerate(cat_set):
                    if _hex_or_none(hx) is None:
                        errors.append(
                            f"palette.category_set[{i}] is not a #RRGGBB hex: {hx!r}"
                        )

    sl = brief.get("shape_language")
    if sl is not None and sl not in _ALLOWED_SHAPE_LANG:
        errors.append(
            f"shape_language {sl!r} not in {sorted(_ALLOWED_SHAPE_LANG)}"
        )

    ns = brief.get("numbering_style")
    if ns is not None and ns not in _ALLOWED_NUMBERING:
        errors.append(
            f"numbering_style {ns!r} not in {sorted(_ALLOWED_NUMBERING)}"
        )

    for free_key in ("tone", "image_prompt_style"):
        val = brief.get(free_key)
        if val is not None and not isinstance(val, str):
            errors.append(f"{free_key} must be a string, got {type(val).__name__}")

    ff = brief.get("font_family")
    if ff is not None:
        if not isinstance(ff, dict):
            errors.append("font_family must be a dict with optional 'heading' and 'body'")
        else:
            for axis in ("heading", "body"):
                v = ff.get(axis)
                if v is not None and not isinstance(v, str):
                    errors.append(
                        f"font_family.{axis} must be a string, got {type(v).__name__}"
                    )
                if isinstance(v, str) and not v.strip():
                    errors.append(f"font_family.{axis} must be non-empty if provided")

    return (not errors), errors


def serialize_brief(brief: dict[str, Any]) -> str:
    """Render a brief to the body text a meta slide carries (warning + YAML)."""
    # Normalize: always stamp the current schema version on write.
    normalized = {**brief, "version": SCHEMA_VERSION}
    payload = {YAML_ROOT_KEY: normalized}
    yaml_text = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
    return WARNING_PREAMBLE + yaml_text


def parse_brief_body(body_text: str) -> dict[str, Any] | None:
    """Extract the brief dict from a body text string.

    Tolerates the warning preamble prefix; looks for the first `---` separator
    and parses everything after as YAML. Returns None if the body isn't a
    recognizable brief.
    """
    if not body_text:
        return None
    # Split on the YAML document marker separating preamble from the payload.
    parts = body_text.split("\n---\n", 1)
    payload = parts[1] if len(parts) == 2 else body_text
    try:
        doc = yaml.safe_load(payload)
    except yaml.YAMLError:
        return None
    if not isinstance(doc, dict):
        return None
    brief = doc.get(YAML_ROOT_KEY)
    if not isinstance(brief, dict):
        return None
    return brief


# ---------------------------------------------------------------------------
# Meta-slide discovery
# ---------------------------------------------------------------------------


def _text_of_shape(page_element: dict[str, Any]) -> str:
    """Concatenate the textRun.content across a shape's text elements."""
    shape = page_element.get("shape") or {}
    text = shape.get("text") or {}
    out: list[str] = []
    for te in text.get("textElements", []) or []:
        tr = te.get("textRun")
        if tr and "content" in tr:
            out.append(tr["content"])
    return "".join(out)


def find_meta_slide(prez: dict[str, Any]) -> dict[str, Any] | None:
    """Locate the meta slide inside a presentation dict.

    Scans `prez["slides"]`, returns the first slide whose title text begins with
    `BRIEF_TITLE_MARKER`. Returns None if none found.

    The returned dict carries:
      - slide_id: the meta slide objectId
      - marker_box_id: objectId of the textbox containing the title marker
      - body_box_id: objectId of the textbox containing the YAML body (or None
        if it hasn't been created yet)
      - body_text: raw text content of the body textbox
    """
    for slide in prez.get("slides", []) or []:
        marker_box_id: str | None = None
        body_box_id: str | None = None
        body_text: str = ""
        for element in slide.get("pageElements", []) or []:
            text = _text_of_shape(element)
            if text.startswith(BRIEF_TITLE_MARKER):
                marker_box_id = element.get("objectId")
            elif text.strip():
                # First non-empty non-marker textbox is the body; multiple would
                # be a corrupted state — first-wins is forgiving.
                if body_box_id is None:
                    body_box_id = element.get("objectId")
                    body_text = text
        if marker_box_id:
            return {
                "slide_id": slide.get("objectId"),
                "marker_box_id": marker_box_id,
                "body_box_id": body_box_id,
                "body_text": body_text,
            }
    return None


# ---------------------------------------------------------------------------
# batchUpdate request composition
# ---------------------------------------------------------------------------


_EMU_PER_INCH = 914400


def _inch_to_emu(value: float) -> int:
    return int(round(value * _EMU_PER_INCH))


def build_create_meta_slide_requests(
    slide_id: str,
    marker_box_id: str,
    body_box_id: str,
    brief: dict[str, Any],
    deck_width_in: float,
    deck_height_in: float,
    insertion_index: int,
) -> list[dict[str, Any]]:
    """Compose the Slides API batchUpdate requests that build the meta slide.

    The returned list in order:
      1. createSlide(BLANK layout)
      2. updateSlideProperties(isSkipped=True)
      3. createShape(TEXT_BOX) for marker
      4. insertText(marker box, BRIEF_TITLE)
      5. updateTextStyle(marker box, 24pt bold red)
      6. createShape(TEXT_BOX) for body
      7. insertText(body box, serialized brief)
      8. updateTextStyle(body box, 10pt monospace dark gray)
    """
    body_content = serialize_brief(brief)

    # Slide layout math: marker across top ~0.6", body below ~4.5"
    margin_in = 0.4
    marker_h_in = 0.7
    body_top_in = margin_in + marker_h_in + 0.15
    body_h_in = max(0.5, deck_height_in - body_top_in - margin_in)
    content_w_in = max(0.5, deck_width_in - 2 * margin_in)

    marker_props = {
        "pageObjectId": slide_id,
        "size": {
            "width": {"magnitude": _inch_to_emu(content_w_in), "unit": "EMU"},
            "height": {"magnitude": _inch_to_emu(marker_h_in), "unit": "EMU"},
        },
        "transform": {
            "scaleX": 1,
            "scaleY": 1,
            "translateX": _inch_to_emu(margin_in),
            "translateY": _inch_to_emu(margin_in),
            "unit": "EMU",
        },
    }
    body_props = {
        "pageObjectId": slide_id,
        "size": {
            "width": {"magnitude": _inch_to_emu(content_w_in), "unit": "EMU"},
            "height": {"magnitude": _inch_to_emu(body_h_in), "unit": "EMU"},
        },
        "transform": {
            "scaleX": 1,
            "scaleY": 1,
            "translateX": _inch_to_emu(margin_in),
            "translateY": _inch_to_emu(body_top_in),
            "unit": "EMU",
        },
    }

    return [
        {
            "createSlide": {
                "objectId": slide_id,
                "insertionIndex": insertion_index,
                "slideLayoutReference": {"predefinedLayout": "BLANK"},
            }
        },
        {
            "updateSlideProperties": {
                "objectId": slide_id,
                "slideProperties": {"isSkipped": True},
                "fields": "isSkipped",
            }
        },
        {
            "createShape": {
                "objectId": marker_box_id,
                "shapeType": "TEXT_BOX",
                "elementProperties": marker_props,
            }
        },
        {
            "insertText": {
                "objectId": marker_box_id,
                "text": BRIEF_TITLE,
                "insertionIndex": 0,
            }
        },
        {
            "updateTextStyle": {
                "objectId": marker_box_id,
                "style": {
                    "fontSize": {"magnitude": 20, "unit": "PT"},
                    "bold": True,
                    "foregroundColor": {
                        "opaqueColor": {
                            "rgbColor": {"red": 0.8, "green": 0.1, "blue": 0.1}
                        }
                    },
                },
                "fields": "fontSize,bold,foregroundColor",
                "textRange": {"type": "ALL"},
            }
        },
        {
            "createShape": {
                "objectId": body_box_id,
                "shapeType": "TEXT_BOX",
                "elementProperties": body_props,
            }
        },
        {
            "insertText": {
                "objectId": body_box_id,
                "text": body_content,
                "insertionIndex": 0,
            }
        },
        {
            "updateTextStyle": {
                "objectId": body_box_id,
                "style": {
                    "fontSize": {"magnitude": 9, "unit": "PT"},
                    "fontFamily": "Roboto Mono",
                    "foregroundColor": {
                        "opaqueColor": {
                            "rgbColor": {"red": 0.2, "green": 0.2, "blue": 0.2}
                        }
                    },
                },
                "fields": "fontSize,fontFamily,foregroundColor",
                "textRange": {"type": "ALL"},
            }
        },
    ]


def build_update_brief_requests(
    body_box_id: str,
    brief: dict[str, Any],
) -> list[dict[str, Any]]:
    """Compose requests to replace the body box text with a new serialized brief.

    Emits deleteText(ALL) + insertText(0, new_body). Object-scoped so the edit
    is safe even if the body text appears elsewhere in the deck.
    """
    body_content = serialize_brief(brief)
    return [
        {
            "deleteText": {
                "objectId": body_box_id,
                "textRange": {"type": "ALL"},
            }
        },
        {
            "insertText": {
                "objectId": body_box_id,
                "text": body_content,
                "insertionIndex": 0,
            }
        },
    ]


# ---------------------------------------------------------------------------
# Amendment — forward-only patching
# ---------------------------------------------------------------------------


def merge_brief(existing: dict[str, Any], changes: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge `changes` into `existing`, return a new dict.

    - Top-level keys replace wholesale unless both sides are dicts (then recurse).
    - Lists replace wholesale (no element-wise merge — e.g. category_set is
      replaced entirely when specified).
    - None in `changes` drops the key.
    """
    result = dict(existing)
    for k, v in changes.items():
        if v is None:
            result.pop(k, None)
            continue
        if isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = merge_brief(result[k], v)
        else:
            result[k] = v
    return result


# ---------------------------------------------------------------------------
# Brownfield extraction — propose a brief from an existing deck (Phase 2C)
# ---------------------------------------------------------------------------


def _neutral_hex(hex_value: str) -> bool:
    """True for very-light (near-white) or very-dark (near-black) or mid-gray
    hexes — colors that can't stand alone as an accent. Used to separate
    palette.text (dark neutral) from palette.accent (chromatic)."""
    try:
        h = hex_value.lstrip("#").upper()
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except (ValueError, IndexError):
        return True
    # Very dark: all channels < 40 → text-black territory
    if max(r, g, b) < 40:
        return True
    # Very light: all channels > 240 → background-white
    if min(r, g, b) > 240:
        return True
    # Near-gray: channel spread small (< 15) AND mid range → neutral gray
    if max(r, g, b) - min(r, g, b) < 15:
        return True
    return False


def _canonical_hex(hex_value: str) -> str:
    """Normalize to #RRGGBB uppercase. Used as histogram key so near-dup
    values from different sources collapse to the same bucket."""
    h = hex_value.lstrip("#").upper()
    if len(h) != 6:
        return hex_value.upper()
    return f"#{h}"


def _rgb_from_page_color(
    color_obj: dict[str, Any] | None,
) -> str | None:
    """Slides API colors come as {red, green, blue} fractions (0..1). Missing
    channel = 0. Return #RRGGBB or None if not an RGB color (themeColor etc.)."""
    if not color_obj:
        return None
    rgb = color_obj.get("rgbColor")
    if rgb is None:
        return None
    r = int(round(float(rgb.get("red", 0.0)) * 255))
    g = int(round(float(rgb.get("green", 0.0)) * 255))
    b = int(round(float(rgb.get("blue", 0.0)) * 255))
    return f"#{r:02X}{g:02X}{b:02X}"


def _walk_slide_colors(
    slide: dict[str, Any],
) -> tuple[
    list[tuple[str, int]],  # (fill_hex, area_weight)
    list[str],               # text foreground hexes (per run, repeatable)
    dict[str, int],          # shape-type counts
]:
    """Collect fill hexes (weighted by shape area), text foreground hexes, and
    shape-type counts from a single raw Slides API slide dict."""
    fills: list[tuple[str, int]] = []
    text_colors: list[str] = []
    shape_types: dict[str, int] = {}

    for element in slide.get("pageElements", []) or []:
        shape = element.get("shape") or {}
        shape_type = shape.get("shapeType")
        if shape_type:
            shape_types[shape_type] = shape_types.get(shape_type, 0) + 1

        size = element.get("size") or {}
        w = (size.get("width") or {}).get("magnitude", 0) or 0
        h = (size.get("height") or {}).get("magnitude", 0) or 0
        # EMU² → rough area weight. Clamp to avoid one huge bg dominating.
        area_weight = int((int(w) * int(h)) / 10_000_000_000) or 1

        fill_color = (
            shape.get("shapeProperties", {})
            .get("shapeBackgroundFill", {})
            .get("solidFill", {})
            .get("color")
        )
        fill_hex = _rgb_from_page_color(fill_color)
        if fill_hex:
            fills.append((fill_hex, area_weight))

        text = shape.get("text") or {}
        for te in text.get("textElements", []) or []:
            tr = te.get("textRun")
            if not tr:
                continue
            fg = (
                (tr.get("style") or {})
                .get("foregroundColor", {})
                .get("opaqueColor", {})
            )
            hex_value = _rgb_from_page_color(fg)
            if hex_value:
                text_colors.append(hex_value)

    return fills, text_colors, shape_types


def extract_brief_from_prez(prez: dict[str, Any]) -> dict[str, Any]:
    """Brownfield heuristic: audit an existing deck, propose a theme brief.

    Walks every slide (excluding the meta-slide if present), tallies:
      - fill colors (area-weighted — big bg shapes matter more)
      - text foreground colors (per-run count)
      - shape type counts (for shape_language hint)

    Returns `{proposed_brief, evidence, confidence}` where:
      - proposed_brief matches the schema of set_theme_brief
      - evidence carries the raw histograms so the agent can explain its
        proposal to the user + iterate before committing
      - confidence is "high" / "medium" / "low" based on histogram clarity

    Rationale: the agent calls this on a brownfield deck (e.g. Joon openers),
    reviews the proposal with the user, tweaks if needed, then commits via
    `set_theme_brief`. This is the sole legitimate path from "deck without
    brief" to "deck with brief" — the tool never commits on its own.
    """
    all_fills: dict[str, int] = {}
    all_text_colors: dict[str, int] = {}
    all_shape_types: dict[str, int] = {}
    slides_walked = 0

    meta = find_meta_slide(prez)
    meta_slide_id = meta["slide_id"] if meta else None

    for slide in prez.get("slides", []) or []:
        if slide.get("objectId") == meta_slide_id:
            continue  # don't count the brief slide in its own evidence
        fills, text_colors, shape_types = _walk_slide_colors(slide)
        slides_walked += 1
        for fh, w in fills:
            key = _canonical_hex(fh)
            all_fills[key] = all_fills.get(key, 0) + w
        for tc in text_colors:
            key = _canonical_hex(tc)
            all_text_colors[key] = all_text_colors.get(key, 0) + 1
        for st, c in shape_types.items():
            all_shape_types[st] = all_shape_types.get(st, 0) + c

    # ----- Heuristic: choose palette members -----------------------------
    fills_sorted = sorted(all_fills.items(), key=lambda x: -x[1])
    text_sorted = sorted(all_text_colors.items(), key=lambda x: -x[1])

    # palette.text = most common dark neutral text color (if any)
    text_hex = next(
        (h for h, _ in text_sorted if _neutral_hex(h) and h != "#FFFFFF"),
        None,
    ) or (text_sorted[0][0] if text_sorted else "#000000")

    # palette.accent = most common chromatic (non-neutral) color anywhere
    chromatic_fills = [(h, c) for h, c in fills_sorted if not _neutral_hex(h)]
    chromatic_text = [(h, c) for h, c in text_sorted if not _neutral_hex(h)]
    accent_hex: str | None = None
    if chromatic_text:
        accent_hex = chromatic_text[0][0]
    if accent_hex is None and chromatic_fills:
        accent_hex = chromatic_fills[0][0]
    if accent_hex is None:
        accent_hex = "#E8612E"  # safety-net orange

    # palette.surface = most dominant dark/saturated fill (biggest area)
    # Prefer a dark (neutral-dark) fill first — that's a header bar / background.
    # If no dark fill, fall back to the biggest chromatic fill.
    surface_hex: str | None = None
    for h, _ in fills_sorted:
        try:
            stripped = h.lstrip("#").upper()
            r, g, b = int(stripped[0:2], 16), int(stripped[2:4], 16), int(stripped[4:6], 16)
            if max(r, g, b) < 80:  # dark fill
                surface_hex = h
                break
        except ValueError:
            continue
    if surface_hex is None and chromatic_fills:
        surface_hex = chromatic_fills[0][0]
    if surface_hex is None:
        surface_hex = "#0F1A4A"  # safety-net dark navy

    # palette.category_set = top 3-5 distinct non-surface, non-text chromatic fills.
    # If not enough chromatic fills, pad with chromatic text colors.
    reserved = {surface_hex, text_hex}
    category_pool: list[str] = []
    for h, _ in chromatic_fills:
        if h not in reserved and h not in category_pool:
            category_pool.append(h)
    for h, _ in chromatic_text:
        if h not in reserved and h not in category_pool:
            category_pool.append(h)
    # Always lead with accent if accent isn't surface/text
    if accent_hex not in reserved:
        if accent_hex in category_pool:
            category_pool.remove(accent_hex)
        category_pool.insert(0, accent_hex)
    category_set = category_pool[:5] if category_pool else [accent_hex]

    # ----- Heuristic: shape_language -------------------------------------
    round_count = all_shape_types.get("ROUND_RECTANGLE", 0)
    rect_count = all_shape_types.get("RECTANGLE", 0)
    total_shaped = round_count + rect_count
    if total_shaped == 0:
        shape_language = "sharp"
    else:
        round_ratio = round_count / total_shaped
        if round_ratio > 0.65:
            shape_language = "rounded"
        elif round_ratio < 0.25:
            shape_language = "sharp"
        else:
            shape_language = "mixed"

    # ----- Confidence heuristic ------------------------------------------
    # High: rich histograms (>= 8 distinct fill colors, >= 2 slides walked).
    # Medium: moderate evidence.
    # Low: tiny deck or near-empty histograms.
    distinct_fills = len(all_fills)
    if slides_walked >= 3 and distinct_fills >= 8 and accent_hex != "#E8612E":
        confidence = "high"
    elif slides_walked >= 2 and distinct_fills >= 4:
        confidence = "medium"
    else:
        confidence = "low"

    proposed_brief: dict[str, Any] = {
        "version": SCHEMA_VERSION,
        "palette": {
            "surface": surface_hex,
            "accent": accent_hex,
            "text": text_hex,
            "category_set": category_set,
        },
        "shape_language": shape_language,
        "numbering_style": "bold",  # sensible default, no good extraction signal
        "tone": "",                 # agent fills from user intent
        "image_prompt_style": "",   # agent fills from user intent
    }

    evidence: dict[str, Any] = {
        "slides_walked": slides_walked,
        "distinct_fill_colors": distinct_fills,
        "distinct_text_colors": len(all_text_colors),
        "top_fills": fills_sorted[:8],
        "top_text_colors": text_sorted[:8],
        "shape_types": all_shape_types,
    }
    return {
        "proposed_brief": proposed_brief,
        "evidence": evidence,
        "confidence": confidence,
    }


# ------------------------------------------------------------------
# Variant proposals (v0.5.0 — B1)
# ------------------------------------------------------------------

# Curated mood templates. Each combines a palette with shape/tone/numbering choices
# to produce a distinct-feeling visual identity. Keywords bias the scoring against
# an intent string. Templates intentionally differ on palette.accent (distinctness
# invariant) and on shape_language x numbering_style combinations (structural variety).
#
# Order matters — it's the deterministic tiebreaker when scores are equal. Early
# templates win ties, so put broadly-applicable moods first.
_MOOD_TEMPLATES: list[dict[str, Any]] = [
    {
        "keywords": {"editorial", "magazine", "publication", "narrative", "story",
                     "journal", "feature"},
        "brief": {
            "version": SCHEMA_VERSION,
            "palette": {
                "surface": "#0F1A4A",
                "accent": "#E8612E",
                "text": "#1A1A1A",
                "category_set": ["#E8612E", "#0F1A4A", "#5A6B9A"],
            },
            "shape_language": "sharp",
            "numbering_style": "bold",
            "tone": "clean editorial",
            "image_prompt_style": "documentary photography, warm light",
            "font_family": {"heading": "Fraunces", "body": "Inter"},
        },
    },
    {
        "keywords": {"enterprise", "corporate", "b2b", "confident", "buyer",
                     "executive", "cio", "ceo", "qbr"},
        "brief": {
            "version": SCHEMA_VERSION,
            "palette": {
                "surface": "#134E4A",
                "accent": "#B45309",
                "text": "#1F2937",
                "category_set": ["#B45309", "#134E4A", "#A16207"],
            },
            "shape_language": "sharp",
            "numbering_style": "outlined",
            "tone": "confident enterprise",
            "image_prompt_style": "editorial photography, dark saturated palette",
            "font_family": {"heading": "DM Serif Display", "body": "IBM Plex Sans"},
        },
    },
    {
        "keywords": {"tech", "technical", "data", "analytics", "ai", "software",
                     "saas", "platform", "dashboard", "api"},
        "brief": {
            "version": SCHEMA_VERSION,
            "palette": {
                "surface": "#FFFFFF",
                "accent": "#2563EB",
                "text": "#0F172A",
                "category_set": ["#2563EB", "#0EA5E9", "#8B5CF6"],
            },
            "shape_language": "rounded",
            "numbering_style": "dot",
            "tone": "minimalist technical",
            "image_prompt_style": "isometric illustration, clean vector",
            "font_family": {"heading": "Space Grotesk", "body": "Inter"},
        },
    },
    {
        "keywords": {"warm", "human", "organic", "wellness", "community", "care",
                     "lifestyle", "craft"},
        "brief": {
            "version": SCHEMA_VERSION,
            "palette": {
                "surface": "#FEF3C7",
                "accent": "#7C2D12",
                "text": "#1C1917",
                "category_set": ["#7C2D12", "#A16207", "#78350F"],
            },
            "shape_language": "rounded",
            "numbering_style": "dot",
            "tone": "warm and human",
            "image_prompt_style": "hand-drawn illustration, earthy tones",
            "font_family": {"heading": "Merriweather", "body": "Lora"},
        },
    },
    {
        "keywords": {"bold", "striking", "provocative", "creative", "agency",
                     "pitch", "brand"},
        "brief": {
            "version": SCHEMA_VERSION,
            "palette": {
                "surface": "#1F2937",
                "accent": "#F59E0B",
                "text": "#FFFFFF",
                "category_set": ["#F59E0B", "#EC4899", "#8B5CF6"],
            },
            "shape_language": "sharp",
            "numbering_style": "bold",
            "tone": "bold magazine",
            "image_prompt_style": "high-contrast editorial photography",
            "font_family": {"heading": "Archivo Black", "body": "Archivo"},
        },
    },
    {
        "keywords": {"elegant", "luxury", "serif", "refined", "timeless",
                     "heritage", "fine"},
        "brief": {
            "version": SCHEMA_VERSION,
            "palette": {
                "surface": "#F5F1E8",
                "accent": "#78350F",
                "text": "#1C1917",
                "category_set": ["#78350F", "#44403C", "#A8A29E"],
            },
            "shape_language": "sharp",
            "numbering_style": "outlined",
            "tone": "elegant editorial",
            "image_prompt_style": "fine-art photography, natural light",
            "font_family": {"heading": "Playfair Display", "body": "Source Sans Pro"},
        },
    },
]


# ---------------------------------------------------------------------------
# Font pairings — curated Google Fonts pairs, mood-tagged.
# Used by `list_font_pairings` MCP tool and `propose_brief_variants` to fill
# the font_family axis on each variant. Each pairing is a single Google Fonts
# combo (heading + body) tagged with mood keywords.
#
# Constraint: stay with Google Fonts catalog — free, web-available, widely
# cached. No designer-only foundry picks.
# ---------------------------------------------------------------------------

FONT_PAIRINGS: list[dict[str, Any]] = [
    {
        "id": "inter_duo",
        "heading": "Inter",
        "body": "Inter",
        "mood": ["neutral", "modern", "tech", "saas", "default"],
        "rationale": "Single-face system default. Readable at every size. Safe pick when tone is undecided.",
    },
    {
        "id": "fraunces_inter",
        "heading": "Fraunces",
        "body": "Inter",
        "mood": ["editorial", "magazine", "narrative", "warm"],
        "rationale": "Fraunces gives distinctive editorial headlines; Inter keeps body crisp and modern.",
    },
    {
        "id": "space_grotesk_plex",
        "heading": "Space Grotesk",
        "body": "IBM Plex Sans",
        "mood": ["tech", "data", "saas", "minimalist"],
        "rationale": "Space Grotesk's geometric headline + IBM Plex's humanist body reads as a data-forward tool.",
    },
    {
        "id": "dm_serif_dm_sans",
        "heading": "DM Serif Display",
        "body": "DM Sans",
        "mood": ["enterprise", "confident", "buyer", "b2b"],
        "rationale": "DM Serif's weight reads executive; DM Sans matches for consistent grid.",
    },
    {
        "id": "playfair_source",
        "heading": "Playfair Display",
        "body": "Source Sans Pro",
        "mood": ["elegant", "luxury", "serif", "timeless"],
        "rationale": "Playfair's high-contrast serif leads; Source Sans provides clean utility text.",
    },
    {
        "id": "merriweather_lora",
        "heading": "Merriweather",
        "body": "Lora",
        "mood": ["warm", "editorial", "human", "longform"],
        "rationale": "Two serifs — warm body pairing that signals longform, unhurried narrative.",
    },
    {
        "id": "archivo_black_archivo",
        "heading": "Archivo Black",
        "body": "Archivo",
        "mood": ["bold", "agency", "pitch", "creative"],
        "rationale": "Archivo Black screams. Archivo normal keeps body readable. Same family = tight system.",
    },
    {
        "id": "ibm_plex_serif_sans",
        "heading": "IBM Plex Serif",
        "body": "IBM Plex Sans",
        "mood": ["technical", "enterprise", "engineered"],
        "rationale": "Engineered family with matching proportions; serif heading signals thought leadership.",
    },
    {
        "id": "manrope_manrope",
        "heading": "Manrope",
        "body": "Manrope",
        "mood": ["modern", "startup", "clean", "ui"],
        "rationale": "Geometric humanist single-face. Reads app-like, fresh. Good for product decks.",
    },
    {
        "id": "libre_baskerville_libre_franklin",
        "heading": "Libre Baskerville",
        "body": "Libre Franklin",
        "mood": ["heritage", "editorial", "institutional"],
        "rationale": "Traditional serif heading with sans body. Reads like a quality long-read publication.",
    },
    {
        "id": "oswald_lato",
        "heading": "Oswald",
        "body": "Lato",
        "mood": ["sports", "impactful", "condensed", "training"],
        "rationale": "Condensed display headline + calm body. Works for training decks, kickoffs.",
    },
    {
        "id": "crimson_nunito",
        "heading": "Crimson Text",
        "body": "Nunito Sans",
        "mood": ["academic", "research", "serif", "calm"],
        "rationale": "Academic serif heading with friendly rounded sans. Reads like a thoughtful white paper.",
    },
]


def list_font_pairings(mood: str | None = None) -> list[dict[str, Any]]:
    """Return curated Google Fonts pairings; optionally filtered by mood keyword.

    Pure function. Case-insensitive substring match on mood tags AND the mood
    parameter also matches against any word in the pairing's mood list.

    Returns a FRESH list of dicts — callers can mutate without side effect.

    Shape:
        [{id, heading, body, mood: [str, ...], rationale: str}, ...]
    """
    if not mood:
        return [dict(p) for p in FONT_PAIRINGS]
    needle = mood.strip().lower()
    if not needle:
        return [dict(p) for p in FONT_PAIRINGS]
    result: list[dict[str, Any]] = []
    for p in FONT_PAIRINGS:
        tags = [t.lower() for t in p.get("mood", [])]
        if any(needle in t or t in needle for t in tags):
            result.append(dict(p))
    return result


def propose_brief_variants(
    intent: str,
    n: int = 3,
    exclude_accents: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Propose n distinct-mood theme briefs from natural-language intent.

    exclude_accents: list of hex colors to skip. Useful when the deck already
        has a brief with a known accent and the caller wants N alternatives
        (not N including the current one). Case-insensitive hex match.

    Pure function — deterministic: same (intent, n) → same returned list.

    Strategy: each mood template has a keyword set. For each template, score
    against intent (case-insensitive substring match, one point per matched
    keyword). Sort by score DESC, tiebreak by template order. Return top-n
    briefs, enforcing palette-accent distinctness (any tie on accent drops
    the later template and continues to the next).

    Guarantees:
      - 0 <= len(result) <= min(n, len(_MOOD_TEMPLATES))
      - Every returned brief passes validate_brief
      - No two returned briefs share the same palette.accent
      - Each brief is a FRESH copy — caller can mutate without poisoning
        the next call

    n <= 0 returns an empty list.
    """
    if n <= 0:
        return []

    excluded = {h.upper() for h in (exclude_accents or []) if isinstance(h, str)}
    intent_lower = (intent or "").lower()
    scored: list[tuple[int, int, dict[str, Any]]] = []
    for i, template in enumerate(_MOOD_TEMPLATES):
        keywords = template.get("keywords") or set()
        score = sum(1 for kw in keywords if kw in intent_lower)
        # (-score, index) sort gives score DESC, index ASC tiebreak
        scored.append((-score, i, template["brief"]))

    scored.sort()

    result: list[dict[str, Any]] = []
    seen_accents: set[str] = set()
    for _score, _i, brief in scored:
        accent = (brief.get("palette") or {}).get("accent")
        if accent in seen_accents:
            continue
        if accent and accent.upper() in excluded:
            continue
        seen_accents.add(accent)
        result.append(_copy_mod.deepcopy(brief))
        if len(result) == n:
            break
    return result


# ---------------------------------------------------------------------------
# Coherence audit (H1) — "did the deck obey the brief?"
# ---------------------------------------------------------------------------

_NEAR_BLACK_MAX: int = 32  # per-channel max for near-black classification
_NEAR_WHITE_MIN: int = 240  # per-channel min for near-white classification
_COHERENCE_COLOR_DIST: int = 40  # RGB-sum distance under which a hex counts as matching a brief palette member


def _hex_channels(hex_value: str) -> tuple[int, int, int] | None:
    h = (hex_value or "").lstrip("#").upper()
    if len(h) != 6:
        return None
    try:
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except ValueError:
        return None


def _is_near_neutral(hex_value: str) -> bool:
    """Return True for near-black, near-white, and mid-gray; they never count as drift."""
    ch = _hex_channels(hex_value)
    if not ch:
        return False
    r, g, b = ch
    if all(c <= _NEAR_BLACK_MAX for c in (r, g, b)):
        return True
    if all(c >= _NEAR_WHITE_MIN for c in (r, g, b)):
        return True
    # gray within 12 channel spread
    if max(r, g, b) - min(r, g, b) <= 12:
        return True
    return False


def _rgb_sum_dist(a: tuple[int, int, int], b: tuple[int, int, int]) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1]) + abs(a[2] - b[2])


def _hex_in_brief_palette(hex_value: str, brief_hexes: set[str]) -> bool:
    """True if hex matches any brief palette member within _COHERENCE_COLOR_DIST."""
    ch = _hex_channels(hex_value)
    if ch is None:
        return False
    for bh in brief_hexes:
        bch = _hex_channels(bh)
        if bch is None:
            continue
        if _rgb_sum_dist(ch, bch) <= _COHERENCE_COLOR_DIST:
            return True
    return False


_HEADING_FONT_ROLE_HINTS: tuple[str, ...] = (
    "display", "title", "heading", "num", "pill", "big",
)


def _role_is_heading_from_size(size_pt: float | None) -> bool:
    """Fallback classifier: runs with size_pt >= 24 are heading-class.

    Used when we don't have a theme role annotation (raw deck walk — runs
    don't carry the original theme role). Size is an OK proxy.
    """
    if size_pt is None:
        return False
    return float(size_pt) >= 24.0


def audit_brief_coherence(
    prez: dict[str, Any],
    brief: dict[str, Any] | None = None,
    slide_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Walk non-meta slides and compare palette/fonts/shapes to brief.

    slide_ids: when non-empty, restrict the walk to slides whose `objectId`
        appears in this list. Use this to audit ONLY a freshly-generated
        batch (e.g. slides just emitted by create_slide) without pre-existing
        legacy content dragging down the score. When None (default), walks
        every non-meta slide.

    Returns a structured coherence report:

        {
          brief_active: bool,
          brief_used: dict | None,           # echoed for convenience
          coherence_score: float 0..1,       # composite, weighted
          sub_scores: {palette, font, shape},
          drift_by_kind: {palette, font, shape},   # drift counts
          slides_with_drift: [                # cap ~20
            {slide_id, drift_fields: [str], fix_hint: str},
            ...
          ],
          most_common_overrides: [           # hexes NOT in brief, by frequency
            {hex, count, context: "fill"|"text"},
            ...
          ],
          observations: {
            slides_walked, palette_total, palette_matching,
            font_total, font_matching, shape_total, shape_matching,
          },
        }

    Near-neutral colors (black / white / mid-gray) are considered always-matching
    and excluded from drift counts — they're structural (text on surface) not
    brand-expressive.

    If brief is None (no active brief), returns `brief_active: False` with
    zero drift counts and coherence_score 0.0 — caller knows to set a brief first.
    """
    # --- Assemble brief guards --------------------------------------------
    brief_active = brief is not None and isinstance(brief.get("palette"), dict)
    if not brief_active:
        return {
            "brief_active": False,
            "brief_used": None,
            "coherence_score": 0.0,
            "sub_scores": {"palette": 0.0, "font": 0.0, "shape": 0.0},
            "drift_by_kind": {"palette": 0, "font": 0, "shape": 0},
            "slides_with_drift": [],
            "most_common_overrides": [],
            "observations": {
                "slides_walked": 0,
                "palette_total": 0,
                "palette_matching": 0,
                "font_total": 0,
                "font_matching": 0,
                "shape_total": 0,
                "shape_matching": 0,
            },
            "next_action_hint": (
                "no active brief — run extract_theme_brief + set_theme_brief first, "
                "or propose_brief_variants + render_brief_swatch_grid to start fresh"
            ),
        }

    palette = brief["palette"]
    brief_hexes: set[str] = set()
    for role in ("surface", "accent", "text"):
        v = palette.get(role)
        if isinstance(v, str):
            brief_hexes.add(v.upper())
    for v in (palette.get("category_set") or []):
        if isinstance(v, str):
            brief_hexes.add(v.upper())

    ff = brief.get("font_family") or {}
    brief_heading_family = ff.get("heading") if isinstance(ff, dict) else None
    brief_body_family = ff.get("body") if isinstance(ff, dict) else None
    brief_shape_lang = (brief.get("shape_language") or "").lower()

    # --- Walk slides -------------------------------------------------------
    meta = find_meta_slide(prez)
    meta_slide_id = meta["slide_id"] if meta else None

    palette_total = 0
    palette_matching = 0
    font_total = 0
    font_matching = 0
    shape_round = 0
    shape_rect = 0
    slides_walked = 0
    slide_drifts: list[dict[str, Any]] = []
    override_counts: dict[str, list[int]] = {}
    # override_counts[hex] = [count, fill_count, text_count]

    def _bump_override(hex_value: str, kind: str) -> None:
        bucket = override_counts.setdefault(hex_value, [0, 0, 0])
        bucket[0] += 1
        bucket[1 if kind == "fill" else 2] += 1

    filter_set = set(slide_ids) if slide_ids else None
    for slide in prez.get("slides", []) or []:
        sid = slide.get("objectId")
        if sid == meta_slide_id:
            continue
        if filter_set is not None and sid not in filter_set:
            continue
        slides_walked += 1
        slide_drift_fields: set[str] = set()

        for element in slide.get("pageElements", []) or []:
            shape = element.get("shape") or {}
            shape_type = shape.get("shapeType")
            if shape_type == "ROUND_RECTANGLE":
                shape_round += 1
            elif shape_type == "RECTANGLE":
                shape_rect += 1

            # fill color
            fill = (
                shape.get("shapeProperties", {})
                .get("shapeBackgroundFill", {})
                .get("solidFill", {})
                .get("color")
            )
            fill_hex = _rgb_from_page_color(fill)
            if fill_hex and not _is_near_neutral(fill_hex):
                palette_total += 1
                if _hex_in_brief_palette(fill_hex, brief_hexes):
                    palette_matching += 1
                else:
                    slide_drift_fields.add("palette.fill")
                    _bump_override(_canonical_hex(fill_hex), "fill")

            # text runs
            text = shape.get("text") or {}
            for te in text.get("textElements", []) or []:
                tr = te.get("textRun")
                if not tr:
                    continue
                style = tr.get("style") or {}
                fg = style.get("foregroundColor", {}).get("opaqueColor", {})
                text_hex = _rgb_from_page_color(fg)
                if text_hex and not _is_near_neutral(text_hex):
                    palette_total += 1
                    if _hex_in_brief_palette(text_hex, brief_hexes):
                        palette_matching += 1
                    else:
                        slide_drift_fields.add("palette.text")
                        _bump_override(_canonical_hex(text_hex), "text")

                family = style.get("fontFamily")
                size_pt = (style.get("fontSize") or {}).get("magnitude")
                if family and (brief_heading_family or brief_body_family):
                    font_total += 1
                    target = brief_heading_family if _role_is_heading_from_size(size_pt) else brief_body_family
                    if not target:
                        # axis not set in brief — skip, don't count as drift
                        font_total -= 1
                    elif family.strip().lower() == target.strip().lower():
                        font_matching += 1
                    else:
                        slide_drift_fields.add("font_family")

        if slide_drift_fields:
            hint_parts = []
            if "palette.fill" in slide_drift_fields or "palette.text" in slide_drift_fields:
                hint_parts.append("call restyle_slides(slide_ids=[this], confirm_destructive=True)")
            if "font_family" in slide_drift_fields:
                hint_parts.append("call restyle_slides(..., normalize_fonts=True)")
            slide_drifts.append({
                "slide_id": sid,
                "drift_fields": sorted(slide_drift_fields),
                "fix_hint": " + ".join(hint_parts) if hint_parts else "",
            })

    # --- Compute shape score ----------------------------------------------
    shape_total = shape_round + shape_rect
    if shape_total == 0 or not brief_shape_lang:
        shape_matching = 0
    else:
        if brief_shape_lang == "rounded":
            shape_matching = shape_round
        elif brief_shape_lang == "sharp":
            shape_matching = shape_rect
        else:  # mixed
            shape_matching = shape_total

    # --- Sub-scores + composite ------------------------------------------
    def _ratio(n: int, d: int) -> float:
        return (n / d) if d > 0 else 1.0  # empty bucket = vacuously match

    palette_score = _ratio(palette_matching, palette_total)
    font_score = _ratio(font_matching, font_total) if font_total else 1.0
    shape_score = _ratio(shape_matching, shape_total) if shape_total else 1.0

    # Weighted: palette 50%, font 30%, shape 20%
    composite = round(
        palette_score * 0.5 + font_score * 0.3 + shape_score * 0.2, 3
    )

    # Most common overrides (top 10)
    override_list = [
        {"hex": h, "count": v[0], "fill_count": v[1], "text_count": v[2]}
        for h, v in sorted(override_counts.items(), key=lambda kv: -kv[1][0])[:10]
    ]

    # Next-action hint
    if composite >= 0.9:
        hint = "coherence strong — deck ready to ship"
    elif composite >= 0.7:
        hint = (
            "minor drift — consider restyle_slides(slide_ids=[drift slides], "
            "confirm_destructive=True) to tidy"
        )
    elif composite >= 0.4:
        hint = (
            "moderate drift — restyle_slides(slide_ids='all', "
            "normalize_fonts=True, confirm_destructive=True) recommended"
        )
    else:
        hint = (
            "major drift — re-inspect brief (likely too narrow for this deck) "
            "or commit to restyle_slides across the whole deck"
        )

    return {
        "brief_active": True,
        "brief_used": brief,
        "coherence_score": composite,
        "sub_scores": {
            "palette": round(palette_score, 3),
            "font": round(font_score, 3),
            "shape": round(shape_score, 3),
        },
        "drift_by_kind": {
            "palette": palette_total - palette_matching,
            "font": font_total - font_matching,
            "shape": shape_total - shape_matching,
        },
        "slides_with_drift": slide_drifts[:20],
        "most_common_overrides": override_list,
        "observations": {
            "slides_walked": slides_walked,
            "palette_total": palette_total,
            "palette_matching": palette_matching,
            "font_total": font_total,
            "font_matching": font_matching,
            "shape_total": shape_total,
            "shape_matching": shape_matching,
        },
        "next_action_hint": hint,
    }
