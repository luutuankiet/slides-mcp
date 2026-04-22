"""Archetype registry.

Archetypes are layout templates that define the slot schema for a slide kind.
Bundled archetypes ship in src/slides_mcp/archetypes/ as YAML files.
Users can override by placing files in $SLIDES_MCP_ARCHETYPES_DIR.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

import yaml

_BUNDLED = Path(__file__).parent / "archetypes"


def archetype_search_paths() -> list[Path]:
    paths: list[Path] = []
    if env := os.environ.get("SLIDES_MCP_ARCHETYPES_DIR"):
        paths.append(Path(env))
    paths.append(_BUNDLED)
    return paths


@dataclass(frozen=True)
class Archetype:
    name: str
    description: str
    required_slots: tuple[str, ...]
    optional_slots: tuple[str, ...]
    slot_schema: dict[str, Any]
    geometry_defaults: dict[str, Any]
    theme_roles_used: tuple[str, ...]
    constraints: dict[str, Any] = field(default_factory=dict)


def _parse(raw: dict[str, Any], name: str) -> Archetype:
    slots = raw.get("slots") or {}
    return Archetype(
        name=raw.get("name", name),
        description=raw.get("description", ""),
        required_slots=tuple(slots.get("required") or ()),
        optional_slots=tuple(slots.get("optional") or ()),
        slot_schema=dict(raw.get("slot_schema") or {}),
        geometry_defaults=dict(raw.get("geometry_defaults") or {}),
        theme_roles_used=tuple(raw.get("theme_roles_used") or ()),
        constraints=dict(slots.get("constraints") or {}),
    )


@cache
def registry() -> dict[str, Archetype]:
    """Load all archetype YAML files into a name → Archetype map."""
    found: dict[str, Archetype] = {}
    for base in reversed(archetype_search_paths()):
        # reverse: bundled loaded first, user dir overrides
        if not base.exists():
            continue
        for f in sorted(base.glob("*.yaml")):
            raw = yaml.safe_load(f.read_text()) or {}
            a = _parse(raw, f.stem)
            found[a.name] = a
    return found


def get(name: str) -> Archetype:
    reg = registry()
    if name not in reg:
        raise KeyError(f"Unknown archetype '{name}'. Known: {sorted(reg)}")
    return reg[name]


def names() -> list[str]:
    return sorted(registry())
