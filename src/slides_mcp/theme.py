"""Theme loader and resolution.

Resolution order (first match wins):
  1. $SLIDES_MCP_THEMES_DIR
  2. $XDG_CONFIG_HOME/slides-mcp/themes  (default: ~/.config/slides-mcp/themes)
  3. ./slides-mcp-themes (project-local, gitignored)
  4. Bundled src/slides_mcp/themes/ (fallback)

Themes are never committed to this repo. See README for setup.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

import yaml

_BUNDLED = Path(__file__).parent / "themes"


def theme_search_paths() -> list[Path]:
    paths: list[Path] = []
    if env := os.environ.get("SLIDES_MCP_THEMES_DIR"):
        paths.append(Path(env))
    xdg = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    paths.append(Path(xdg) / "slides-mcp" / "themes")
    paths.append(Path.cwd() / "slides-mcp-themes")
    paths.append(_BUNDLED)
    return paths


@dataclass(frozen=True)
class FontSpec:
    family: str
    size_pt: float
    weight: int = 400
    color_role: str | None = None


@dataclass(frozen=True)
class SubTheme:
    name: str
    palette: dict[str, str] = field(default_factory=dict)
    fonts: dict[str, FontSpec] = field(default_factory=dict)

    def resolve_color(self, role: str) -> str | None:
        """Follow palette.X or direct role name."""
        if role.startswith("palette."):
            role = role.split(".", 1)[1]
        return self.palette.get(role)

    def role_for_hex(self, hex_value: str) -> str | None:
        """Reverse lookup — is this hex in the palette under a named role?"""
        norm = hex_value.upper()
        for name, v in self.palette.items():
            if v.upper() == norm:
                return name
        return None


@dataclass(frozen=True)
class Theme:
    name: str
    description: str
    sub_themes: dict[str, SubTheme]
    slide_dimensions: dict[str, Any] = field(default_factory=dict)
    layout_tokens: dict[str, Any] = field(default_factory=dict)

    def sub(self, key: str) -> SubTheme:
        if key not in self.sub_themes:
            raise KeyError(f"Sub-theme '{key}' not in theme '{self.name}'. "
                           f"Known: {list(self.sub_themes)}")
        return self.sub_themes[key]


def _parse_font(raw: dict[str, Any]) -> FontSpec:
    return FontSpec(
        family=raw["family"],
        size_pt=float(raw["size_pt"]),
        weight=int(raw.get("weight", 400)),
        color_role=raw.get("color_ref") or raw.get("color_role"),
    )


def _parse_theme(raw: dict[str, Any]) -> Theme:
    sub_themes: dict[str, SubTheme] = {}
    for sub_name, sub_raw in (raw.get("sub_themes") or {}).items():
        fonts = {k: _parse_font(v) for k, v in (sub_raw.get("fonts") or {}).items()}
        sub_themes[sub_name] = SubTheme(
            name=sub_name,
            palette=dict(sub_raw.get("palette") or {}),
            fonts=fonts,
        )
    return Theme(
        name=raw.get("name", "unnamed"),
        description=raw.get("description", ""),
        sub_themes=sub_themes,
        slide_dimensions=dict(raw.get("slide_dimensions") or {}),
        layout_tokens=dict(raw.get("layout_tokens") or {}),
    )


@cache
def load_theme(name: str = "example") -> Theme:
    """Find and parse a theme YAML file by base name."""
    for base in theme_search_paths():
        candidate = base / f"{name}.yaml"
        if candidate.exists():
            raw = yaml.safe_load(candidate.read_text()) or {}
            return _parse_theme(raw)
    raise FileNotFoundError(
        f"Theme '{name}' not found in any of: {[str(p) for p in theme_search_paths()]}"
    )


def available_themes() -> list[str]:
    """List all theme base names discoverable in search paths (dedup by name)."""
    seen: set[str] = set()
    out: list[str] = []
    for base in theme_search_paths():
        if not base.exists():
            continue
        for f in sorted(base.glob("*.yaml")):
            if f.stem not in seen:
                seen.add(f.stem)
                out.append(f.stem)
    return out
