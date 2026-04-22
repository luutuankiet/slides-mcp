"""Color + font hygiene.

Two operations:
  - audit(deck_shapes, theme_sub) → drift report (colors/fonts not in theme)
  - promote_to_theme(theme_name, hex_or_font, role_name) → patches user theme file

The MCP tool layer exposes both. `audit` is read-only; `promote_to_theme` is the
only writer of theme YAMLs and always targets a user-writable location — never
the bundled `example.yaml`.
"""
from __future__ import annotations

import os
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .normalize import FlatShape, flatten
from .theme import SubTheme, theme_search_paths


@dataclass
class ColorDrift:
    hex_value: str
    count: int
    where: list[str] = field(default_factory=list)  # per-usage breadcrumbs
    nearest_role: str | None = None
    nearest_hex: str | None = None


@dataclass
class FontDrift:
    family: str
    size_pt: float
    count: int
    where: list[str] = field(default_factory=list)


@dataclass
class AuditReport:
    theme_name: str
    sub_theme_name: str
    color_drifts: list[ColorDrift] = field(default_factory=list)
    font_drifts: list[FontDrift] = field(default_factory=list)
    total_text_runs: int = 0
    total_shapes_with_fill: int = 0


def _color_distance(a: str, b: str) -> int:
    """Absolute sum of RGB channel differences. 0 = same, 765 = max."""
    a = a.lstrip("#")
    b = b.lstrip("#")
    try:
        ar, ag, ab = int(a[0:2], 16), int(a[2:4], 16), int(a[4:6], 16)
        br, bg, bb = int(b[0:2], 16), int(b[2:4], 16), int(b[4:6], 16)
        return abs(ar - br) + abs(ag - bg) + abs(ab - bb)
    except ValueError:
        return 999


def _nearest_role(hex_value: str, sub: SubTheme) -> tuple[str | None, str | None]:
    if not sub.palette:
        return None, None
    best_role, best_hex, best_d = None, None, 9999
    for role, v in sub.palette.items():
        d = _color_distance(hex_value, v)
        if d < best_d:
            best_role, best_hex, best_d = role, v, d
    return best_role, best_hex


def audit(shapes: list[FlatShape], sub: SubTheme, theme_name: str = "unknown") -> AuditReport:
    """Walk all shapes, collect every hex + (family, size) tuple, compare to sub theme."""
    report = AuditReport(theme_name=theme_name, sub_theme_name=sub.name)

    color_uses: dict[str, list[str]] = {}
    font_uses: dict[tuple[str, float], list[str]] = {}

    def visit(s: FlatShape, path: str) -> None:
        where = f"{path}#{s.object_id}"
        if s.fill_hex:
            report.total_shapes_with_fill += 1
            if sub.role_for_hex(s.fill_hex) is None:
                color_uses.setdefault(s.fill_hex.upper(), []).append(f"{where}:fill")
        if s.outline_hex and sub.role_for_hex(s.outline_hex) is None:
            color_uses.setdefault(s.outline_hex.upper(), []).append(f"{where}:outline")
        for i, run in enumerate(s.runs or []):
            report.total_text_runs += 1
            if run.color_hex and sub.role_for_hex(run.color_hex) is None:
                color_uses.setdefault(run.color_hex.upper(), []).append(f"{where}:text{i}")
            if run.font_family and run.size_pt:
                matched = any(
                    spec.family == run.font_family and abs(spec.size_pt - run.size_pt) < 0.5
                    for spec in sub.fonts.values()
                )
                if not matched:
                    font_uses.setdefault((run.font_family, run.size_pt), []).append(
                        f"{where}:text{i}",
                    )

    for s in flatten(shapes):
        visit(s, path="slide")

    for hex_value, where in color_uses.items():
        role, nearest = _nearest_role(hex_value, sub)
        report.color_drifts.append(ColorDrift(
            hex_value=hex_value, count=len(where), where=where,
            nearest_role=role, nearest_hex=nearest,
        ))
    report.color_drifts.sort(key=lambda d: -d.count)

    for (family, size), where in font_uses.items():
        report.font_drifts.append(FontDrift(
            family=family, size_pt=size, count=len(where), where=where,
        ))
    report.font_drifts.sort(key=lambda d: -d.count)

    return report


def audit_deck(
    slide_shape_lists: list[tuple[str, list[FlatShape]]],
    sub: SubTheme,
    theme_name: str = "unknown",
) -> AuditReport:
    """Aggregate audit across many slides. Returns one combined AuditReport."""
    combined = AuditReport(theme_name=theme_name, sub_theme_name=sub.name)
    color_totals: Counter[str] = Counter()
    color_where: dict[str, list[str]] = {}
    font_totals: Counter[tuple[str, float]] = Counter()
    font_where: dict[tuple[str, float], list[str]] = {}

    for slide_id, shapes in slide_shape_lists:
        slide_report = audit(shapes, sub, theme_name)
        combined.total_text_runs += slide_report.total_text_runs
        combined.total_shapes_with_fill += slide_report.total_shapes_with_fill
        for cd in slide_report.color_drifts:
            color_totals[cd.hex_value] += cd.count
            color_where.setdefault(cd.hex_value, []).extend(
                f"{slide_id}/{w}" for w in cd.where
            )
        for fd in slide_report.font_drifts:
            color_key = (fd.family, fd.size_pt)
            font_totals[color_key] += fd.count
            font_where.setdefault(color_key, []).extend(
                f"{slide_id}/{w}" for w in fd.where
            )

    for hex_value, count in color_totals.most_common():
        role, nearest = _nearest_role(hex_value, sub)
        combined.color_drifts.append(ColorDrift(
            hex_value=hex_value, count=count, where=color_where[hex_value],
            nearest_role=role, nearest_hex=nearest,
        ))
    for (family, size), count in font_totals.most_common():
        combined.font_drifts.append(FontDrift(
            family=family, size_pt=size, count=count, where=font_where[(family, size)],
        ))

    return combined


def user_theme_dir() -> Path:
    """Where promote_to_theme writes. Prefers env var, then XDG config."""
    if env := os.environ.get("SLIDES_MCP_THEMES_DIR"):
        return Path(env).expanduser()
    xdg = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(xdg) / "slides-mcp" / "themes"


def _find_theme_file(theme_name: str) -> Path | None:
    """Find an existing writable theme file. Never returns the bundled example."""
    for base in theme_search_paths():
        candidate = base / f"{theme_name}.yaml"
        if candidate.exists() and "src/slides_mcp/themes" not in str(candidate):
            return candidate
    return None


def promote_color_to_theme(
    theme_name: str, sub_theme: str, role_name: str, hex_value: str,
) -> Path:
    """Add a hex under palette.<role_name> in the user's theme file.
    Creates the file if it doesn't exist. Never touches the bundled example.
    """
    target = _find_theme_file(theme_name)
    if target is None:
        target = user_theme_dir() / f"{theme_name}.yaml"
        target.parent.mkdir(parents=True, exist_ok=True)
        skeleton = {
            "name": theme_name,
            "description": "User-promoted theme (created by slides-mcp on first promotion)",
            "sub_themes": {sub_theme: {"palette": {}, "fonts": {}}},
        }
        target.write_text(yaml.safe_dump(skeleton, sort_keys=False))

    raw = yaml.safe_load(target.read_text()) or {}
    raw.setdefault("sub_themes", {}).setdefault(sub_theme, {}).setdefault("palette", {})
    raw["sub_themes"][sub_theme]["palette"][role_name] = hex_value.upper()
    target.write_text(yaml.safe_dump(raw, sort_keys=False))
    return target


def promote_font_to_theme(
    theme_name: str, sub_theme: str, role_name: str,
    family: str, size_pt: float, weight: int = 400, color_role: str | None = None,
) -> Path:
    """Add a font spec under fonts.<role_name> in the user's theme file."""
    target = _find_theme_file(theme_name)
    if target is None:
        target = user_theme_dir() / f"{theme_name}.yaml"
        target.parent.mkdir(parents=True, exist_ok=True)
        skeleton: dict[str, Any] = {
            "name": theme_name,
            "description": "User-promoted theme (created by slides-mcp on first promotion)",
            "sub_themes": {sub_theme: {"palette": {}, "fonts": {}}},
        }
        target.write_text(yaml.safe_dump(skeleton, sort_keys=False))

    raw = yaml.safe_load(target.read_text()) or {}
    font_spec: dict[str, Any] = {"family": family, "size_pt": size_pt, "weight": weight}
    if color_role:
        font_spec["color_role"] = color_role
    raw.setdefault("sub_themes", {}).setdefault(sub_theme, {}).setdefault("fonts", {})
    raw["sub_themes"][sub_theme]["fonts"][role_name] = font_spec
    target.write_text(yaml.safe_dump(raw, sort_keys=False))
    return target
