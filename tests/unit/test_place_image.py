"""place_image: put SVG or an image URL into a placeholder or a box.

Called as an MCP client would, over the fake Slides API and a hosted state
store whose bucket is a fake GCS client at the network boundary. Assertions
are about the reply, the requests that reached the fake, and what the bucket
holds afterwards.
"""
from __future__ import annotations

import json
import struct
from typing import Any

import pytest

from slides_mcp import slides_api, state_store
from slides_mcp.server import place_image
from slides_mcp.slides_api import SlidesApiError
from tests.fake_api import _box, _style, _text

DECK = "deck_fixture"
SLIDE = "slide_cases"
MARKER = "{{DIAGRAM}}"
# 2:1 diagram with a label in a generic family.
SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 100">'
       '<rect x="10" y="10" width="180" height="80" rx="8" fill="#3366cc"/>'
       '<text x="100" y="58" font-family="sans-serif" font-size="20" fill="#fff" '
       'text-anchor="middle">Plan</text></svg>')


class _Blob:
    def __init__(self, gcs: FakeGcs, name: str):
        self.gcs, self.name = gcs, name

    def upload_from_string(self, data: bytes, content_type: str) -> None:
        self.gcs.objects[self.name] = data
        self.gcs.uploads.append((self.name, data, content_type))

    def generate_signed_url(self, **kw: Any) -> str:
        return f"https://storage.example/{self.name}?X-Goog-Signature=abc"

    def delete(self) -> None:
        del self.gcs.objects[self.name]


class FakeGcs:
    """GCS at the network boundary: holds objects, signs every URL."""

    class _credentials:  # the attribute the store reads to decide how to sign
        valid = True
        service_account_email = "runner@example.iam.gserviceaccount.com"
        token = "t"

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.uploads: list[tuple[str, bytes, str]] = []

    def bucket(self, name: str) -> FakeGcs:
        return self

    def blob(self, name: str) -> _Blob:
        return _Blob(self, name)


@pytest.fixture
def gcs(monkeypatch: pytest.MonkeyPatch) -> FakeGcs:
    """The hosted store, as serve-http installs it, over a fake bucket."""
    fake_gcs = FakeGcs()
    monkeypatch.setattr(state_store, "_current", state_store.HostedStateStore(
        database="slides-mcp-auth", image_bucket="images", storage_client=fake_gcs))
    return fake_gcs


def split(out: Any) -> tuple[dict[str, Any], list[Any]]:
    if isinstance(out, list):
        return json.loads(out[0]), list(out[1:])
    return out, []


def png_size(data: bytes) -> tuple[int, int]:
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", data[16:24])


BOX = {"x": 40, "y": 60, "width": 300, "height": 150}


async def test_svg_into_a_box_creates_one_image_at_that_box_and_cleans_up(fake, gcs):
    body, _ = split(await place_image(DECK, slide_id=SLIDE, svg=SVG, box=BOX))

    assert body["isError"] is False, body
    assert len(fake.batches) == 1 and len(fake.batches[0]) == 1
    create = fake.batches[0][0]["createImage"]
    ep = create["elementProperties"]
    assert ep["pageObjectId"] == SLIDE
    assert ep["size"] == {"width": {"magnitude": 300, "unit": "PT"},
                          "height": {"magnitude": 150, "unit": "PT"}}
    assert ep["transform"] == {"scaleX": 1, "scaleY": 1, "translateX": 40,
                               "translateY": 60, "unit": "PT"}
    (name, png, content_type), = gcs.uploads
    assert content_type == "image/png"
    assert create["url"].startswith(f"https://storage.example/{name}?")
    w, h = png_size(png)
    assert w == 2 * h  # rasterised at the box's 2:1 aspect, so the box is kept
    assert gcs.objects == {}


@pytest.fixture
def marker_box(fake) -> str:
    """A 4 x 2 inch box holding the marker on SLIDE, as a user would prepare it."""
    slide = next(s for s in fake.deck["slides"] if s["objectId"] == SLIDE)
    slide["pageElements"].append(_box("diagram_box", 1, 1, 4, 2, shape={
        "shapeType": "RECTANGLE", "text": _text([(MARKER + "\n", _style())])}))
    return "diagram_box"


async def test_svg_into_a_placeholder_replaces_it_on_that_slide_only(fake, gcs, marker_box):
    body, _ = split(await place_image(DECK, slide_id=SLIDE, svg=SVG, placeholder=MARKER,
                                      confirm_destructive=True))

    assert body["isError"] is False, body
    (request,), = fake.batches
    replace = request["replaceAllShapesWithImage"]
    assert replace["pageObjectIds"] == [SLIDE]
    assert replace["containsText"] == {"text": MARKER, "matchCase": True}
    assert replace["imageReplaceMethod"] == "CENTER_INSIDE"
    (name, png, _), = gcs.uploads
    assert replace["imageUrl"].startswith(f"https://storage.example/{name}?")
    w, h = png_size(png)
    assert w == 2 * h  # the marker box is 4 x 2 inches
    assert gcs.objects == {}
    assert body["placed"] == {"slide_id": SLIDE, "object_ids": [marker_box],
                              "source": "svg", "target": "placeholder"}


async def test_cover_fit_crops_into_the_placeholder(fake, gcs, marker_box):
    await place_image(DECK, slide_id=SLIDE, image_url="https://example.com/a.png",
                      placeholder=MARKER, fit="cover", confirm_destructive=True)
    (request,), = fake.batches
    assert request["replaceAllShapesWithImage"]["imageReplaceMethod"] == "CENTER_CROP"


async def test_image_url_skips_rasterising_and_the_bucket(fake, monkeypatch):
    # The stdio store: any bucket use would raise.
    monkeypatch.setattr(state_store, "_current", state_store.MemoryStateStore())
    url = "https://example.com/photo.jpg"
    body, _ = split(await place_image(DECK, slide_id=SLIDE, image_url=url, box=BOX))

    assert body["isError"] is False, body
    (request,), = fake.batches
    assert request["createImage"]["url"] == url
    assert body["placed"]["source"] == "image_url"
    assert any("aspect ratio" in w for w in body["warnings"])


async def test_placeholder_without_confirm_destructive_is_refused_before_any_work(
        fake, gcs, marker_box):
    body = await place_image(DECK, slide_id=SLIDE, svg=SVG, placeholder=MARKER)

    assert body["isError"] is True
    assert "confirm_destructive=True" in body["warnings"][0]
    assert "replaceAllShapesWithImage" in body["warnings"][0]
    assert fake.batches == [] and gcs.uploads == []


async def test_reply_carries_a_receipt_of_the_slide(fake, gcs):
    body, images = split(await place_image(DECK, slide_id=SLIDE, svg=SVG, box=BOX))
    assert fake.thumbnails == [(SLIDE, "MEDIUM")]
    assert body["thumbnails"]["slide_ids"] == [SLIDE]
    assert len(images) == 1


# ---- errors ---------------------------------------------------------------


async def test_svg_over_stdio_says_it_needs_the_hosted_server(fake, monkeypatch):
    monkeypatch.setattr(state_store, "_current", state_store.MemoryStateStore())
    body = await place_image(DECK, slide_id=SLIDE, svg=SVG, box=BOX)
    assert body["isError"] is True
    assert body["error"]["kind"] == "hosting"
    assert "slides-mcp serve-http" in body["error"]["message"]
    assert fake.batches == []


async def test_svg_without_a_bucket_names_the_missing_setting(fake, monkeypatch):
    monkeypatch.setattr(state_store, "_current", state_store.HostedStateStore(
        database="slides-mcp-auth", image_bucket=None))
    body = await place_image(DECK, slide_id=SLIDE, svg=SVG, box=BOX)
    assert body["error"]["kind"] == "hosting"
    assert "SLIDES_MCP_IMAGE_BUCKET" in body["error"]["message"]


async def test_svg_that_fails_to_render_quotes_the_renderer(fake, gcs):
    body = await place_image(DECK, slide_id=SLIDE, svg="<svg", box=BOX)
    assert body["error"]["kind"] == "svg"
    assert "not well-formed" in body["error"]["message"]
    assert gcs.uploads == [] and fake.batches == []


async def test_svg_reaching_outside_the_document_is_refused(fake, gcs):
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
           '<image href="/etc/hostname.png" width="10" height="10"/></svg>')
    body = await place_image(DECK, slide_id=SLIDE, svg=svg, box=BOX)
    assert body["error"]["kind"] == "svg"
    assert "/etc/hostname.png" in body["error"]["message"]
    assert gcs.uploads == []


async def test_google_rejecting_the_image_passes_its_reason_on_and_cleans_up(fake, gcs):
    fake.fail = SlidesApiError(
        "Slides API error 400: Invalid requests[0].createImage: The provided image "
        "was not found.", status=400, reason="x")
    body = await place_image(DECK, slide_id=SLIDE, svg=SVG, box=BOX)
    assert body["isError"] is True
    assert body["error"] == {"kind": "google",
                             "message": "The provided image was not found."}
    assert len(gcs.uploads) == 1 and gcs.objects == {}


async def test_marker_missing_from_the_slide_is_an_error_before_upload(fake, gcs):
    body = await place_image(DECK, slide_id=SLIDE, svg=SVG, placeholder="{{NOPE}}",
                             confirm_destructive=True)
    assert body["error"]["kind"] == "marker"
    assert "{{NOPE}}" in body["error"]["message"] and "layout" in body["error"]["message"]
    assert gcs.uploads == [] and fake.batches == []


async def test_empty_replace_reply_is_marker_not_found_and_cleans_up(
        fake, gcs, marker_box, monkeypatch):
    # Slides answers {} when the scoped replace matched nothing (HTTP 200).
    monkeypatch.setattr(slides_api, "batch_update", lambda deck_id, requests: {
        "replies": [{"replaceAllShapesWithImage": {}}]})
    body, _ = split(await place_image(DECK, slide_id=SLIDE, svg=SVG, placeholder=MARKER,
                                      confirm_destructive=True))
    assert body["isError"] is True
    assert body["error"]["kind"] == "marker"
    assert SLIDE in body["error"]["message"]
    assert len(gcs.uploads) == 1 and gcs.objects == {}


async def test_unknown_slide_is_an_error(fake, gcs):
    body = await place_image(DECK, slide_id="no_such_slide", svg=SVG, box=BOX)
    assert body["error"]["kind"] == "target"
    assert gcs.uploads == []


async def test_failed_cleanup_is_a_warning_not_an_error(fake, gcs, monkeypatch):
    def refuse(self):
        raise RuntimeError("storage unavailable")
    monkeypatch.setattr(_Blob, "delete", refuse)
    body, _ = split(await place_image(DECK, slide_id=SLIDE, svg=SVG, box=BOX))
    assert body["isError"] is False
    assert any("not deleted" in w and "storage unavailable" in w for w in body["warnings"])


@pytest.mark.parametrize("kwargs, message", [
    ({"svg": SVG, "image_url": "https://example.com/a.png", "box": BOX}, "exactly one of"),
    ({"box": BOX}, "exactly one of"),
    ({"svg": SVG}, "exactly one target"),
    ({"svg": SVG, "box": BOX, "placeholder": MARKER}, "exactly one target"),
    ({"svg": SVG, "box": BOX, "fit": "cover"}, "placeholder targets only"),
    ({"svg": SVG, "box": {"x": 1, "y": 1, "width": 0, "height": 5}}, "positive"),
    ({"svg": SVG, "box": {"x": 1}}, "in points"),
])
async def test_bad_arguments_are_refused(fake, gcs, kwargs, message):
    with pytest.raises(ValueError, match=message):
        await place_image(DECK, slide_id=SLIDE, **kwargs)
    assert gcs.uploads == [] and fake.batches == []
