"""Receipts: write tools attach thumbnails of the slides they touched.

Called as an MCP client would, over the fake Slides API. Assertions are about
what came back (reply fields, attached images) and which thumbnails the fake
was asked for.
"""
from __future__ import annotations

import contextvars
import json
import threading
from typing import Any

from fastmcp.utilities.types import Image

from slides_mcp.server import (
    add_section_footers,
    exec_batch_update,
    run_deck_script,
    write_speaker_notes,
)
from slides_mcp.slides_api import SlidesApiError

DECK = "deck_fixture"


def split(out: Any) -> tuple[dict[str, Any], list[Image]]:
    """A tool reply as (JSON body, attached images)."""
    if isinstance(out, list):
        return json.loads(out[0]), list(out[1:])
    return out, []


def box_on(slide_id: str, oid: str) -> dict[str, Any]:
    return {"createShape": {"objectId": oid, "shapeType": "RECTANGLE",
                            "elementProperties": {"pageObjectId": slide_id}}}


def test_batch_passthrough_attaches_medium_thumbnail_of_touched_slide(fake):
    out = exec_batch_update(DECK, [box_on("slide_cases", "new_box")])
    body, images = split(out)
    assert body["isError"] is False
    assert fake.thumbnails == [("slide_cases", "MEDIUM")]
    assert len(images) == 1
    assert images[0].data == b"\x89PNG fake MEDIUM slide_cases"
    assert body["thumbnails"]["slide_ids"] == ["slide_cases"]


def test_receipt_off_attaches_nothing(fake):
    out = exec_batch_update(DECK, [box_on("slide_cases", "new_box")], receipt="off")
    body, images = split(out)
    assert images == []
    assert fake.thumbnails == []
    assert "thumbnails" not in body


def test_receipt_large_requests_the_large_size(fake):
    _, images = split(exec_batch_update(DECK, [box_on("slide_cases", "new_box")],
                                        receipt="large"))
    assert fake.thumbnails == [("slide_cases", "LARGE")]
    assert len(images) == 1


def test_five_touched_slides_attach_three_and_list_two(fake):
    order = [s["objectId"] for s in fake.deck["slides"]]
    five = order[:5]
    out = exec_batch_update(DECK, [box_on(sid, f"b{i}") for i, sid in enumerate(five)])
    body, images = split(out)
    assert len(images) == 3
    assert body["thumbnails"]["slide_ids"] == five[:3]
    assert body["thumbnails"]["not_shown_slide_ids"] == five[3:]
    assert "render_thumbnail" in body["thumbnails"]["hint"]
    assert [sid for sid, _ in fake.thumbnails] == five[:3]


def three_slides(fake) -> list[str]:
    return [s["objectId"] for s in fake.deck["slides"]][:3]


def test_thumbnail_failure_is_a_warning_not_an_error(fake):
    fake.thumbnail_fail["slide_cases"] = SlidesApiError("Slides API error 500: boom", status=500)
    out = exec_batch_update(DECK, [box_on("slide_cases", "new_box")])
    body, images = split(out)
    assert body["isError"] is False
    assert body["applied_request_count"] == 1
    assert images == []
    assert any("slide_cases" in w and "boom" in w for w in body["warnings"])


def test_rate_limited_thumbnail_is_not_retried_and_names_the_limit(fake):
    first, second, _ = three_slides(fake)
    fake.thumbnail_fail[first] = SlidesApiError("Slides API error 429: quota", status=429)
    out = exec_batch_update(DECK, [box_on(first, "a"), box_on(second, "b")])
    body, images = split(out)
    assert body["isError"] is False
    assert len(images) == 1
    assert [sid for sid, _ in fake.thumbnails].count(first) == 1
    assert body["thumbnails"]["slide_ids"] == [second]
    assert any(first in w and "60 requests a minute per user" in w for w in body["warnings"])


def test_receipt_thumbnails_are_fetched_concurrently(fake):
    # Each fetch waits until all three are in flight; one after another, it times out.
    barrier = threading.Barrier(3, timeout=5)
    fake.thumbnail_hook = lambda sid: barrier.wait()
    slides = three_slides(fake)
    out = exec_batch_update(DECK, [box_on(sid, f"b{i}") for i, sid in enumerate(slides)])
    body, images = split(out)
    assert body["warnings"] == []
    assert len(images) == 3


CALLER: contextvars.ContextVar[str] = contextvars.ContextVar("caller", default="nobody")


def test_receipt_thumbnails_run_as_the_calling_request(fake):
    # HTTP mode finds the signed-in caller in context variables.
    seen: list[str] = []
    fake.thumbnail_hook = lambda sid: seen.append(CALLER.get())
    token = CALLER.set("alice")
    try:
        exec_batch_update(DECK, [box_on("slide_cases", "new_box")])
    finally:
        CALLER.reset(token)
    assert seen == ["alice"]


# ---- the other write tools --------------------------------------------------


def test_section_footers_attach_a_receipt(fake):
    out = add_section_footers(DECK, sections=[{"name": "Intro", "slide_ids": ["slide_cases"]}])
    body, images = split(out)
    assert body["footers_added"] == 1
    assert body["thumbnails"]["slide_ids"] == ["slide_cases"]
    assert fake.thumbnails == [("slide_cases", "MEDIUM")]
    assert len(images) == 1


def test_speaker_note_write_attaches_no_receipt(fake):
    out = write_speaker_notes(deck_url=DECK, notes={"slide_blank": "Hello"})
    assert isinstance(out, dict)
    assert out["isError"] is False
    assert "thumbnails" not in out
    assert fake.thumbnails == []


SCRIPT = "emit(setFill('stat_box', '#000000'))"


async def test_deck_script_apply_attaches_receipt_of_touched_slides(fake):
    out = await run_deck_script(deck_url=DECK, script=SCRIPT, dry_run=False)
    body, images = split(out)
    assert body["isError"] is False
    assert body["thumbnails"]["slide_ids"] == ["slide_cases"]
    assert fake.thumbnails == [("slide_cases", "MEDIUM")]
    assert len(images) == 1


async def test_deck_script_dry_run_attaches_nothing(fake):
    out = await run_deck_script(deck_url=DECK, script=SCRIPT)
    assert isinstance(out, dict)
    assert fake.thumbnails == []


async def test_deck_script_render_slides_overrides_touched_slides(fake):
    first = fake.deck["slides"][0]["objectId"]
    out = await run_deck_script(deck_url=DECK, script=SCRIPT, dry_run=False,
                                render_slides=[1, "slide_blank"], receipt="large")
    body, images = split(out)
    assert body["thumbnails"]["slide_ids"] == [first, "slide_blank"]
    assert fake.thumbnails == [(first, "LARGE"), ("slide_blank", "LARGE")]
    assert len(images) == 2


async def test_deck_script_render_slides_keeps_its_limit_of_six(fake):
    out = await run_deck_script(deck_url=DECK, script=SCRIPT, dry_run=False,
                                render_slides="1-6")
    body, images = split(out)
    assert len(images) == 6
    assert "not_shown_slide_ids" not in body["thumbnails"]


async def test_deck_script_receipt_off_attaches_nothing(fake):
    out = await run_deck_script(deck_url=DECK, script=SCRIPT, dry_run=False, receipt="off")
    assert isinstance(out, dict)
    assert fake.thumbnails == []
