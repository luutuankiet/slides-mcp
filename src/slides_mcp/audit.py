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


@dataclass
class OrphanBold:
    slide_id: str
    object_id: str | None
    run_index: int
    text_preview: str  # first 60 chars


@dataclass
class SizeCluster:
    size_pt: float
    count: int
    role_guess: str  # theme font role, "orphan" if <5% of runs, else "unknown"


@dataclass
class TypographyReport:
    theme_name: str
    sub_theme_name: str
    brief_applied: bool
    total_text_runs: int = 0
    total_text_shapes: int = 0
    dominant_font: str | None = None
    font_outliers: list[FontDrift] = field(default_factory=list)
    size_clusters: list[SizeCluster] = field(default_factory=list)
    orphan_bolds: list[OrphanBold] = field(default_factory=list)
    color_drifts_vs_brief: list[ColorDrift] = field(default_factory=list)


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


def _brief_palette_hexes(brief: dict[str, Any] | None) -> dict[str, str]:
    """Flatten brief.palette into {role: HEX} map for drift comparison.

    Returns {} when brief is None or has no palette. Accent/text/surface
    become 1-hex roles; category_set becomes role_idx entries.
    """
    if not brief:
        return {}
    palette = (brief.get("palette") or {}) if isinstance(brief, dict) else {}
    out: dict[str, str] = {}
    for role in ("accent", "text", "surface"):
        v = palette.get(role)
        if isinstance(v, str) and v.startswith("#") and len(v) == 7:
            out[role] = v.upper()
    cats = palette.get("category_set") or []
    if isinstance(cats, list):
        for i, hex_value in enumerate(cats):
            if isinstance(hex_value, str) and hex_value.startswith("#") and len(hex_value) == 7:
                out[f"category_{i}"] = hex_value.upper()
    return out


def _nearest_brief_role(
    hex_value: str, brief_hexes: dict[str, str],
) -> tuple[str | None, str | None, int]:
    """Nearest brief-palette role. Returns (role, hex, distance). Empty brief → (None, None, 9999)."""
    if not brief_hexes:
        return None, None, 9999
    best_role, best_hex, best_d = None, None, 9999
    for role, v in brief_hexes.items():
        d = _color_distance(hex_value, v)
        if d < best_d:
            best_role, best_hex, best_d = role, v, d
    return best_role, best_hex, best_d


def _theme_font_role_for_size(size_pt: float, sub: SubTheme) -> str:
    """Best theme font role match for a size. Returns role name or 'unknown'."""
    best_role = "unknown"
    best_d = 9999.0
    for role, spec in sub.fonts.items():
        d = abs(spec.size_pt - size_pt)
        if d < best_d:
            best_role, best_d = role, d
    # tolerance: within 1pt = role match; otherwise 'unknown'
    return best_role if best_d <= 1.0 else "unknown"


def audit_typography(
    slide_shape_lists: list[tuple[str, list[FlatShape]]],
    sub: SubTheme,
    brief: dict[str, Any] | None = None,
    theme_name: str = "unknown",
) -> TypographyReport:
    """Brownfield typography audit.

    Walks every text run across every slide and reports:
      - dominant font_family + outliers (families <10% of runs)
      - size clusters (within 0.5pt tolerance), labeled by theme font role
      - orphan bolds (bold runs in shapes where majority is non-bold)
      - color drift against the deck's theme brief (if provided)

    Orthogonal to `audit_deck`: that one reports drift vs THEME; this one
    reports typography structure + drift vs BRIEF. Both feed `restyle_slides`
    with actionable targets.
    """
    report = TypographyReport(
        theme_name=theme_name, sub_theme_name=sub.name,
        brief_applied=bool(brief and brief.get("palette")),
    )
    brief_hexes = _brief_palette_hexes(brief)

    family_totals: Counter[str] = Counter()
    family_where: dict[str, list[str]] = {}
    size_totals: Counter[float] = Counter()
    color_totals: Counter[str] = Counter()
    color_where: dict[str, list[str]] = {}
    total_runs = 0
    total_text_shapes = 0

    for slide_id, shapes in slide_shape_lists:
        for s in flatten(shapes):
            if s.kind != "text" or not s.runs:
                continue
            total_text_shapes += 1
            # bold distribution within this shape (for orphan detection)
            bold_flags = [bool(r.bold) for r in s.runs]
            shape_mostly_plain = bold_flags.count(True) < len(bold_flags) / 2
            for i, r in enumerate(s.runs):
                total_runs += 1
                if r.font_family:
                    family_totals[r.font_family] += 1
                    family_where.setdefault(r.font_family, []).append(
                        f"{slide_id}/{s.object_id}:text{i}"
                    )
                if r.size_pt:
                    # bucket to nearest 0.5pt to collapse float noise
                    bucketed = round(float(r.size_pt) * 2) / 2
                    size_totals[bucketed] += 1
                if r.bold and shape_mostly_plain and len(s.runs) >= 2:
                    report.orphan_bolds.append(OrphanBold(
                        slide_id=slide_id,
                        object_id=s.object_id,
                        run_index=i,
                        text_preview=(r.content or "")[:60],
                    ))
                if r.color_hex and brief_hexes:
                    nearest_role, nearest_hex, d = _nearest_brief_role(
                        r.color_hex, brief_hexes,
                    )
                    # drift threshold: >60 RGB-sum distance from nearest brief role
                    if d > 60:
                        hex_up = r.color_hex.upper()
                        color_totals[hex_up] += 1
                        color_where.setdefault(hex_up, []).append(
                            f"{slide_id}/{s.object_id}:text{i}"
                        )

    report.total_text_runs = total_runs
    report.total_text_shapes = total_text_shapes

    # dominant font + outliers
    if family_totals:
        dominant_family, dominant_count = family_totals.most_common(1)[0]
        report.dominant_font = dominant_family
        threshold = max(1, total_runs // 10)  # <10% of runs is outlier
        for family, count in family_totals.most_common():
            if family == dominant_family:
                continue
            if count < threshold or family != dominant_family:
                # First-size match for representative size_pt; 0.0 if unknown
                sample_size = 0.0
                for _sid, shapes in slide_shape_lists:
                    found = False
                    for s in flatten(shapes):
                        if s.kind != "text" or not s.runs:
                            continue
                        for r in s.runs:
                            if r.font_family == family and r.size_pt:
                                sample_size = float(r.size_pt)
                                found = True
                                break
                        if found:
                            break
                    if found:
                        break
                report.font_outliers.append(FontDrift(
                    family=family, size_pt=sample_size, count=count,
                    where=family_where.get(family, []),
                ))

    # size clusters (sorted by count desc). Orphan = <5% share of total runs.
    for size_pt, count in size_totals.most_common():
        role_guess = _theme_font_role_for_size(size_pt, sub)
        if total_runs > 0 and (count / total_runs) < 0.05:
            role_guess = "orphan"
        report.size_clusters.append(SizeCluster(
            size_pt=size_pt, count=count, role_guess=role_guess,
        ))

    # color drift vs brief (sorted by count desc)
    for hex_value, count in color_totals.most_common():
        role, nearest, _d = _nearest_brief_role(hex_value, brief_hexes)
        report.color_drifts_vs_brief.append(ColorDrift(
            hex_value=hex_value, count=count, where=color_where[hex_value],
            nearest_role=role, nearest_hex=nearest,
        ))

    return report


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
