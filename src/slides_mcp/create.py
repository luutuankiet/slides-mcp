"""Build batchUpdate Requests for a new slide from archetype + semantic content.

The companion of `projection.py`: projection reads (shapes -> YAML); this module
writes (YAML-ish dict -> Slides API Requests). Per-archetype builders map
each slot in `content` to one or more Request dicts using
`archetype.geometry_defaults` for positioning and the active `SubTheme`
for fonts + palette.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from . import archetypes as arch_mod
from . import theme as theme_mod

_EMU_PER_INCH = 914400


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


def _element_props(slide_id: str, left_in: float, top_in: float, w_in: float, h_in: float) -> dict:
    return {
        "pageObjectId": slide_id,
        "size": {
            "width": {"magnitude": _inch_to_emu(w_in), "unit": "EMU"},
            "height": {"magnitude": _inch_to_emu(h_in), "unit": "EMU"},
        },
        "transform": {
            "scaleX": 1,
            "scaleY": 1,
            "translateX": _inch_to_emu(left_in),
            "translateY": _inch_to_emu(top_in),
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
) -> list[dict]:
    """Compose createShape + optional fill + insertText + optional text styling for one slot."""
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
                ),
            }
        }
    ]
    if fill_hex:
        reqs.append(
            {
                "updateShapeProperties": {
                    "objectId": new_id,
                    "fields": "shapeBackgroundFill.solidFill.color",
                    "shapeProperties": {
                        "shapeBackgroundFill": {
                            "solidFill": {"color": {"rgbColor": _hex_to_rgb_fracs(fill_hex)}}
                        }
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
) -> list[dict]:
    geom = arch.geometry_defaults
    reqs: list[dict] = []
    if content.get("title") and "title" in geom:
        reqs.extend(_build_text_slot(slide_id, geom["title"], sub, str(content["title"])))
    paragraphs = content.get("paragraphs") or []
    body_text = "\n\n".join(str(p) for p in paragraphs)
    if body_text and "body" in geom:
        reqs.extend(_build_text_slot(slide_id, geom["body"], sub, body_text))
    return reqs


def _build_cover_with_hero(
    slide_id: str,
    content: dict[str, Any],
    arch: arch_mod.Archetype,
    sub: theme_mod.SubTheme,
) -> list[dict]:
    geom = arch.geometry_defaults
    reqs: list[dict] = []
    if content.get("title") and "title" in geom:
        reqs.extend(_build_text_slot(slide_id, geom["title"], sub, str(content["title"])))
    if content.get("subtitle") and "subtitle" in geom:
        reqs.extend(_build_text_slot(slide_id, geom["subtitle"], sub, str(content["subtitle"])))
    # hero image slot is not implemented in MVP — inserting images needs createImage + URL.
    return reqs


def _build_3col_pill_cards(
    slide_id: str,
    content: dict[str, Any],
    arch: arch_mod.Archetype,
    sub: theme_mod.SubTheme,
) -> list[dict]:
    geom = arch.geometry_defaults
    reqs: list[dict] = []
    if content.get("title") and "title" in geom:
        reqs.extend(_build_text_slot(slide_id, geom["title"], sub, str(content["title"])))
    if content.get("lead") and "lead" in geom:
        reqs.extend(_build_text_slot(slide_id, geom["lead"], sub, str(content["lead"])))

    pill_meta = geom.get("pill") or {}
    body_meta = geom.get("body") or {}
    pill_color_role = pill_meta.get("color_role") or "brand_accent"
    pill_hex = sub.resolve_color(pill_color_role)
    pill_font_role = pill_meta.get("font_role") or "pill_header"
    body_font_role = body_meta.get("font_role") or "body"

    pill_h = 0.6
    gap = 0.1

    columns = content.get("columns") or []
    for i, col in enumerate(columns[:3], start=1):
        col_geom = geom.get(f"column_{i}")
        if not col_geom:
            continue
        col_left = float(col_geom.get("left_in", 0))
        col_top = float(col_geom.get("top_in", 0))
        col_w = float(col_geom.get("w_in", 4.6))
        col_h = float(col_geom.get("h_in", 2.3))

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
                    shape_type="RECTANGLE",
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
            reqs.extend(_build_text_slot(slide_id, body_geom, sub, body_text))

    return reqs


_Builder = Callable[[str, dict[str, Any], arch_mod.Archetype, theme_mod.SubTheme], list[dict]]

_BUILDERS: dict[str, _Builder] = {
    "text_heavy_body": _build_text_heavy_body,
    "cover_with_hero": _build_cover_with_hero,
    "3col_pill_cards": _build_3col_pill_cards,
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
) -> tuple[list[dict], list[str]]:
    """Build the Request list for populating a newly-created slide.

    Does NOT include the `createSlide` request — caller fires that first with the
    chosen `slide_id`, then passes these content requests in the same or a
    subsequent batchUpdate.

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
    return builder(slide_id, content, arch, sub), warnings
