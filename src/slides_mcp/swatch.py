"""PIL-composed tone-card swatches for theme briefs.

Zero Google Slides API calls. Zero external fetches. Pure in-memory PNG
composition from a brief dict. The swatch IS the fast-switch approval
primitive: human sees N candidate tones as one composite PNG, picks one,
commits. No write-and-render-and-delete cycle.

The tone card surfaces every visually-expressive field of a brief:
  - palette.surface                  -> card background fill
  - palette.accent                   -> heading color + accent bar + chevron
  - palette.text                     -> body text color
  - palette.category_set             -> pill row (left-to-right)
  - shape_language (sharp|rounded)   -> pill corner radius + chevron shape
  - numbering_style                  -> numbered chip style (1 2 3)
  - font_family.heading/body (opt)   -> TTF load attempt, else annotated label
  - tone + image_prompt_style        -> caption lines

Principles:
  - Pure function: brief dict in, PNG bytes out. No side effects.
  - Fail-open on fonts: swatch renders with fallback if custom TTF missing.
  - Backward-compat: briefs without font_family / category_set work fine.
  - Renderer-not-brand: swatch renders WHATEVER brief passed.
"""
from __future__ import annotations

import io
import logging
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

# Geometry constants
SWATCH_W = 800
SWATCH_H = 450
ACCENT_BAR_H = 10
PADDING = 36
PILL_W = 140
PILL_H = 38
PILL_GAP = 12
GRID_GAP = 24
GRID_COLS = 3

FONT_SIZE_HEADING = 44
FONT_SIZE_TONE = 20
FONT_SIZE_LABEL = 14
FONT_SIZE_PILL = 15
FONT_SIZE_BODY = 16
FONT_SIZE_CAPTION = 13

_FALLBACK_SANS = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
_FALLBACK_SANS_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
_FALLBACK_SERIF = "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf"
_FALLBACK_SERIF_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf"

_SERIF_HINTS = (
    "serif", "fraunces", "playfair", "merriweather", "bookman", "garamond",
    "times", "georgia", "dm serif", "libre caslon", "lora",
)


def _hex_to_rgb(hex_value: str) -> tuple[int, int, int]:
    """Convert "#RRGGBB" or "#RGB" to (r, g, b). Raises ValueError on bad input."""
    h = (hex_value or "").lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        raise ValueError(f"expected #RRGGBB hex, got {hex_value!r}")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _rgb_luminance(rgb: tuple[int, int, int]) -> float:
    """Approximate relative luminance per ITU-R BT.709. Range 0..1."""
    r, g, b = (c / 255.0 for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _readable_on(bg_rgb: tuple[int, int, int]) -> tuple[int, int, int]:
    """Pick black or white text to sit on bg_rgb."""
    return (0, 0, 0) if _rgb_luminance(bg_rgb) > 0.55 else (255, 255, 255)


def _looks_serif(family: str | None) -> bool:
    if not family:
        return False
    low = family.lower()
    return any(h in low for h in _SERIF_HINTS)


def _load_font(family: str | None, size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    """Attempt to load the requested family; fall back to DejaVu by shape hint."""
    if family:
        try:
            return ImageFont.truetype(family, size)
        except OSError:
            pass
        slug = family.lower().replace(" ", "-")
        for candidate in (
            f"/usr/share/fonts/truetype/{slug}/{family.replace(' ', '')}-{'Bold' if bold else 'Regular'}.ttf",
            f"/usr/share/fonts/truetype/{slug}/{family}-{'Bold' if bold else 'Regular'}.ttf",
        ):
            if Path(candidate).exists():
                try:
                    return ImageFont.truetype(candidate, size)
                except OSError:
                    pass
    is_serif = _looks_serif(family)
    path = (
        (_FALLBACK_SERIF_BOLD if bold else _FALLBACK_SERIF)
        if is_serif
        else (_FALLBACK_SANS_BOLD if bold else _FALLBACK_SANS)
    )
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        try:
            return ImageFont.load_default(size=size)
        except TypeError:
            return ImageFont.load_default()


def _text_wh(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> tuple[int, int]:
    if hasattr(draw, "textbbox"):
        left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
        return right - left, bottom - top
    return (len(text) * 8, 12)


def _rounded(draw: ImageDraw.ImageDraw, xy, radius: int, fill=None, outline=None):
    try:
        draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline)
    except AttributeError:
        draw.rectangle(xy, fill=fill, outline=outline)


def render_swatch(brief: dict) -> bytes:
    """Render one brief as a tone-card PNG. Returns PNG bytes."""
    img = _compose_single(brief, SWATCH_W, SWATCH_H)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def render_swatch_grid(briefs) -> bytes:
    """Render up to N briefs in a grid, return PNG bytes of the composite."""
    briefs = list(briefs)
    if not briefs:
        raise ValueError("render_swatch_grid requires at least 1 brief")
    n = len(briefs)
    cols = 1 if n == 1 else (2 if n == 2 else GRID_COLS)
    rows = math.ceil(n / cols)
    scale = min(1.0, (1600 - (cols + 1) * GRID_GAP) / (cols * SWATCH_W))
    tile_w = int(SWATCH_W * scale)
    tile_h = int(SWATCH_H * scale)
    label_h = 28
    canvas_w = cols * tile_w + (cols + 1) * GRID_GAP
    canvas_h = rows * (tile_h + label_h + GRID_GAP) + GRID_GAP
    canvas = Image.new("RGB", (canvas_w, canvas_h), (245, 246, 248))
    draw = ImageDraw.Draw(canvas)
    label_font = _load_font(None, 16, bold=True)
    for i, brief in enumerate(briefs):
        col = i % cols
        row = i // cols
        x0 = GRID_GAP + col * (tile_w + GRID_GAP)
        y0 = GRID_GAP + row * (tile_h + label_h + GRID_GAP)
        tone = (brief.get("tone") or "").strip()
        label = f"Variant {i + 1}" + (f" - {tone}" if tone else "")
        draw.text((x0 + 4, y0), label, fill=(60, 64, 72), font=label_font)
        tile_img = _compose_single(brief, tile_w, tile_h)
        canvas.paste(tile_img, (x0, y0 + label_h))
    buf = io.BytesIO()
    canvas.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _compose_single(brief: dict, w: int, h: int) -> Image.Image:
    """Compose a single tone card at (w, h). Returns PIL Image."""
    palette = brief.get("palette") or {}
    surface_hex = palette.get("surface") or "#111335"
    accent_hex = palette.get("accent") or "#E8612E"
    text_hex = palette.get("text") or "#1A1A1A"
    category_set = palette.get("category_set") or [accent_hex]
    try:
        surface_rgb = _hex_to_rgb(surface_hex)
        accent_rgb = _hex_to_rgb(accent_hex)
        text_rgb = _hex_to_rgb(text_hex)
    except ValueError as e:
        raise ValueError(f"bad palette color: {e}") from e
    shape_lang = (brief.get("shape_language") or "sharp").lower()
    numbering = (brief.get("numbering_style") or "bold").lower()
    tone = (brief.get("tone") or "").strip()
    image_style = (brief.get("image_prompt_style") or "").strip()
    font_family = brief.get("font_family") or {}
    heading_family = font_family.get("heading") if isinstance(font_family, dict) else None
    body_family = font_family.get("body") if isinstance(font_family, dict) else None
    scale = w / SWATCH_W

    def px(v: float) -> int:
        return max(1, int(round(v * scale)))
    img = Image.new("RGB", (w, h), surface_rgb)
    draw = ImageDraw.Draw(img)
    bar_h = px(ACCENT_BAR_H)
    draw.rectangle([(0, 0), (w, bar_h)], fill=accent_rgb)
    on_surface_rgb = text_rgb
    if abs(_rgb_luminance(surface_rgb) - _rgb_luminance(text_rgb)) < 0.25:
        on_surface_rgb = _readable_on(surface_rgb)
    pad = px(PADDING)
    cursor_y = bar_h + pad
    heading_font = _load_font(heading_family, px(FONT_SIZE_HEADING), bold=True)
    heading_text = "Aa - Presentation tone"
    draw.text((pad, cursor_y), heading_text, fill=accent_rgb, font=heading_font)
    _, hh = _text_wh(draw, heading_text, heading_font)
    cursor_y += hh + px(6)
    caption_font = _load_font(None, px(FONT_SIZE_CAPTION), bold=False)
    family_label_parts = []
    if heading_family:
        family_label_parts.append(f"heading - {heading_family}")
    if body_family and body_family != heading_family:
        family_label_parts.append(f"body - {body_family}")
    if family_label_parts:
        family_label = "   .   ".join(family_label_parts).upper()
        draw.text((pad, cursor_y), family_label, fill=on_surface_rgb, font=caption_font)
        _, cwh = _text_wh(draw, family_label, caption_font)
        cursor_y += cwh + px(10)
    if tone:
        tone_font = _load_font(body_family, px(FONT_SIZE_TONE), bold=False)
        draw.text((pad, cursor_y), tone, fill=on_surface_rgb, font=tone_font)
        _, tw_h = _text_wh(draw, tone, tone_font)
        cursor_y += tw_h + px(14)
    pills = list(category_set)[:5]
    if pills:
        pill_w = px(PILL_W)
        pill_h = px(PILL_H)
        pill_gap = px(PILL_GAP)
        if shape_lang == "rounded":
            pill_radius = pill_h // 2
        elif shape_lang == "mixed":
            pill_radius = pill_h // 5
        else:
            pill_radius = 2
        pill_font = _load_font(body_family, px(FONT_SIZE_PILL), bold=True)
        for i, hx in enumerate(pills):
            try:
                fill_rgb = _hex_to_rgb(hx)
            except ValueError:
                continue
            x0 = pad + i * (pill_w + pill_gap)
            y0 = cursor_y
            x1 = x0 + pill_w
            y1 = y0 + pill_h
            if x1 > w - pad:
                break
            _rounded(draw, [(x0, y0), (x1, y1)], radius=pill_radius, fill=fill_rgb)
            label_text = hx.upper()
            lw, lh = _text_wh(draw, label_text, pill_font)
            tx = x0 + (pill_w - lw) // 2
            ty = y0 + (pill_h - lh) // 2
            draw.text((tx, ty), label_text, fill=_readable_on(fill_rgb), font=pill_font)
        cursor_y += pill_h + px(16)
    meta_font = _load_font(body_family, px(FONT_SIZE_LABEL), bold=True)
    chip_text = f"shape - {shape_lang}    numbering - {numbering}"
    draw.text((pad, cursor_y), chip_text.upper(), fill=on_surface_rgb, font=meta_font)
    _, chip_h = _text_wh(draw, chip_text, meta_font)
    cursor_y += chip_h + px(10)
    chip_diam = px(32)
    chip_gap = px(8)
    for i in range(3):
        cx0 = pad + i * (chip_diam + chip_gap)
        cy0 = cursor_y
        cx1 = cx0 + chip_diam
        cy1 = cy0 + chip_diam
        if numbering == "hidden":
            continue
        if numbering == "outlined":
            draw.ellipse([(cx0, cy0), (cx1, cy1)], outline=accent_rgb, width=px(2))
            num_fill = accent_rgb
        elif numbering == "dot":
            draw.ellipse([(cx0, cy0), (cx1, cy1)], fill=accent_rgb)
            num_fill = _readable_on(accent_rgb)
        else:
            draw.ellipse([(cx0, cy0), (cx1, cy1)], fill=accent_rgb)
            num_fill = _readable_on(accent_rgb)
        num_font = _load_font(heading_family, chip_diam - px(12), bold=True)
        num_str = str(i + 1)
        nw, nh = _text_wh(draw, num_str, num_font)
        draw.text(
            (cx0 + (chip_diam - nw) // 2, cy0 + (chip_diam - nh) // 2 - px(2)),
            num_str,
            fill=num_fill,
            font=num_font,
        )
    cursor_y += chip_diam + px(14)
    body_font = _load_font(body_family, px(FONT_SIZE_BODY), bold=False)
    body_sample = "The quick brown fox jumps over the lazy dog, 1234567890."
    bw, bh = _text_wh(draw, body_sample, body_font)
    while bw > w - 2 * pad and len(body_sample) > 10:
        body_sample = body_sample[:-4] + "..."
        bw, bh = _text_wh(draw, body_sample, body_font)
    draw.text((pad, cursor_y), body_sample, fill=on_surface_rgb, font=body_font)
    cursor_y += bh + px(6)
    if image_style:
        capt_font = _load_font(body_family, px(FONT_SIZE_CAPTION), bold=False)
        capt = f"image style - {image_style}"
        cw, ch = _text_wh(draw, capt, capt_font)
        while cw > w - 2 * pad and len(capt) > 20:
            capt = capt[:-4] + "..."
            cw, ch = _text_wh(draw, capt, capt_font)
        draw.text((pad, cursor_y), capt, fill=on_surface_rgb, font=capt_font)
    chev_w = px(36)
    chev_h = px(24)
    chx1 = w - pad
    chy1 = h - pad
    chx0 = chx1 - chev_w
    chy0 = chy1 - chev_h
    if shape_lang == "rounded":
        _rounded(draw, [(chx0, chy0), (chx1, chy1)], radius=chev_h // 2, fill=accent_rgb)
    elif shape_lang == "mixed":
        _rounded(draw, [(chx0, chy0), (chx1, chy1)], radius=px(6), fill=accent_rgb)
    else:
        draw.polygon(
            [(chx0, chy1), (chx0 + chev_w // 2, chy0), (chx1, chy1)],
            fill=accent_rgb,
        )
    return img


# ---------------------------------------------------------------------------
# Contact sheet — grid of rendered deck thumbnails (Scope C)
# ---------------------------------------------------------------------------

CONTACT_THUMB_W = 400
CONTACT_THUMB_H = 225
CONTACT_GRID_COLS = 4
CONTACT_GAP = 16
CONTACT_LABEL_H = 20


def render_contact_sheet(
    thumbnails: list[tuple[str, bytes]],
    title: str | None = None,
) -> bytes:
    """Compose a contact-sheet PNG from a list of (slide_id, png_bytes) tuples.

    Each thumbnail is downscaled to CONTACT_THUMB_W × CONTACT_THUMB_H; titles
    are slide_ids rendered below each tile. Grid is CONTACT_GRID_COLS wide,
    rows auto-expand.

    Pure PIL — no Slides API calls. Caller (server.render_deck_contact_sheet)
    is responsible for fetching the thumbnail bytes via Slides API.

    title: optional header text rendered above the grid.

    Raises ValueError on empty input.
    """
    if not thumbnails:
        raise ValueError("render_contact_sheet requires at least 1 thumbnail")

    n = len(thumbnails)
    cols = min(CONTACT_GRID_COLS, n)
    rows = math.ceil(n / cols)

    label_font = _load_font(None, 13, bold=False)
    title_font = _load_font(None, 20, bold=True)

    title_h = 40 if title else 0
    cell_w = CONTACT_THUMB_W
    cell_h = CONTACT_THUMB_H + CONTACT_LABEL_H + 4
    canvas_w = cols * cell_w + (cols + 1) * CONTACT_GAP
    canvas_h = title_h + rows * (cell_h + CONTACT_GAP) + CONTACT_GAP
    canvas = Image.new("RGB", (canvas_w, canvas_h), (250, 250, 252))
    draw = ImageDraw.Draw(canvas)

    if title:
        draw.text((CONTACT_GAP, 10), title, fill=(30, 30, 40), font=title_font)

    for i, (slide_id, png) in enumerate(thumbnails):
        col = i % cols
        row = i // cols
        x0 = CONTACT_GAP + col * (cell_w + CONTACT_GAP)
        y0 = title_h + CONTACT_GAP + row * (cell_h + CONTACT_GAP)
        # Render thumbnail
        try:
            thumb = Image.open(io.BytesIO(png))
            thumb = thumb.convert("RGB").resize(
                (CONTACT_THUMB_W, CONTACT_THUMB_H), Image.LANCZOS
            )
            canvas.paste(thumb, (x0, y0))
        except OSError as e:
            # fallback: draw empty tile with error label
            draw.rectangle(
                [(x0, y0), (x0 + CONTACT_THUMB_W, y0 + CONTACT_THUMB_H)],
                fill=(220, 220, 224),
                outline=(180, 180, 184),
            )
            err_text = "(render failed)"
            draw.text(
                (x0 + 12, y0 + CONTACT_THUMB_H // 2),
                err_text, fill=(120, 120, 128), font=label_font,
            )
            logger.warning("contact sheet thumb decode failed for %s: %s", slide_id, e)

        # Label
        label = slide_id[:40]
        lw, lh = _text_wh(draw, label, label_font)
        label_x = x0 + (CONTACT_THUMB_W - lw) // 2
        draw.text((label_x, y0 + CONTACT_THUMB_H + 2), label, fill=(60, 64, 72), font=label_font)

    buf = io.BytesIO()
    canvas.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Archetype preview — PIL dry-run of a builder's output (Scope E)
# ---------------------------------------------------------------------------

# Canvas at 16x9 reference ratio; 1200x675 keeps PNG under ~50KB at this
# complexity while remaining legible.
PREVIEW_W = 1200
PREVIEW_H = 675


def render_archetype_preview(
    archetype: str,
    content: dict,
    brief: dict | None = None,
) -> bytes:
    """Render an archetype + content + brief as a PNG preview. No Slides API.

    The preview is a FAITHFUL SKETCH, not pixel-perfect to what Slides would
    render. It captures: palette + shape language + fonts + layout topology.
    Use it to compare N archetypes for the same content before committing
    one via create_slide.

    Supported archetypes:
      - cover_with_hero
      - text_left_image_right
      - 3col_pill_cards
      - 4col_numbered_flow
      - text_heavy_body

    Unknown archetype renders a fallback "title + body" box.

    Returns PNG bytes ready for MCP ImageContent.
    """
    img = _compose_preview(archetype, content, brief or {}, PREVIEW_W, PREVIEW_H)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _preview_palette(brief: dict) -> dict[str, tuple[int, int, int]]:
    """Extract RGB tuples from brief with safe defaults."""
    p = brief.get("palette") or {}
    out: dict[str, tuple[int, int, int]] = {}
    out["surface"] = _hex_to_rgb(p.get("surface") or "#FFFFFF")
    out["accent"] = _hex_to_rgb(p.get("accent") or "#E8612E")
    out["text"] = _hex_to_rgb(p.get("text") or "#1A1A1A")
    cs = p.get("category_set") or [p.get("accent") or "#E8612E"]
    out_list = []
    for h in cs[:5]:
        try:
            out_list.append(_hex_to_rgb(h))
        except ValueError:
            continue
    out["category_set"] = out_list or [out["accent"]]
    return out


def _preview_fonts(brief: dict) -> tuple[str | None, str | None]:
    ff = brief.get("font_family") or {}
    if not isinstance(ff, dict):
        return None, None
    return (
        ff.get("heading") if isinstance(ff.get("heading"), str) else None,
        ff.get("body") if isinstance(ff.get("body"), str) else None,
    )


def _compose_preview(archetype: str, content: dict, brief: dict, w: int, h: int) -> Image.Image:
    palette = _preview_palette(brief)
    heading_family, body_family = _preview_fonts(brief)
    shape_lang = (brief.get("shape_language") or "sharp").lower()

    img = Image.new("RGB", (w, h), palette["surface"])
    draw = ImageDraw.Draw(img)

    # 6px accent bar at top as visual signature
    draw.rectangle([(0, 0), (w, 6)], fill=palette["accent"])

    # Badge in top-right marking this as a preview
    badge_font = _load_font(None, 11, bold=True)
    badge_text = "PREVIEW · NOT WRITTEN TO DECK"
    bw, bh = _text_wh(draw, badge_text, badge_font)
    bx, by = w - bw - 16, 14
    draw.rectangle([(bx - 6, by - 2), (bx + bw + 6, by + bh + 2)], fill=(230, 230, 235))
    draw.text((bx, by), badge_text, fill=(80, 80, 90), font=badge_font)

    # Dispatch
    dispatch = {
        "cover_with_hero": _preview_cover,
        "text_left_image_right": _preview_tlir,
        "3col_pill_cards": _preview_3col,
        "4col_numbered_flow": _preview_4col,
        "text_heavy_body": _preview_textheavy,
    }
    fn = dispatch.get(archetype)
    if fn:
        fn(draw, content, palette, heading_family, body_family, shape_lang, w, h)
    else:
        # Generic fallback
        title_font = _load_font(heading_family, 42, bold=True)
        draw.text((60, 100), str(content.get("title") or archetype), fill=palette["accent"], font=title_font)
        note_font = _load_font(body_family, 16, bold=False)
        draw.text(
            (60, 160),
            f"(no preview renderer for '{archetype}' — fallback sketch)",
            fill=palette["text"],
            font=note_font,
        )

    return img


def _preview_cover(draw, content, palette, heading_family, body_family, shape_lang, w, h):
    title = str(content.get("title") or "Title")
    subtitle = str(content.get("subtitle") or "")
    title_font = _load_font(heading_family, 62, bold=True)
    sub_font = _load_font(body_family, 22, bold=False)
    # Title block
    draw.text((80, h // 2 - 80), title, fill=palette["accent"], font=title_font)
    if subtitle:
        draw.text((80, h // 2 + 10), subtitle, fill=palette["text"], font=sub_font)
    # Hero placeholder on right (gradient stripe approximation)
    hero_x = int(w * 0.6)
    draw.rectangle([(hero_x, 0), (w, h)], fill=palette["category_set"][0])
    draw.rectangle([(hero_x, h - 80), (w, h)], fill=palette["accent"])


def _preview_tlir(draw, content, palette, heading_family, body_family, shape_lang, w, h):
    title = str(content.get("title") or "Title")
    body = content.get("body") or "\n".join(content.get("paragraphs") or ["body text"])
    body = str(body)
    title_font = _load_font(heading_family, 40, bold=True)
    body_font = _load_font(body_family, 16, bold=False)
    # Left column (text)
    mid_x = int(w * 0.55)
    draw.text((60, 90), title, fill=palette["accent"], font=title_font)
    # Body lines wrapped crudely
    max_chars = 60
    y = 170
    for line in str(body).split("\n"):
        while line:
            chunk = line[:max_chars]
            line = line[max_chars:]
            draw.text((60, y), chunk, fill=palette["text"], font=body_font)
            y += 24
            if y > h - 80:
                return
    # Right column (image placeholder)
    _rounded(
        draw, [(mid_x + 20, 90), (w - 60, h - 60)],
        radius=12 if shape_lang == "rounded" else 4,
        fill=palette["category_set"][0 % len(palette["category_set"])],
    )
    label_font = _load_font(None, 14, bold=True)
    draw.text((mid_x + 40, h - 110), "IMAGE", fill=_readable_on(palette["category_set"][0]), font=label_font)


def _preview_3col(draw, content, palette, heading_family, body_family, shape_lang, w, h):
    title = str(content.get("title") or "Three columns")
    lead = str(content.get("lead") or "")
    cols = content.get("columns") or [{"pill": f"Col {i+1}", "body": "body"} for i in range(3)]
    cols = cols[:3]
    title_font = _load_font(heading_family, 38, bold=True)
    draw.text((60, 60), title, fill=palette["accent"], font=title_font)
    if lead:
        lead_font = _load_font(body_family, 16, bold=False)
        draw.text((60, 115), lead, fill=palette["text"], font=lead_font)
    col_w = (w - 60 * 2 - 30 * 2) // 3
    col_h = h - 250
    col_y = 180
    pill_font = _load_font(body_family, 16, bold=True)
    body_font = _load_font(body_family, 14, bold=False)
    pill_radius = 20 if shape_lang == "rounded" else (8 if shape_lang == "mixed" else 2)
    for i, c in enumerate(cols):
        x = 60 + i * (col_w + 30)
        # pill header
        pill_color = palette["category_set"][i % len(palette["category_set"])]
        pill_text = str(c.get("pill") or f"Col {i+1}")
        _rounded(draw, [(x, col_y), (x + col_w, col_y + 44)], radius=pill_radius, fill=pill_color)
        pw, ph = _text_wh(draw, pill_text, pill_font)
        draw.text(
            (x + (col_w - pw) // 2, col_y + (44 - ph) // 2),
            pill_text,
            fill=_readable_on(pill_color),
            font=pill_font,
        )
        # body
        body_text = str(c.get("body") or "")
        y = col_y + 60
        for line in body_text.split("\n"):
            while line:
                chunk = line[:40]
                line = line[40:]
                draw.text((x + 8, y), chunk, fill=palette["text"], font=body_font)
                y += 20
                if y > col_y + col_h - 20:
                    break
            if y > col_y + col_h - 20:
                break


def _preview_4col(draw, content, palette, heading_family, body_family, shape_lang, w, h):
    title = str(content.get("title") or "Four-step flow")
    cols = content.get("columns") or [{"num": str(i+1), "subtitle": f"Step {i+1}", "body": "body"} for i in range(4)]
    cols = cols[:4]
    title_font = _load_font(heading_family, 38, bold=True)
    draw.text((60, 60), title, fill=palette["accent"], font=title_font)
    col_w = (w - 60 * 2 - 20 * 3) // 4
    col_y = 160
    num_font = _load_font(heading_family, 54, bold=True)
    sub_font = _load_font(body_family, 16, bold=True)
    body_font = _load_font(body_family, 13, bold=False)
    for i, c in enumerate(cols):
        x = 60 + i * (col_w + 20)
        num = str(c.get("num") or (i + 1))
        num_color = palette["category_set"][i % len(palette["category_set"])]
        draw.text((x, col_y), num, fill=num_color, font=num_font)
        subtitle = str(c.get("subtitle") or "")
        draw.text((x, col_y + 80), subtitle, fill=palette["text"], font=sub_font)
        body = str(c.get("body") or "")
        y = col_y + 110
        for line in body.split("\n"):
            while line:
                chunk = line[:30]
                line = line[30:]
                draw.text((x, y), chunk, fill=palette["text"], font=body_font)
                y += 18
                if y > h - 80:
                    break


def _preview_textheavy(draw, content, palette, heading_family, body_family, shape_lang, w, h):
    title = str(content.get("title") or "Title")
    paras = content.get("paragraphs") or [str(content.get("body") or "body")]
    title_font = _load_font(heading_family, 42, bold=True)
    body_font = _load_font(body_family, 17, bold=False)
    draw.text((80, 80), title, fill=palette["accent"], font=title_font)
    y = 160
    for para in paras:
        for line in str(para).split("\n"):
            while line:
                chunk = line[:80]
                line = line[80:]
                draw.text((80, y), chunk, fill=palette["text"], font=body_font)
                y += 26
                if y > h - 80:
                    return
        y += 10
