"""A fake Slides API that serves a deck fixture and records every batchUpdate.

Tests drive the MCP tools exactly as a client would; this stands in for
Google. It applies the handful of request kinds the tests need so a re-read
after `commit()` or a notes write sees the change.
"""
from __future__ import annotations

import copy
import json
import time
from pathlib import Path
from typing import Any

from slides_mcp import notes_md, slides_api
from slides_mcp.normalize import utf16_len

FIXTURE = Path(__file__).parent / "fixtures" / "sandbox_deck.json"
EMU = 914400


def _rgb(h: str) -> dict[str, float]:
    h = h.lstrip("#")
    return {"red": int(h[0:2], 16) / 255, "green": int(h[2:4], 16) / 255,
            "blue": int(h[4:6], 16) / 255}


def _box(oid: str, x: float, y: float, w: float, h: float, **extra: Any) -> dict[str, Any]:
    el = {
        "objectId": oid,
        "size": {"width": {"magnitude": 3000000, "unit": "EMU"},
                 "height": {"magnitude": 3000000, "unit": "EMU"}},
        "transform": {"scaleX": w * EMU / 3000000, "scaleY": h * EMU / 3000000,
                      "translateX": x * EMU, "translateY": y * EMU, "unit": "EMU"},
    }
    el.update(extra)
    return el


def _text(runs: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
    els: list[dict[str, Any]] = []
    pos = 0
    total = sum(utf16_len(t) for t, _ in runs)
    els.append({"endIndex": total, "paragraphMarker": {}})
    for t, style in runs:
        el: dict[str, Any] = {"endIndex": pos + utf16_len(t),
                              "textRun": {"content": t, "style": style}}
        if pos:
            el["startIndex"] = pos
        els.append(el)
        pos += utf16_len(t)
    return {"textElements": els}


def _style(font: str = "Arial", size: float = 12, weight: int = 400, bold: bool = False,
           color: str | None = "#222222", theme: str | None = None) -> dict[str, Any]:
    st: dict[str, Any] = {"fontFamily": font, "fontSize": {"magnitude": size, "unit": "PT"},
                          "bold": bold, "weightedFontFamily": {"fontFamily": font, "weight": weight}}
    if theme:
        st["foregroundColor"] = {"opaqueColor": {"themeColor": theme}}
    elif color:
        st["foregroundColor"] = {"opaqueColor": {"rgbColor": _rgb(color)}}
    return st


NO_FILL = {"shapeBackgroundFill": {"propertyState": "NOT_RENDERED",
                                   "solidFill": {"color": {"rgbColor": _rgb("#FFFFFF")}, "alpha": 1}},
           "outline": {"propertyState": "NOT_RENDERED"},
           "autofit": {"autofitType": "NONE"}}


def cases_slide() -> dict[str, Any]:
    """Hand-built cases the recorded deck lacks, all on one light slide."""
    rich_notes = {
        "text": {"textElements": [
            {"endIndex": 6, "paragraphMarker": {}},
            {"endIndex": 6, "textRun": {"content": "Intro\n", "style": {
                "bold": True, "fontSize": {"magnitude": 18, "unit": "PT"}}}},
            {"startIndex": 6, "endIndex": 20, "paragraphMarker": {}},
            {"startIndex": 6, "endIndex": 10, "textRun": {"content": "Say ", "style": {
                "fontSize": {"magnitude": 11, "unit": "PT"}}}},
            {"startIndex": 10, "endIndex": 14, "textRun": {"content": "this", "style": {
                "bold": True, "fontSize": {"magnitude": 11, "unit": "PT"}}}},
            {"startIndex": 14, "endIndex": 20, "textRun": {"content": " now.\n", "style": {
                "fontSize": {"magnitude": 11, "unit": "PT"}}}},
            {"startIndex": 20, "endIndex": 26, "paragraphMarker": {
                "bullet": {"listId": "l1"}}},
            {"startIndex": 20, "endIndex": 26, "textRun": {"content": "point\n", "style": {
                "fontSize": {"magnitude": 11, "unit": "PT"}}}},
            {"startIndex": 26, "endIndex": 31, "paragraphMarker": {
                "bullet": {"listId": "l1", "nestingLevel": 1}}},
            {"startIndex": 26, "endIndex": 31, "textRun": {"content": "sub\n\n", "style": {
                "italic": True, "fontSize": {"magnitude": 11, "unit": "PT"}}}},
        ]},
    }
    return {
        "objectId": "slide_cases",
        "pageProperties": {"pageBackgroundFill": {"solidFill": {
            "color": {"rgbColor": _rgb("#FAFAFA")}, "alpha": 1}}},
        "slideProperties": {
            "layoutObjectId": "",
            "notesPage": {
                "notesProperties": {"speakerNotesObjectId": "cases_notes"},
                "pageElements": [{"objectId": "cases_notes", "shape": {
                    "placeholder": {"type": "BODY"}, **rich_notes}}],
            },
        },
        "pageElements": [
            _box("transparent_box", 1, 1, 4, 0.5, shape={
                "shapeType": "TEXT_BOX", "shapeProperties": NO_FILL,
                "text": _text([("Clear\n", _style())])}),
            _box("translucent_badge", 6, 1, 1, 0.4, shape={
                "shapeType": "ROUND_RECTANGLE", "shapeProperties": {
                    "shapeBackgroundFill": {"solidFill": {"color": {"themeColor": "ACCENT1"},
                                                          "alpha": 0.2}},
                    "autofit": {"autofitType": "NONE"}}}),
            {**_box("rotated_label", 8, 1, 2, 0.5, shape={
                "shapeType": "TEXT_BOX", "shapeProperties": NO_FILL,
                "text": _text([("Rotated\n", _style())])}),
             "transform": {"shearX": -2 * EMU / 3000000, "shearY": 0.5 * EMU / 3000000,
                           "translateX": 8 * EMU, "translateY": 1 * EMU, "unit": "EMU"}},
            _box("weighted_heading", 1, 2, 8, 0.8, shape={
                "shapeType": "TEXT_BOX", "shapeProperties": NO_FILL,
                "text": _text([("Heavy heading\n", _style("Nunito Sans", 28, 800, bold=True))])}),
            _box("stat_box", 1, 3.2, 1.2, 0.6, shape={
                "shapeType": "TEXT_BOX", "shapeProperties": NO_FILL,
                "text": _text([("98.6%\n", _style("Arial", 24, 700, bold=True))])}),
            _box("theme_text", 1, 4, 4, 0.5, shape={
                "shapeType": "TEXT_BOX", "shapeProperties": NO_FILL,
                "text": _text([("Theme coloured \U0001F600 text\n", _style(theme="ACCENT2"))])}),
        ],
    }


def empty_notes_slide() -> dict[str, Any]:
    return {
        "objectId": "slide_blank",
        "slideProperties": {"notesPage": {
            "notesProperties": {"speakerNotesObjectId": "blank_notes"},
            "pageElements": [{"objectId": "blank_notes", "shape": {
                "placeholder": {"type": "BODY"}}}]}},
        "pageElements": [],
    }


def load_deck() -> dict[str, Any]:
    deck = json.loads(FIXTURE.read_text())
    deck["slides"].extend([cases_slide(), empty_notes_slide()])
    return copy.deepcopy(deck)  # the case builders share module-level dicts


class FakeSlides:
    def __init__(self, deck: dict[str, Any] | None = None):
        self.deck = deck or load_deck()
        self.batches: list[list[dict[str, Any]]] = []
        self.reads = 0
        self.fail: slides_api.SlidesApiError | None = None
        self.batch_delay = 0.0
        # Thumbnails: every (slide_id, size) asked for, and optional hooks.
        self.thumbnails: list[tuple[str, str]] = []
        self.thumbnail_fail: dict[str, Exception] = {}
        self.thumbnail_hook: Any = None

    # --- API surface -----------------------------------------------------
    def get_presentation(self, deck_id: str, fields: str | None = None) -> dict[str, Any]:
        self.reads += 1
        return copy.deepcopy(self.deck)

    def batch_update(self, deck_id: str, requests: list[dict]) -> dict:
        if self.batch_delay:
            time.sleep(self.batch_delay)
        if self.fail:
            raise self.fail
        self.batches.append(copy.deepcopy(requests))
        replies: list[dict[str, Any]] = []
        for req in requests:
            replies.append(self._apply(req) or {})
        return {"presentationId": deck_id, "replies": replies}

    def get_thumbnail_bytes(self, deck_id: str, slide_id: str, size: str = "MEDIUM") -> bytes:
        self.thumbnails.append((slide_id, size))
        if self.thumbnail_hook:
            self.thumbnail_hook(slide_id)
        if err := self.thumbnail_fail.get(slide_id):
            raise err
        return b"\x89PNG fake " + size.encode() + b" " + slide_id.encode()

    # --- tiny mutation model ----------------------------------------------
    def _elements(self) -> dict[str, dict[str, Any]]:
        idx: dict[str, dict[str, Any]] = {}

        def walk(el: dict[str, Any]) -> None:
            idx[el["objectId"]] = el
            for c in (el.get("elementGroup") or {}).get("children") or []:
                walk(c)

        for s in self.deck["slides"]:
            for el in s.get("pageElements") or []:
                walk(el)
            for el in ((s.get("slideProperties") or {}).get("notesPage") or {}).get(
                    "pageElements") or []:
                walk(el)
        return idx

    def _apply(self, req: dict[str, Any]) -> dict[str, Any] | None:
        (kind, body), = req.items()
        els = self._elements()
        el = els.get(body.get("objectId", ""))
        if el is not None and el.get("shape", {}).get("placeholder", {}).get("type") == "BODY" and \
                kind in ("insertText", "deleteText", "updateTextStyle", "createParagraphBullets",
                         "deleteParagraphBullets"):
            el["shape"] = notes_md.apply_to_shape(el["shape"], [req])
            return
        if kind == "updateShapeProperties" and el is not None:
            props = el["shape"].setdefault("shapeProperties", {})
            props.update(copy.deepcopy(body["shapeProperties"]))
        elif kind == "updateTextStyle" and el is not None:
            for te in el["shape"].get("text", {}).get("textElements", []):
                if "textRun" in te:
                    te["textRun"]["style"].update(copy.deepcopy(body["style"]))
        elif kind == "createShape":
            page = body["elementProperties"]["pageObjectId"]
            for s in self.deck["slides"]:
                if s["objectId"] == page:
                    s.setdefault("pageElements", []).append({
                        "objectId": body["objectId"],
                        "size": body["elementProperties"].get("size"),
                        "transform": body["elementProperties"].get("transform"),
                        "shape": {"shapeType": body.get("shapeType", "RECTANGLE")},
                    })
        elif kind == "deleteObject":
            for s in self.deck["slides"]:
                s["pageElements"] = [e for e in s.get("pageElements") or []
                                     if e["objectId"] != body["objectId"]]
        elif kind == "createImage":
            ep = body["elementProperties"]
            oid = body.get("objectId") or f"image_{len(self.batches)}"
            for s in self.deck["slides"]:
                if s["objectId"] == ep["pageObjectId"]:
                    s.setdefault("pageElements", []).append({
                        "objectId": oid, "size": ep.get("size"),
                        "transform": ep.get("transform"),
                        "image": {"sourceUrl": body["url"]}})
            return {"createImage": {"objectId": oid}}
        elif kind == "replaceAllShapesWithImage":
            # Like Slides: a scoped replace that matches nothing answers {}.
            marker = body["containsText"]["text"]
            pages = body.get("pageObjectIds")
            changed = 0
            for s in self.deck["slides"]:
                if pages and s["objectId"] not in pages:
                    continue
                for i, e in enumerate(s.get("pageElements") or []):
                    text = "".join(te.get("textRun", {}).get("content", "")
                                   for te in e.get("shape", {}).get("text", {})
                                   .get("textElements", []))
                    if marker in text:
                        s["pageElements"][i] = {
                            "objectId": e["objectId"], "size": e.get("size"),
                            "transform": e.get("transform"),
                            "image": {"sourceUrl": body["imageUrl"]}}
                        changed += 1
            if changed:
                return {"replaceAllShapesWithImage": {"occurrencesChanged": changed}}
            return {"replaceAllShapesWithImage": {}}
        return None
