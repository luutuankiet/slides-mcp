"""Helpers to build mock Google Slides API JSON for tests."""
from __future__ import annotations

from typing import Any

EMU = 914400


def _dim(inches: float) -> dict[str, Any]:
    return {"magnitude": int(inches * EMU), "unit": "EMU"}


def size(w: float, h: float) -> dict[str, Any]:
    return {"width": _dim(w), "height": _dim(h)}


def transform(left: float, top: float) -> dict[str, Any]:
    return {
        "scaleX": 1.0,
        "scaleY": 1.0,
        "translateX": int(left * EMU),
        "translateY": int(top * EMU),
        "unit": "EMU",
    }


def rgb(r: float, g: float, b: float) -> dict[str, Any]:
    return {"red": r, "green": g, "blue": b}


def hex_to_rgb(h: str) -> dict[str, Any]:
    """#AABBCC → {red: 0.666, green: 0.733, blue: 0.8}."""
    h = h.lstrip("#")
    r = int(h[0:2], 16) / 255
    g = int(h[2:4], 16) / 255
    b = int(h[4:6], 16) / 255
    return rgb(r, g, b)


def text_element(content: str, font: str | None = None, size_pt: float | None = None,
                 color_hex: str | None = None, bold: bool = False) -> dict[str, Any]:
    style: dict[str, Any] = {}
    if font:
        style["fontFamily"] = font
    if size_pt:
        style["fontSize"] = {"magnitude": size_pt, "unit": "PT"}
    if bold:
        style["bold"] = True
    if color_hex:
        style["foregroundColor"] = {"opaqueColor": {"rgbColor": hex_to_rgb(color_hex)}}
    return {"textRun": {"content": content, "style": style}}


def textbox(obj_id: str, content: str, left: float, top: float, w: float, h: float,
            font: str | None = None, size_pt: float | None = None,
            color_hex: str | None = None, bold: bool = False) -> dict[str, Any]:
    return {
        "objectId": obj_id,
        "size": size(w, h),
        "transform": transform(left, top),
        "shape": {
            "shapeType": "TEXT_BOX",
            "text": {"textElements": [text_element(content, font, size_pt, color_hex, bold)]},
        },
    }


def picture(obj_id: str, url: str, left: float, top: float, w: float, h: float) -> dict[str, Any]:
    return {
        "objectId": obj_id,
        "size": size(w, h),
        "transform": transform(left, top),
        "image": {"contentUrl": url, "sourceUrl": url},
    }


def shape_with_fill(obj_id: str, shape_type: str, left: float, top: float, w: float, h: float,
                    fill_hex: str | None = None) -> dict[str, Any]:
    shape_props: dict[str, Any] = {}
    if fill_hex:
        shape_props["shapeBackgroundFill"] = {
            "solidFill": {"color": {"rgbColor": hex_to_rgb(fill_hex)}}
        }
    return {
        "objectId": obj_id,
        "size": size(w, h),
        "transform": transform(left, top),
        "shape": {
            "shapeType": shape_type,
            "shapeProperties": shape_props,
            "text": {"textElements": []},
        },
    }


def vline(obj_id: str, left: float, top: float, height: float) -> dict[str, Any]:
    return {
        "objectId": obj_id,
        "size": size(0.01, height),
        "transform": transform(left, top),
        "line": {"lineType": "STRAIGHT_CONNECTOR_1"},
    }


def page(object_id: str, elements: list[dict[str, Any]], notes_text: str = "") -> dict[str, Any]:
    p: dict[str, Any] = {"objectId": object_id, "pageElements": elements}
    if notes_text:
        p["slideProperties"] = {
            "notesPage": {
                "pageElements": [{
                    "objectId": f"{object_id}_notes",
                    "size": size(10, 4),
                    "transform": transform(0, 0),
                    "shape": {
                        "shapeType": "TEXT_BOX",
                        "placeholder": {"type": "BODY"},
                        "text": {"textElements": [text_element(notes_text)]},
                    },
                }]
            }
        }
    return p


# Pre-baked representative slides for tests

def slide_3col_pill_cards() -> dict[str, Any]:
    """Mirrors sales slide 2 — 'Looker is the Heart of Business Analytics'."""
    return page("slide_3col", [
        textbox("title", "Looker is the Heart of Business Analytics",
                1.8, 0.6, 12.4, 0.9, font="Inter", size_pt=36, bold=True),
        textbox("lead",
                "Companies have invested in Looker as the single source of truth "
                "and governed data insights platform.",
                0.9, 2.2, 14.3, 2.0, font="Inter", size_pt=18),
        # 3 card backgrounds
        shape_with_fill("card1", "RECTANGLE", 0.9, 5.6, 4.6, 2.3, fill_hex="#F3F3F3"),
        shape_with_fill("card2", "RECTANGLE", 5.7, 5.6, 4.6, 2.3, fill_hex="#F3F3F3"),
        shape_with_fill("card3", "RECTANGLE", 10.5, 5.6, 4.6, 2.3, fill_hex="#F3F3F3"),
        # pill headers
        textbox("pill1", "Semantic Layer", 1.1, 5.9, 2.9, 0.4,
                font="Inter", size_pt=22, bold=True, color_hex="#3366CC"),
        textbox("pill2", "Empowered Users", 5.9, 5.9, 2.9, 0.4,
                font="Inter", size_pt=22, bold=True, color_hex="#3366CC"),
        textbox("pill3", "Untapped Potential", 10.7, 5.9, 2.9, 0.4,
                font="Inter", size_pt=22, bold=True, color_hex="#3366CC"),
        # body texts
        textbox("body1", "Trusted LookML models and governed data environment",
                1.1, 6.3, 4.2, 0.7, font="Inter", size_pt=18),
        textbox("body2", "Both centralized teams and self-service business users",
                5.9, 6.3, 4.2, 0.7, font="Inter", size_pt=18),
        textbox("body3", "Gap between data availability and business action",
                10.7, 6.3, 4.2, 0.7, font="Inter", size_pt=18),
    ], notes_text="Emphasize the last-mile problem in this section.")


def slide_cover_with_hero() -> dict[str, Any]:
    return page("slide_cover", [
        picture("hero", "https://example.test/cityscape.jpg", 0.0, 0.0, 8.0, 9.0),
        shape_with_fill("accent_panel", "RECTANGLE", 8.0, 0.0, 8.0, 9.0, fill_hex="#1F4F9F"),
        textbox("title", "Agentic analytics", 9.0, 3.0, 6.5, 1.3,
                font="Inter", size_pt=60, bold=True, color_hex="#FFFFFF"),
        textbox("subtitle", "From Insight to Impact with Looker", 9.0, 4.4, 6.5, 0.6,
                font="Inter", size_pt=22, color_hex="#FFFFFF"),
    ])


def slide_text_heavy() -> dict[str, Any]:
    long = (
        "This is a long paragraph of content that exceeds 300 characters in total "
        "so the classifier should mark it as text_heavy_body. It contains multiple "
        "sentences and clauses meant to represent the kind of content that ends up "
        "on a long-form narrative slide without images. "
    ) * 2
    return page("slide_heavy", [
        textbox("title", "Background and Context", 0.5, 0.5, 15.0, 0.8,
                font="Inter", size_pt=36, bold=True),
        textbox("body", long, 0.5, 1.6, 15.0, 7.0, font="Inter", size_pt=18),
    ])


def slide_text_left_image_right() -> dict[str, Any]:
    return page("slide_ti", [
        textbox("title", "OOTB System Activity Comparator", 0.5, 0.5, 15.0, 0.8,
                font="Inter", size_pt=36, bold=True),
        textbox("body", "Automated comparator to detect OOTB system changes instantly. "
                "Engineered to reduce manual overhead through proactive reporting.",
                0.5, 1.6, 7.5, 4.0, font="Inter", size_pt=18),
        picture("screenshot", "https://example.test/screenshot.png",
                8.5, 1.6, 7.0, 5.0),
    ])


def slide_4col_numbered_flow() -> dict[str, Any]:
    base = [
        textbox("title", "Priorities from last QBR Retrospective",
                0.5, 0.5, 15.0, 0.6, font="Inter", size_pt=28, bold=True),
    ]
    for i, (num, title, body) in enumerate([
        ("01", "Field Usage Explore Bug Fixes",
         "Isolated the root cause in the Field Usage pipeline"),
        ("02", "OOTB System Activity Comparator",
         "Engineering an automated comparator to detect OOTB system changes"),
        ("03", "LookML Dashboard View",
         "Enrich monitoring dashboard with insights of LookML Dashboard"),
        ("04", "Health check on Monitoring Dashboards",
         "Reduce 20 monitoring dashboards runtimes significantly"),
    ]):
        left = 0.5 + i * 3.6
        base.append(textbox(f"num_{i}", num, left, 1.1, 0.8, 0.8,
                            font="Inter", size_pt=36, bold=True, color_hex="#3366CC"))
        base.append(textbox(f"sub_{i}", title, left, 2.0, 3.2, 0.6,
                            font="Inter", size_pt=22, bold=True))
        base.append(textbox(f"body_{i}", body, left, 2.8, 3.2, 3.0,
                            font="Inter", size_pt=14))
    # 3 vertical separator lines
    for i in range(1, 4):
        base.append(vline(f"line_{i}", 0.5 + i * 3.6 - 0.2, 1.1, 3.2))
    return page("slide_4col", base)
