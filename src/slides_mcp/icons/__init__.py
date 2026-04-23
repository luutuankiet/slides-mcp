"""Vanilla icon primitives composed from Slides API native shape types.

Probe result (v0.6.0): Slides API rejects inline SVG data URLs (400 "URL is
invalid") — bundled SVGs can't be embedded offline, and a CDN introduces an
external dep that contradicts the "vanilla primitives" vision. Pivoted per
user's pre-approved fallback: compose icons from Slides API shape primitives
(RIGHT_ARROW, STAR_5, HEART, LIGHTNING_BOLT, ELLIPSE, RECTANGLE, …).

Registry format (registry.yaml):

    version: 1
    icons:
      arrow-right:
        category: arrows
        keywords: [next, forward, proceed]
        shapes:
          - {type: RIGHT_ARROW, at: [0.0, 0.25, 1.0, 0.5]}

Each icon carries 1-N shapes. Shape `at` is RELATIVE (0..1) within the icon's
bounding box; the tool scales to absolute inches at emit time.

Key design properties:
  - Zero external dependencies (native Slides shape rendering)
  - Theme-color native (fill = brief.palette.accent by default)
  - Scales perfectly at any size (vector primitives)
  - Drop-in extensibility (edit YAML, no code changes)
"""
from __future__ import annotations

from functools import lru_cache
from importlib import resources
from typing import Any

import yaml


@lru_cache(maxsize=1)
def _load_registry() -> dict[str, Any]:
    """Load + cache the bundled icon registry YAML."""
    data = resources.files("slides_mcp.icons").joinpath("registry.yaml").read_text()
    return yaml.safe_load(data) or {}


def list_icons(filter_keyword: str | None = None) -> list[dict[str, Any]]:
    """Return the icon catalog, optionally filtered by keyword.

    Filter matches against icon name + keywords + category (case-insensitive
    substring). Sorted by (category, name). Empty/None filter returns full
    catalog.
    """
    registry = _load_registry()
    icons = registry.get("icons") or {}
    needle = (filter_keyword or "").lower().strip()
    out: list[dict[str, Any]] = []
    for name, spec in icons.items():
        haystack = " ".join([
            name.lower(),
            (spec.get("category") or "").lower(),
            " ".join(spec.get("keywords") or []).lower(),
        ])
        if needle and needle not in haystack:
            continue
        out.append({
            "name": name,
            "category": spec.get("category") or "uncategorized",
            "keywords": list(spec.get("keywords") or []),
        })
    out.sort(key=lambda x: (x["category"], x["name"]))
    return out


def get_icon_spec(name: str) -> dict[str, Any]:
    """Look up an icon's full spec (shapes list). Raises KeyError if unknown.

    Returned dict carries:
      category: str
      keywords: list[str]
      shapes: list[{type: str, at: [l,t,w,h] relative 0..1}]
    """
    registry = _load_registry()
    icons = registry.get("icons") or {}
    if name not in icons:
        known = sorted(icons)[:10]
        raise KeyError(
            f"unknown icon '{name}'; call list_icons() to browse."
            f" First 10 known: {known}"
        )
    return icons[name]


def all_icon_names() -> list[str]:
    """Sorted list of every known icon name."""
    return sorted((_load_registry().get("icons") or {}).keys())
