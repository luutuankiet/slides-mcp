"""SVG to PNG for place_image, rendered by resvg in a child process.

A child process because resvg reports a missing font only as a log line on
file descriptor 1, which in stdio mode is the MCP transport and in any mode is
shared by every thread. The child points fd 1 at its stderr before rendering
and writes the PNG to the original stdout, so the parent reads both cleanly.
It also bounds a pathological SVG with a timeout.
"""
from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

PX_PER_PT = 3                 # 216 dpi: sharp on a 4K projector at full-slide size
MAX_PIXELS = 24_000_000       # Google refuses images over 25 megapixels
MAX_BYTES = 50 * 1000 * 1000  # and over 50 MB, with the same "too large" message
RENDER_TIMEOUT_S = 30

_SVG_NS = "{http://www.w3.org/2000/svg}"
_ERROR_MARK = "SLIDES_MCP_RENDER_ERROR "
_ROOT_TAG_RE = re.compile(r"<(?:[\w.-]+:)?svg\b[^>]*>")
_SIZE_ATTR_RE = re.compile(r"\s(?:width|height)\s*=\s*(?:\"[^\"]*\"|'[^']*')")
_NO_FONT_RE = re.compile(r"No match for (.+?) font-family")
_NO_GLYPH_RE = re.compile(r"No fonts with a (.+?) character were found")
_NUMBER_RE = re.compile(r"\s*([0-9.]+)\s*(px)?\s*$")


class SvgError(ValueError):
    """The SVG cannot be placed; the message says why, for the agent."""


@dataclass(frozen=True)
class Raster:
    png: bytes
    width_px: int
    height_px: int
    warnings: list[str] = field(default_factory=list)


def pixel_size(width_pt: float, height_pt: float) -> tuple[int, int]:
    """Pixels for a box in points: PX_PER_PT, scaled down to stay under MAX_PIXELS."""
    k = min(PX_PER_PT, math.sqrt(MAX_PIXELS / (width_pt * height_pt)))
    return max(1, math.floor(width_pt * k)), max(1, math.floor(height_pt * k))


def _length(value: str | None) -> float | None:
    m = _NUMBER_RE.match(value or "")
    return float(m.group(1)) if m else None


def prepare(svg: str, width_px: int, height_px: int) -> str:
    """The SVG with its root sized to the target pixels; refuses external references.

    The drawing is scaled into that size keeping its own aspect ratio and
    centred (the SVG default, `xMidYMid meet`), so the PNG has the box's
    aspect ratio whatever the SVG's is.
    """
    try:
        root = ET.fromstring(svg)
    except ET.ParseError as e:
        raise SvgError(f"The SVG is not well-formed XML: {e}.") from e
    if root.tag not in (f"{_SVG_NS}svg", "svg"):
        raise SvgError(f"The root element must be <svg>, not <{root.tag.split('}')[-1]}>.")
    if root.tag == "svg":
        raise SvgError('The root <svg> needs xmlns="http://www.w3.org/2000/svg".')
    external = sorted({v for el in root.iter() for k, v in el.attrib.items()
                       if k.endswith("href") and not v.strip().startswith(("#", "data:"))})
    if external:
        raise SvgError(
            f"The SVG references outside resources {external}. Only in-document "
            f"references (#id) and data: URIs are allowed; embed images as data: URIs.")
    view_box = root.get("viewBox")
    if not view_box:
        w, h = _length(root.get("width")), _length(root.get("height"))
        if not w or not h:
            raise SvgError("The root <svg> needs a viewBox, or a numeric width and height.")
        view_box = f"0 0 {w:g} {h:g}"
    m = _ROOT_TAG_RE.search(svg)
    assert m is not None  # the parser found an <svg> root
    tag = _SIZE_ATTR_RE.sub("", m.group(0)[:-1].rstrip("/"))
    if not root.get("viewBox"):
        tag += f' viewBox="{view_box}"'
    tag += f' width="{width_px}" height="{height_px}"'
    tag += "/>" if m.group(0).endswith("/>") else ">"
    return svg[:m.start()] + tag + svg[m.end():]


def _warnings(log: str) -> list[str]:
    out: list[str] = []
    for family in dict.fromkeys(_NO_FONT_RE.findall(log)):
        out.append(f"Text in font-family {family} did not render: no font on this server "
                   f"matches it. Use sans-serif, serif or monospace; if those fail too, "
                   f"the server has no fonts installed.")
    glyphs = list(dict.fromkeys(_NO_GLYPH_RE.findall(log)))
    if glyphs:
        out.append(f"No font on this server has the characters {glyphs}; they render as "
                   f"empty boxes (CJK is not covered).")
    return out


def rasterise(svg: str, width_pt: float, height_pt: float, *,
              system_fonts: bool = True) -> Raster:
    """PNG of `svg` for a box of `width_pt` x `height_pt` points.

    `system_fonts=False` renders with no fonts at all, as the stock slim
    container did; tests use it to check missing text is reported.
    """
    width_px, height_px = pixel_size(width_pt, height_pt)
    prepared = prepare(svg, width_px, height_px)
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "slides_mcp.svg_raster"],
            input=json.dumps({"svg": prepared, "system_fonts": system_fonts}).encode(),
            capture_output=True, timeout=RENDER_TIMEOUT_S, check=False)
    except subprocess.TimeoutExpired as e:
        raise SvgError(f"The SVG took longer than {RENDER_TIMEOUT_S}s to render; "
                       f"simplify it.") from e
    log = proc.stderr.decode("utf-8", "replace")
    if proc.returncode != 0:
        if _ERROR_MARK in log:
            msg = log.split(_ERROR_MARK, 1)[1].strip()
            raise SvgError(f"The SVG renderer refused it: {msg}")
        raise SvgError(f"The SVG renderer failed (exit {proc.returncode}): "
                       f"{log.strip()[-500:]}")
    if len(proc.stdout) > MAX_BYTES:
        raise SvgError(f"The rendered PNG is {len(proc.stdout)} bytes, over Google's "
                       f"50 MB limit; simplify the SVG or use a smaller box.")
    return Raster(proc.stdout, width_px, height_px, _warnings(log))


def _child() -> int:
    job = json.loads(sys.stdin.buffer.read())
    png_out = os.fdopen(os.dup(1), "wb")
    os.dup2(2, 1)  # resvg's log lines now land on stderr
    import resvg_py

    try:
        png = resvg_py.svg_to_bytes(svg_string=job["svg"], log_information=True,
                                    skip_system_fonts=not job["system_fonts"])
    except ValueError as e:
        sys.stderr.write(f"\n{_ERROR_MARK}{e}\n")
        return 2
    with png_out:
        png_out.write(png)
    return 0


if __name__ == "__main__":
    sys.exit(_child())
