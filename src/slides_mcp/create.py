"""Build batchUpdate Requests for a new slide from archetype + semantic content.

The companion of `projection.py`: projection reads (shapes -> YAML); this module
writes (YAML-ish dict -> Slides API Requests). Per-archetype builders map
each slot in `content` to one or more Request dicts using
`archetype.geometry_defaults` for positioning and the active `SubTheme`
for fonts + palette.
"""
from __future__ import annotations

import dataclasses
from collections.abc import Callable
from typing import Any

from . import archetypes as arch_mod
from . import icons as icons_mod
from . import theme as theme_mod

# Font roles considered "heading-class" for brief.font_family.heading override.
# Matched by substring against the font_role string on each slot. Everything
# not matched falls through to the body-class family.
_HEADING_ROLE_SUBSTRINGS: tuple[str, ...] = (
    "display", "title", "heading", "num", "pill", "big",
)


def _is_heading_role(font_role: str | None) -> bool:
    if not font_role:
        return False
    low = font_role.lower()
    return any(tok in low for tok in _HEADING_ROLE_SUBSTRINGS)


def _apply_brief_fonts_to_sub(
    sub: theme_mod.SubTheme,
    brief: dict[str, Any] | None,
) -> theme_mod.SubTheme:
    """Overlay brief.font_family onto a SubTheme, preserving size/weight.

    Returns a FRESH SubTheme with every FontSpec's `family` swapped per the
    brief's heading/body axis. Size and weight are preserved from the theme
    YAML — brief only controls the family name.

    Role classification is substring-based (see `_is_heading_role`). A role
    matching any heading token picks brief.font_family.heading; everything
    else picks brief.font_family.body.

    If brief is None or has no font_family, returns `sub` unchanged.
    If a specific axis is absent (e.g. only heading set), the other axis
    falls through to the theme YAML family — agent can migrate one axis at
    a time.
    """
    if brief is None:
        return sub
    ff = brief.get("font_family")
    if not isinstance(ff, dict):
        return sub
    heading_family = ff.get("heading") if isinstance(ff.get("heading"), str) else None
    body_family = ff.get("body") if isinstance(ff.get("body"), str) else None
    if not heading_family and not body_family:
        return sub

    new_fonts: dict[str, theme_mod.FontSpec] = {}
    for role, spec in sub.fonts.items():
        target_family = heading_family if _is_heading_role(role) else body_family
        if target_family and target_family != spec.family:
            new_fonts[role] = dataclasses.replace(spec, family=target_family)
        else:
            new_fonts[role] = spec
    return dataclasses.replace(sub, fonts=new_fonts)

_EMU_PER_INCH = 914400

# Archetype YAML geometry is authored against this reference deck size.
# At build time we scale to the caller's actual deck dimensions so shapes
# land inside the slide regardless of page size (10×5.625, 13.33×7.5, 16×9, …).
_REF_WIDTH_IN = 16.0
_REF_HEIGHT_IN = 9.0


def _inch_to_emu(v: float) -> int:
    return int(round(v * _EMU_PER_INCH))


def _hex_to_rgb_fracs(hex_value: str) -> dict[str, float]:
    h = hex_value.lstrip("#").upper()
    if len(h) != 6:
        raise ValueError(f"expected 6-digit hex, got {hex_value!r}")
    return {
        "red": int(h[0:2], 16) / 255,
        "green": int(h[2:4], 16) / 255,
        "blue": int(h[4:6], 16) / 255,
    }


def _new_id(prefix: str = "c_") -> str:
    import secrets
    return f"{prefix}{secrets.token_hex(5)}"


def _brief_get(brief: dict[str, Any] | None, path: str) -> Any:
    """Navigate a dotted path inside a theme brief dict.

    Returns None if brief is None, path doesn't resolve, or an intermediate
    key isn't a dict. Quiet — never raises.

    Used by builders to fall back from per-slide content to deck-level brief
    defaults (Phase 2 Decision R resolution order: per_slide > brief > theme).
    Examples:
      _brief_get(brief, "palette.accent")       -> "#E8612E" or None
      _brief_get(brief, "palette.category_set") -> [...]     or None
      _brief_get(brief, "shape_language")       -> "sharp"   or None
    """
    if brief is None:
        return None
    cur: Any = brief
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
        if cur is None:
            return None
    return cur


def _element_props(
    slide_id: str,
    left_in: float,
    top_in: float,
    w_in: float,
    h_in: float,
    sx: float = 1.0,
    sy: float = 1.0,
) -> dict:
    """Build PageElementProperties. sx/sy scale both position and size from the
    archetype reference (16×9) into the caller's actual deck inches."""
    return {
        "pageObjectId": slide_id,
        "size": {
            "width": {"magnitude": _inch_to_emu(w_in * sx), "unit": "EMU"},
            "height": {"magnitude": _inch_to_emu(h_in * sy), "unit": "EMU"},
        },
        "transform": {
            "scaleX": 1,
            "scaleY": 1,
            "translateX": _inch_to_emu(left_in * sx),
            "translateY": _inch_to_emu(top_in * sy),
            "unit": "EMU",
        },
    }


def _text_style_update(
    shape_id: str,
    font: theme_mod.FontSpec | None,
    text_color_hex: str | None = None,
) -> dict | None:
    if font is None and not text_color_hex:
        return None
    style: dict[str, Any] = {}
    fields: list[str] = []
    if font:
        style["fontFamily"] = font.family
        style["fontSize"] = {"magnitude": font.size_pt, "unit": "PT"}
        style["bold"] = font.weight >= 600
        fields.extend(["fontFamily", "fontSize", "bold"])
    if text_color_hex:
        style["foregroundColor"] = {
            "opaqueColor": {"rgbColor": _hex_to_rgb_fracs(text_color_hex)}
        }
        fields.append("foregroundColor")
    return {
        "updateTextStyle": {
            "objectId": shape_id,
            "style": style,
            "textRange": {"type": "ALL"},
            "fields": ",".join(fields),
        }
    }


def _build_text_slot(
    slide_id: str,
    geom: dict[str, Any],
    sub: theme_mod.SubTheme,
    text: str,
    fill_hex: str | None = None,
    text_color_hex: str | None = None,
    font_role_override: str | None = None,
    shape_type: str | None = None,
    sx: float = 1.0,
    sy: float = 1.0,
) -> list[dict]:
    """Compose createShape + optional fill + insertText + optional text styling for one slot.

    sx/sy scale geometry from archetype reference (16×9) into actual deck inches.
    (Note: Slides API rejects write-side autofit — 'Autofit types other than
    NONE are not supported'. Long pill labels handled by caller choosing
    short labels or the pill_header font size in the theme.)
    """
    new_id = _new_id()
    resolved_shape = shape_type or ("RECTANGLE" if fill_hex else "TEXT_BOX")
    reqs: list[dict] = [
        {
            "createShape": {
                "objectId": new_id,
                "shapeType": resolved_shape,
                "elementProperties": _element_props(
                    slide_id,
                    float(geom.get("left_in", 0.5)),
                    float(geom.get("top_in", 0.5)),
                    float(geom.get("w_in", 1.0)),
                    float(geom.get("h_in", 1.0)),
                    sx=sx,
                    sy=sy,
                ),
            }
        }
    ]
    if fill_hex:
        # Set fill + force autofit=NONE explicitly. Google Slides API (post
        # 2026-04 update) auto-applies a non-NONE autofit on text-containing
        # shapes during insertText, and subsequent updateShapeProperties on
        # ANY shape in the batch fail with "Autofit types other than NONE
        # are not supported." Setting autofit=NONE in the SAME request that
        # sets fill keeps us in the supported state. autofit is a no-op for
        # text-less shapes (dots/bars), so it's safe to apply unconditionally.
        reqs.append(
            {
                "updateShapeProperties": {
                    "objectId": new_id,
                    "fields": "shapeBackgroundFill.solidFill.color,autofit.autofitType",
                    "shapeProperties": {
                        "shapeBackgroundFill": {
                            "solidFill": {"color": {"rgbColor": _hex_to_rgb_fracs(fill_hex)}}
                        },
                        "autofit": {"autofitType": "NONE"},
                    },
                }
            }
        )
    if text:
        reqs.append({"insertText": {"objectId": new_id, "text": text, "insertionIndex": 0}})
        font_role = font_role_override or geom.get("font_role")
        font = sub.fonts.get(font_role) if font_role else None
        style_req = _text_style_update(new_id, font, text_color_hex)
        if style_req:
            reqs.append(style_req)
    return reqs


def _build_text_heavy_body(
    slide_id: str,
    content: dict[str, Any],
    arch: arch_mod.Archetype,
    sub: theme_mod.SubTheme,
    sx: float = 1.0,
    sy: float = 1.0,
    brief: dict[str, Any] | None = None,
) -> list[dict]:
    """Minimal text layout (title + paragraph body). No brief-driven visuals —
    kept deliberately minimal as the structural-variety fallback. The brief
    still applies indirectly via tone + image_prompt_style (agent-side hints,
    not builder-side logic).
    """
    _ = brief  # reserved for future symmetric extension; no builder-side wiring
    geom = arch.geometry_defaults
    reqs: list[dict] = []
    if content.get("title") and "title" in geom:
        reqs.extend(_build_text_slot(slide_id, geom["title"], sub, str(content["title"]), sx=sx, sy=sy))
    paragraphs = content.get("paragraphs") or []
    body_text = "\n\n".join(str(p) for p in paragraphs)
    if body_text and "body" in geom:
        reqs.extend(_build_text_slot(slide_id, geom["body"], sub, body_text, sx=sx, sy=sy))
    return reqs


def _build_cover_with_hero(
    slide_id: str,
    content: dict[str, Any],
    arch: arch_mod.Archetype,
    sub: theme_mod.SubTheme,
    sx: float = 1.0,
    sy: float = 1.0,
    brief: dict[str, Any] | None = None,
) -> list[dict]:
    """Build a cover slide — hero image + title + subtitle.

    Content-driven visual identity (LOG-014 Step 6 rewrite):

      - `hero.side`: "left" (default), "right", or "fullbleed". Side modes
        put the image on one half and the text block on the other.
        Fullbleed spans the image across the full 16×9 reference and
        overlays the text block centered.
      - `hero.url` / `hero.prompt`: dual-mode image slot matching the
        `create_image` tool. URL → createImage; prompt → RECTANGLE +
        `[IMAGE: <prompt>]` placeholder. Omit both to render a text-only
        cover (title + subtitle only, positioned for the side implied by
        `hero.side` or the archetype default).
      - `title_color_hex`, `subtitle_color_hex`: optional overrides. For
        fullbleed mode with a bright raster, pass a pale color (e.g.
        `#FFFFFF`) to keep text legible. Theme palette is fallback only.

    Z-order: the hero image is emitted FIRST so subsequent title/subtitle
    shapes land on top — matters for fullbleed mode where text overlays
    the image.

    Backward-compat: when `hero` is unset, behaves like the pre-LOG-014
    builder (title + optional subtitle, text block positioned per default
    side). Single-file test_cover_with_hero_basic passes unchanged.
    """
    geom = arch.geometry_defaults
    reqs: list[dict] = []

    hero = content.get("hero") or {}
    hero_url, hero_prompt = _extract_image_spec(hero if hero else None)
    side_raw = str(hero.get("side", "left")).lower() if isinstance(hero, dict) else "left"
    side = side_raw if side_raw in ("left", "right", "fullbleed") else "left"

    hero_meta = geom.get("hero") or {}
    hero_w = float(hero_meta.get("w_in", 7.5))
    hero_h = float(hero_meta.get("h_in", _REF_HEIGHT_IN))
    hero_top = float(hero_meta.get("top_in", 0.0))

    # --- Hero image / placeholder slot (emit first for z-order) --------
    if hero_url or hero_prompt:
        if side == "fullbleed":
            hero_geom = {
                "left_in": 0.0, "top_in": 0.0,
                "w_in": _REF_WIDTH_IN, "h_in": _REF_HEIGHT_IN,
            }
        else:
            hero_left = 0.0 if side == "left" else _REF_WIDTH_IN - hero_w
            hero_geom = {
                "left_in": hero_left, "top_in": hero_top,
                "w_in": hero_w, "h_in": hero_h,
            }
        reqs.extend(_build_image_slot(
            slide_id, hero_geom, sub, hero_url, hero_prompt, sx=sx, sy=sy,
        ))

    # --- Text block positioning (title + subtitle stack) ---------------
    text_meta = geom.get("text_block") or {}
    text_w = float(text_meta.get("w_in", 7.0))
    text_h = float(text_meta.get("h_in", 3.0))
    title_meta = geom.get("title") or {}
    subtitle_meta = geom.get("subtitle") or {}
    title_h = float(title_meta.get("h_in", 1.5))
    subtitle_h = float(subtitle_meta.get("h_in", 0.8))
    subtitle_offset = float(subtitle_meta.get("top_offset_in", 1.7))

    if side == "fullbleed":
        text_block_left = (_REF_WIDTH_IN - text_w) / 2.0
        text_block_top = (_REF_HEIGHT_IN - text_h) / 2.0
    elif side == "left":
        # Hero on the left → text goes right of it
        text_block_left = hero_w + 0.5
        text_block_top = (_REF_HEIGHT_IN - text_h) / 2.0
    else:  # side == "right"
        # Hero on the right → text goes left of it
        text_block_left = 0.5
        text_block_top = (_REF_HEIGHT_IN - text_h) / 2.0

    # --- Title ----------------------------------------------------------
    # Resolution order: per-slide content > brief.palette.accent > theme default
    title_text = content.get("title")
    if title_text:
        title_color_hex = (
            content.get("title_color_hex")
            or _brief_get(brief, "palette.accent")
        )
        title_geom = {
            "left_in": text_block_left,
            "top_in": text_block_top,
            "w_in": text_w, "h_in": title_h,
            "font_role": title_meta.get("font_role", "display"),
        }
        reqs.extend(_build_text_slot(
            slide_id, title_geom, sub, str(title_text),
            text_color_hex=str(title_color_hex) if title_color_hex else None,
            sx=sx, sy=sy,
        ))

    # --- Subtitle -------------------------------------------------------
    # Resolution order: per-slide > brief.palette.text > theme default
    subtitle_text = content.get("subtitle")
    if subtitle_text:
        subtitle_color_hex = (
            content.get("subtitle_color_hex")
            or _brief_get(brief, "palette.text")
        )
        subtitle_geom = {
            "left_in": text_block_left,
            "top_in": text_block_top + subtitle_offset,
            "w_in": text_w, "h_in": subtitle_h,
            "font_role": subtitle_meta.get("font_role", "body"),
        }
        reqs.extend(_build_text_slot(
            slide_id, subtitle_geom, sub, str(subtitle_text),
            text_color_hex=str(subtitle_color_hex) if subtitle_color_hex else None,
            sx=sx, sy=sy,
        ))

    return reqs


def _build_3col_pill_cards(
    slide_id: str,
    content: dict[str, Any],
    arch: arch_mod.Archetype,
    sub: theme_mod.SubTheme,
    sx: float = 1.0,
    sy: float = 1.0,
    brief: dict[str, Any] | None = None,
) -> list[dict]:
    """Build a 3-column pill-card slide with per-slide visual-story colors.

    Content-driven color resolution (priority high→low):
      1. per-column `col["pill_hex"]` — agent explicitly picks the color
         for one column, e.g. "#DB4437" for an error column.
      2. `content["pill_palette"]` — list[hex], cycled across columns so
         `palette[0]` lands on col1, palette[1] on col2, etc.
      3. archetype `geometry_defaults.pill.color_role` → theme palette
         (fallback; same color for all pills).

    Same priority applies to `content["title_accent_hex"]` for the title bar
    (falls back to first pill color if unset).

    Visual extras emitted by default: title accent rule, rounded pill
    corners, column-matched dot above each pill. No content required — these
    are archetype-level shape additions.
    """
    geom = arch.geometry_defaults
    reqs: list[dict] = []

    pill_meta = geom.get("pill") or {}
    body_meta = geom.get("body") or {}
    pill_font_role = pill_meta.get("font_role") or "pill_header"
    body_font_role = body_meta.get("font_role") or "body"
    theme_default_hex = sub.resolve_color(pill_meta.get("color_role") or "brand_accent") or "#3366CC"

    # Resolution order: per-slide pill_palette > brief.palette.category_set > []
    # (trailing empty list means `_pill_hex` falls through to the theme default)
    palette_source = (
        content.get("pill_palette")
        or _brief_get(brief, "palette.category_set")
        or []
    )
    palette: list[str] = [str(x) for x in palette_source]
    columns = content.get("columns") or []

    def _pill_hex(i: int, col: dict) -> str:
        if col.get("pill_hex"):
            return str(col["pill_hex"])
        if i < len(palette):
            return palette[i]
        return theme_default_hex

    # Reference-frame constants (archetype 16×9 inches). _build_text_slot
    # applies sx/sy on emit — do NOT pre-scale here.
    pill_h = 0.9
    gap = 0.15

    # --- Title + title accent bar ------------------------------------------
    title_text = content.get("title")
    if title_text and "title" in geom:
        reqs.extend(_build_text_slot(slide_id, geom["title"], sub, str(title_text), sx=sx, sy=sy))
        title_accent_meta = geom.get("title_accent") or {}
        if title_accent_meta:
            first_col = columns[0] if columns else {}
            # Resolution order: per-slide > brief.palette.accent > first pill color
            accent_hex = str(
                content.get("title_accent_hex")
                or _brief_get(brief, "palette.accent")
                or _pill_hex(0, first_col)
            )
            t = geom["title"]
            bar_geom = {
                "left_in": float(t.get("left_in", 0.9)),
                "top_in": float(t.get("top_in", 0.6)) + float(title_accent_meta.get("top_offset_in", 1.0)),
                "w_in": float(title_accent_meta.get("w_in", 2.4)),
                "h_in": float(title_accent_meta.get("h_in", 0.08)),
            }
            reqs.extend(
                _build_text_slot(
                    slide_id, bar_geom, sub, "",
                    fill_hex=accent_hex, shape_type="RECTANGLE",
                    sx=sx, sy=sy,
                )
            )

    # --- Lead paragraph ----------------------------------------------------
    if content.get("lead") and "lead" in geom:
        reqs.extend(_build_text_slot(slide_id, geom["lead"], sub, str(content["lead"]), sx=sx, sy=sy))

    # --- Columns (dot|icon + pill + body per column) -----------------------
    dot_meta = geom.get("column_dot") or {}
    dot_r = float(dot_meta.get("r_in", 0.0))  # 0 → skip dot
    icon_names_raw = content.get("icon_names") or []
    icon_names: list[str | None] = [
        str(n) if isinstance(n, str) and n else None
        for n in list(icon_names_raw) + [None, None, None]
    ][:3]

    for i, col in enumerate(columns[:3], start=1):
        col_geom = geom.get(f"column_{i}")
        if not col_geom:
            continue
        col_left = float(col_geom.get("left_in", 0))
        col_top = float(col_geom.get("top_in", 0))
        col_w = float(col_geom.get("w_in", 4.6))
        col_h = float(col_geom.get("h_in", 2.3))
        pill_hex = _pill_hex(i - 1, col)

        # Icon (if specified) OR dot accent. Mutually exclusive — icon
        # replaces the dot as the "above-pill" accent when present.
        icon_name = icon_names[i - 1]
        if icon_name:
            try:
                icon_spec = icons_mod.get_icon_spec(icon_name)
            except KeyError:
                icon_spec = None
            if icon_spec:
                icon_size = 0.6
                icon_left = col_left + (col_w - icon_size) / 2
                icon_top = max(col_top - icon_size - 0.15, 0.0)
                for shape_spec in icon_spec.get("shapes") or []:
                    rel = shape_spec.get("at") or [0.0, 0.0, 1.0, 1.0]
                    rl, rt, rw, rh = (float(x) for x in rel[:4])
                    shape_fill = shape_spec.get("fill_hex") or pill_hex
                    shape_geom = {
                        "left_in": icon_left + rl * icon_size,
                        "top_in": icon_top + rt * icon_size,
                        "w_in": max(rw * icon_size, 0.05),
                        "h_in": max(rh * icon_size, 0.05),
                    }
                    reqs.extend(
                        _build_text_slot(
                            slide_id, shape_geom, sub, "",
                            fill_hex=str(shape_fill),
                            shape_type=str(shape_spec.get("type", "RECTANGLE")),
                            sx=sx, sy=sy,
                        )
                    )
        elif dot_r > 0:
            dot_geom = {
                "left_in": col_left,
                "top_in": max(col_top - dot_r * 2 - 0.1, 0.0),
                "w_in": dot_r * 2,
                "h_in": dot_r * 2,
            }
            reqs.extend(
                _build_text_slot(
                    slide_id, dot_geom, sub, "",
                    fill_hex=pill_hex, shape_type="ELLIPSE",
                    sx=sx, sy=sy,
                )
            )

        pill_text = str(col.get("pill", ""))
        if pill_text:
            pill_geom = {
                "left_in": col_left,
                "top_in": col_top,
                "w_in": col_w,
                "h_in": pill_h,
            }
            reqs.extend(
                _build_text_slot(
                    slide_id,
                    pill_geom,
                    sub,
                    pill_text,
                    fill_hex=pill_hex,
                    text_color_hex="#FFFFFF",
                    font_role_override=pill_font_role,
                    shape_type="ROUND_RECTANGLE",
                    sx=sx,
                    sy=sy,
                )
            )

        body_text = str(col.get("body", ""))
        if body_text:
            body_geom = {
                "left_in": col_left,
                "top_in": col_top + pill_h + gap,
                "w_in": col_w,
                "h_in": max(col_h - pill_h - gap, 1.0),
                "font_role": body_font_role,
            }
            reqs.extend(_build_text_slot(slide_id, body_geom, sub, body_text, sx=sx, sy=sy))

    return reqs


def _extract_image_spec(
    image: Any,
) -> tuple[str | None, str | None]:
    """Pull (url, prompt) from the content's `image` slot.

    Accepts either a dict ({url: "..."} or {prompt: "..."}) — the common
    case for content-driven visual identity — or a bare string which is
    interpreted as a URL (back-compat with the YAML slot hint that calls
    `image` a string).

    Returns (url, prompt). Both can be None (no image requested).
    Only one will be truthy in practice; if both are set, url wins.
    """
    if isinstance(image, dict):
        url = image.get("url")
        prompt = image.get("prompt")
        return (str(url) if url else None, str(prompt) if prompt else None)
    if isinstance(image, str) and image.strip():
        return (image, None)
    return (None, None)


def _build_image_slot(
    slide_id: str,
    geom: dict[str, Any],
    sub: theme_mod.SubTheme,
    image_url: str | None,
    image_prompt: str | None,
    sx: float = 1.0,
    sy: float = 1.0,
) -> list[dict]:
    """Emit the requests for an image slot — URL mode OR placeholder mode.

    Mirrors the `create_image` MCP tool's dual-mode design (Decision P,
    LOG-014). When neither url nor prompt is set, returns [] so callers
    can compose text-only layouts without branching on image presence.
    """
    left_in = float(geom.get("left_in", 0.5))
    top_in = float(geom.get("top_in", 1.6))
    w_in = float(geom.get("w_in", 7.0))
    h_in = float(geom.get("h_in", 6.5))

    if image_url:
        return [{
            "createImage": {
                "objectId": _new_id(prefix="i_"),
                "url": image_url,
                "elementProperties": _element_props(
                    slide_id, left_in, top_in, w_in, h_in, sx=sx, sy=sy,
                ),
            }
        }]
    if image_prompt:
        placeholder_geom = {
            "left_in": left_in, "top_in": top_in,
            "w_in": w_in, "h_in": h_in,
        }
        return _build_text_slot(
            slide_id, placeholder_geom, sub,
            f"[IMAGE: {image_prompt}]",
            shape_type="RECTANGLE",
            sx=sx, sy=sy,
        )
    return []


def _build_text_left_image_right(
    slide_id: str,
    content: dict[str, Any],
    arch: arch_mod.Archetype,
    sub: theme_mod.SubTheme,
    sx: float = 1.0,
    sy: float = 1.0,
    brief: dict[str, Any] | None = None,
) -> list[dict]:
    """Build a title + text-block + image slide — Gamma's dominant archetype.

    Content-driven visual identity (Decision O extension, LOG-014) — every
    visual choice is per-call, theme is fallback only:

      - `image_side`: "right" (default) or "left". Swaps the text/image
        horizontal positions without touching widths.
      - `accent_color_hex`: optional. When set, emits a short colored bar
        under the title (same idiom as 3col_pill_cards title_accent).
      - `body_text_color_hex`: optional. Applied to the body text run.
      - `image`: dual-mode — `{"url": "https://..."}` for a real raster
        (createImage), OR `{"prompt": "..."}` to emit a RECTANGLE placeholder
        with the literal marker `[IMAGE: <prompt>]` (Decision P shapes-first —
        caller fills in the real image later). A bare string is accepted as
        a URL for back-compat with the archetype YAML hint. When neither is
        set, the slide renders text-only on the full-width text block.
      - `image_caption`: optional short caption below the image.

    Body source: `content["body"]` (single paragraph) OR
    `content["paragraphs"]` (list joined with `\\n\\n`). Using both is
    undefined; body wins.
    """
    geom = arch.geometry_defaults
    reqs: list[dict] = []

    image_side_raw = str(content.get("image_side", "right")).lower()
    image_side = "left" if image_side_raw == "left" else "right"

    # --- Title + optional accent bar ----------------------------------
    title_text = content.get("title")
    if title_text and "title" in geom:
        reqs.extend(_build_text_slot(
            slide_id, geom["title"], sub, str(title_text), sx=sx, sy=sy,
        ))
        # Resolution order: per-slide accent_color_hex > brief.palette.accent > skip
        accent_hex = (
            content.get("accent_color_hex")
            or _brief_get(brief, "palette.accent")
        )
        if accent_hex:
            t = geom["title"]
            bar_geom = {
                "left_in": float(t.get("left_in", 0.5)),
                "top_in": float(t.get("top_in", 0.5)) + float(t.get("h_in", 0.8)) + 0.05,
                "w_in": 2.0,
                "h_in": 0.08,
            }
            reqs.extend(_build_text_slot(
                slide_id, bar_geom, sub, "",
                fill_hex=str(accent_hex), shape_type="RECTANGLE",
                sx=sx, sy=sy,
            ))

    # --- Compute text_block + image horizontal positions --------------
    # Archetype YAML ships with text_block on the left (default). For
    # image_side="left" we swap the two slots' left_in values — widths
    # stay per-slot (text 7.5", image 7.0") so the 1-inch mid-gap flips
    # side but layout invariants hold.
    text_geom_base = geom.get("text_block") or {}
    image_geom_base = geom.get("image") or {}
    text_w = float(text_geom_base.get("w_in", 7.5))
    image_w = float(image_geom_base.get("w_in", 7.0))
    text_h = float(text_geom_base.get("h_in", 7.0))
    image_h = float(image_geom_base.get("h_in", 6.5))
    text_top = float(text_geom_base.get("top_in", 1.6))
    image_top = float(image_geom_base.get("top_in", 1.6))

    margin = 0.5  # archetype-reference inches; sx handles deck-fit scaling
    if image_side == "right":
        text_left = margin
        image_left = _REF_WIDTH_IN - margin - image_w
    else:
        image_left = margin
        text_left = _REF_WIDTH_IN - margin - text_w

    # --- Body text (paragraph or joined paragraphs) -------------------
    body_text = content.get("body")
    if not body_text:
        paragraphs = content.get("paragraphs") or []
        body_text = "\n\n".join(str(p) for p in paragraphs) if paragraphs else ""

    if body_text:
        # Resolution order: per-slide body_text_color_hex > brief.palette.text > theme default
        body_color_hex = (
            content.get("body_text_color_hex")
            or _brief_get(brief, "palette.text")
        )
        body_geom = {
            "left_in": text_left,
            "top_in": text_top,
            "w_in": text_w,
            "h_in": text_h,
        }
        reqs.extend(_build_text_slot(
            slide_id, body_geom, sub, str(body_text),
            text_color_hex=str(body_color_hex) if body_color_hex else None,
            sx=sx, sy=sy,
        ))

    # --- Image slot (URL or placeholder) ------------------------------
    image_url, image_prompt = _extract_image_spec(content.get("image"))
    image_slot_geom = {
        "left_in": image_left, "top_in": image_top,
        "w_in": image_w, "h_in": image_h,
    }
    reqs.extend(_build_image_slot(
        slide_id, image_slot_geom, sub, image_url, image_prompt, sx=sx, sy=sy,
    ))

    # --- Caption (only if image slot was rendered) --------------------
    caption = content.get("image_caption")
    if caption and (image_url or image_prompt):
        caption_geom_base = geom.get("image_caption") or {}
        caption_geom = {
            "left_in": image_left,
            "top_in": image_top + image_h + 0.1,
            "w_in": image_w,
            "h_in": float(caption_geom_base.get("h_in", 0.5)),
            "font_role": caption_geom_base.get("font_role", "body_small"),
        }
        reqs.extend(_build_text_slot(
            slide_id, caption_geom, sub, str(caption), sx=sx, sy=sy,
        ))

    return reqs


def _build_4col_numbered_flow(
    slide_id: str,
    content: dict[str, Any],
    arch: arch_mod.Archetype,
    sub: theme_mod.SubTheme,
    sx: float = 1.0,
    sy: float = 1.0,
    brief: dict[str, Any] | None = None,
) -> list[dict]:
    """4-column numbered-flow layout — text-only structural variety.

    Each column emits `num` (large colored label) + `subtitle` (header
    line) + `body` (paragraph). Optional vertical separator lines between
    columns. No image slot (deliberately different shape from
    text_left_image_right — Gamma-style decks mix both for variety).

    Content-driven color identity (LOG-014 Step 7 — mirrors the
    3col_pill_cards contract):

      - per-column `num_color_hex`: highest priority — agent picks the
        color of a specific column (e.g. red for an 'at risk' column).
      - deck-level `content["numbers_palette"]`: list[hex] cycled across
        columns so `palette[0]` lands on col1, palette[1] on col2, etc.
      - theme `brand_accent`: fallback (same color for every column).

    Separators — `content["separators"]` (default True) draws thin
    vertical RECTANGLE dividers between columns. `separator_color_hex`
    overrides the theme-resolved color.

    Column schema: `{num, subtitle|header, body, num_color_hex?}`.
    Only the first 4 columns are rendered; extras dropped.
    """
    geom = arch.geometry_defaults
    reqs: list[dict] = []

    columns = content.get("columns") or []
    # Resolution order: per-slide numbers_palette > brief.palette.category_set > []
    # (falls through to theme default via `_num_hex`)
    palette_source = (
        content.get("numbers_palette")
        or _brief_get(brief, "palette.category_set")
        or []
    )
    palette: list[str] = [str(x) for x in palette_source]
    theme_default_hex = sub.resolve_color("brand_accent") or "#3366CC"

    def _num_hex(i: int, col: dict) -> str:
        if col.get("num_color_hex"):
            return str(col["num_color_hex"])
        if i < len(palette):
            return palette[i]
        return theme_default_hex

    # --- Title ----------------------------------------------------------
    if content.get("title") and "title" in geom:
        reqs.extend(_build_text_slot(
            slide_id, geom["title"], sub, str(content["title"]), sx=sx, sy=sy,
        ))

    col_w = float(geom.get("column_width_in", 3.2))
    col_gap = float(geom.get("column_gap_in", 0.4))
    first_col_left = 0.5

    # Reference-frame layout per column (sx/sy applied in _build_text_slot)
    num_top, num_h = 1.2, 0.8
    subtitle_top, subtitle_h = 2.1, 0.6
    body_top, body_h = 2.9, 5.0

    num_font_role = geom.get("num_font_role", "display")
    subtitle_font_role = geom.get("subtitle_font_role", "pill_header")
    body_font_role = geom.get("body_font_role", "body_small")

    # --- Columns --------------------------------------------------------
    truncated_columns = columns[:4]
    for i, col in enumerate(truncated_columns):
        col_left = first_col_left + i * (col_w + col_gap)
        num_color = _num_hex(i, col)

        num_text = str(col.get("num", f"{i + 1:02d}"))
        num_geom = {
            "left_in": col_left, "top_in": num_top,
            "w_in": col_w, "h_in": num_h,
            "font_role": num_font_role,
        }
        reqs.extend(_build_text_slot(
            slide_id, num_geom, sub, num_text,
            text_color_hex=num_color,
            sx=sx, sy=sy,
        ))

        sub_text = str(col.get("subtitle") or col.get("header") or "")
        if sub_text:
            sub_geom = {
                "left_in": col_left, "top_in": subtitle_top,
                "w_in": col_w, "h_in": subtitle_h,
                "font_role": subtitle_font_role,
            }
            reqs.extend(_build_text_slot(slide_id, sub_geom, sub, sub_text, sx=sx, sy=sy))

        body_text = str(col.get("body", ""))
        if body_text:
            body_geom = {
                "left_in": col_left, "top_in": body_top,
                "w_in": col_w, "h_in": body_h,
                "font_role": body_font_role,
            }
            reqs.extend(_build_text_slot(slide_id, body_geom, sub, body_text, sx=sx, sy=sy))

    # --- Separators -----------------------------------------------------
    separators = content.get("separators", True)
    if separators and len(truncated_columns) >= 2:
        # Resolution order: per-slide separator_color_hex > brief.palette.accent > theme text_body > #666666
        sep_hex = str(
            content.get("separator_color_hex")
            or _brief_get(brief, "palette.accent")
            or sub.resolve_color("text_body")
            or "#666666"
        )
        sep_top = num_top - 0.1
        sep_bottom = body_top + body_h
        sep_h = sep_bottom - sep_top
        sep_w = 0.02  # thin vertical rectangle = separator line
        for i in range(len(truncated_columns) - 1):
            sep_left = (
                first_col_left + i * (col_w + col_gap) + col_w + col_gap / 2
            )
            sep_geom = {
                "left_in": sep_left, "top_in": sep_top,
                "w_in": sep_w, "h_in": sep_h,
            }
            reqs.extend(_build_text_slot(
                slide_id, sep_geom, sub, "",
                fill_hex=sep_hex, shape_type="RECTANGLE",
                sx=sx, sy=sy,
            ))

    return reqs


_Builder = Callable[
    ...,
    list[dict],
]
"""Builder signature: (slide_id, content, arch, sub, sx=1.0, sy=1.0,
brief=None) -> list[dict]. Using `...` (Callable variadic) so the dispatch
table accepts all 5 builders which have identical public signatures modulo
default values, without mypy flagging the brief kwarg."""

_BUILDERS: dict[str, _Builder] = {
    "text_heavy_body": _build_text_heavy_body,
    "cover_with_hero": _build_cover_with_hero,
    "3col_pill_cards": _build_3col_pill_cards,
    "text_left_image_right": _build_text_left_image_right,
    "4col_numbered_flow": _build_4col_numbered_flow,
}


def supported_archetypes() -> list[str]:
    return sorted(_BUILDERS)


def validate_content(content: dict[str, Any], arch: arch_mod.Archetype) -> list[str]:
    warnings: list[str] = []
    for slot in arch.required_slots:
        v = content.get(slot)
        if v in (None, "", [], {}):
            warnings.append(f"missing required slot '{slot}' for archetype '{arch.name}'")
    return warnings


def build_slide_requests(
    slide_id: str,
    archetype_name: str,
    content: dict[str, Any],
    sub: theme_mod.SubTheme,
    deck_width_in: float = _REF_WIDTH_IN,
    deck_height_in: float = _REF_HEIGHT_IN,
    brief: dict[str, Any] | None = None,
) -> tuple[list[dict], list[str]]:
    """Build the Request list for populating a newly-created slide.

    Does NOT include the `createSlide` request — caller fires that first with the
    chosen `slide_id`, then passes these content requests in the same or a
    subsequent batchUpdate.

    deck_width_in / deck_height_in: actual page size of the target deck in
    inches. Archetype YAMLs are authored against a 16×9 reference; the builder
    scales geometry so content fits decks of other sizes (Google's default
    10×5.625, 13.33×7.5 widescreen, 10×7.5 standard 4:3, etc.). Defaults match
    the reference — omitting both params gives backward-compatible behavior.

    brief: optional theme brief (Decision R, Phase 2). When provided, each
    builder uses it as a fallback for unspecified content fields — the
    cross-slide visual DNA carried in the deck's hidden meta-slide. Resolution
    order in every builder:

        per_slide_content > brief.palette.* > theme YAML > safety default

    Passing `None` (the default) preserves pre-Phase-2 behavior — builders
    resolve only from per-slide content + theme. Server callers that want
    brief fallback should fetch it via `theme_brief.find_meta_slide` +
    `parse_brief_body` and pass the parsed dict here.

    Returns (requests, warnings). Warnings are non-fatal (missing optional slot,
    unsupported archetype fallthrough).
    """
    arch = arch_mod.get(archetype_name)
    warnings = validate_content(content, arch)
    builder = _BUILDERS.get(archetype_name)
    if builder is None:
        warnings.append(
            f"archetype '{archetype_name}' has no create_slide builder yet — "
            f"supported: {supported_archetypes()}. The slide will be created "
            f"blank; use create_shape or exec_batch_update to populate."
        )
        return [], warnings
    sx = deck_width_in / _REF_WIDTH_IN
    sy = deck_height_in / _REF_HEIGHT_IN
    # Apply brief.font_family overlay to sub BEFORE dispatch — every builder
    # then inherits brief-correct fonts without per-builder wiring. Resolution
    # order preserved: per_slide_content > brief.palette.* > (brief-overlaid)
    # theme YAML font family > safety default.
    sub_effective = _apply_brief_fonts_to_sub(sub, brief)
    return builder(slide_id, content, arch, sub_effective, sx, sy, brief=brief), warnings
