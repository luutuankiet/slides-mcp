"""Portable theme-brief catalog — the user's personal brief library.

Briefs live at `$XDG_CONFIG_HOME/slides-mcp/briefs/<id>.yaml` (default
`~/.config/slides-mcp/briefs/`). Each file carries:

  - catalog_schema_version
  - id (slug, unique within the catalog)
  - name (human-readable display name)
  - mood_keywords (list of free-form tags, e.g. ["editorial", "dark"])
  - created_at (ISO-8601 UTC timestamp)
  - brief (the full brief dict in the same shape set_theme_brief accepts)

This catalog is **100% user-owned**:
  - The bundled server NEVER ships briefs under this path.
  - The dir is discovered at runtime, not bundled into the wheel.
  - A user can delete the whole dir without losing any deck state — briefs
    that were committed to decks live in those decks' meta-slides.

Anchor (Decision R/S): the deck's meta-slide is the source of truth for an
active brief. The catalog is a SEPARATE channel for *library reuse* — save
an approved brief from deck A, use it on deck B. `set_theme_brief` is
always the bridge that writes a catalog brief into a deck.

Path override: set `SLIDES_MCP_CATALOG_DIR=/some/abs/path` to move the
catalog (useful for tests + shared machines). Otherwise honors
`XDG_CONFIG_HOME`, else falls back to `~/.config/slides-mcp/briefs/`.
"""
from __future__ import annotations

import datetime as _dt
import os
import re
import uuid
from pathlib import Path
from typing import Any

import yaml

_CATALOG_DIRNAME = "briefs"
_CATALOG_SCHEMA_VERSION: int = 1


def catalog_dir() -> Path:
    """Resolve the catalog directory, honoring overrides.

    Priority:
      1. $SLIDES_MCP_CATALOG_DIR (absolute or ~-expanded path)
      2. $XDG_CONFIG_HOME/slides-mcp/briefs/
      3. ~/.config/slides-mcp/briefs/
    """
    override = os.environ.get("SLIDES_MCP_CATALOG_DIR")
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg).expanduser() / "slides-mcp" / _CATALOG_DIRNAME
    return Path.home() / ".config" / "slides-mcp" / _CATALOG_DIRNAME


def _slugify(name: str) -> str:
    """name → filesystem-safe slug. Empty/garbage input → "brief"."""
    slug = re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")
    return slug or "brief"


def _now_iso() -> str:
    """UTC timestamp, second precision, ISO-8601 with `+00:00` suffix."""
    return (
        _dt.datetime.now(_dt.UTC)
        .replace(microsecond=0)
        .isoformat()
    )


def make_brief_id(
    name: str, existing_ids: set[str] | None = None
) -> str:
    """Produce a unique slug-style id from ``name``.

    Slugifies the name; if that collides with an existing id, suffixes
    ``_2``, ``_3`` up to ``_99``; after that, appends a short uuid hex.
    Callers that want to update an existing entry should pass the exact id
    instead of going through this helper.
    """
    base = _slugify(name)
    existing = existing_ids or set()
    if base not in existing:
        return base
    for i in range(2, 100):
        candidate = f"{base}_{i}"
        if candidate not in existing:
            return candidate
    return f"{base}_{uuid.uuid4().hex[:6]}"


def brief_path(brief_id: str) -> Path:
    """Absolute path where the given brief id lives on disk."""
    return catalog_dir() / f"{brief_id}.yaml"


def _existing_ids() -> set[str]:
    d = catalog_dir()
    if not d.exists():
        return set()
    return {p.stem for p in d.glob("*.yaml")}


def save_brief(
    brief: dict[str, Any],
    name: str,
    brief_id: str | None = None,
    mood_keywords: list[str] | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Persist a brief to the catalog. Atomic write.

    Args:
        brief: the brief dict to save (shape ``set_theme_brief`` accepts).
            Validation is the caller's responsibility — this function does not
            call ``validate_brief`` so the catalog can hold in-progress edits.
        name: human-readable display name.
        brief_id: explicit slug to use; auto-generated from ``name`` if omitted.
        mood_keywords: optional tags used by ``list_briefs(mood=...)``.
        overwrite: when False (default) and the id already exists on disk,
            raises ``FileExistsError``. When True, replaces in-place.

    Returns the metadata envelope that was written (including resolved
    ``id`` and ``path``).
    """
    d = catalog_dir()
    d.mkdir(parents=True, exist_ok=True)

    resolved_id = brief_id or make_brief_id(name, _existing_ids())
    # Normalise user-supplied ids too.
    resolved_id = _slugify(resolved_id)

    path = d / f"{resolved_id}.yaml"
    if path.exists() and not overwrite:
        raise FileExistsError(
            f"brief id {resolved_id!r} already exists at {path}; pass "
            "overwrite=True to replace"
        )

    envelope: dict[str, Any] = {
        "catalog_schema_version": _CATALOG_SCHEMA_VERSION,
        "id": resolved_id,
        "name": name,
        "mood_keywords": list(mood_keywords or []),
        "created_at": _now_iso(),
        "brief": brief,
    }

    # Atomic write: tempfile + rename.
    tmp = path.with_suffix(".yaml.tmp")
    tmp.write_text(
        yaml.safe_dump(envelope, sort_keys=False, allow_unicode=True)
    )
    tmp.replace(path)

    return {**envelope, "path": str(path)}


def load_brief(brief_id: str) -> dict[str, Any]:
    """Load a catalog entry. Returns the full envelope (not just the brief).

    Raises:
        FileNotFoundError if no entry with this id exists.
        ValueError if the file is malformed (missing ``brief`` key).
    """
    path = brief_path(brief_id)
    if not path.exists():
        raise FileNotFoundError(
            f"no catalog brief with id {brief_id!r} at {path}. "
            "Call list_catalog_briefs() to see available ids."
        )
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict) or "brief" not in data:
        raise ValueError(
            f"catalog file at {path} is malformed: missing `brief` key"
        )
    data["path"] = str(path)
    return data


def list_briefs(mood: str | None = None) -> list[dict[str, Any]]:
    """List catalog entries, optionally filtered by mood keyword.

    Case-insensitive substring match against each entry's ``mood_keywords``
    list. Returns metadata only (no ``brief`` field) to keep the list
    response small — callers fetch the full entry via ``load_brief``.

    Malformed YAML files in the catalog dir are silently skipped.
    """
    d = catalog_dir()
    if not d.exists():
        return []
    needle = (mood or "").strip().lower()
    entries: list[dict[str, Any]] = []
    for path in sorted(d.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text())
        except Exception:
            continue
        if not isinstance(data, dict) or "brief" not in data:
            continue
        tags = [str(t).lower() for t in (data.get("mood_keywords") or [])]
        if needle and not any(needle in t or t in needle for t in tags):
            continue
        entries.append({
            "id": data.get("id") or path.stem,
            "name": data.get("name") or path.stem,
            "mood_keywords": list(data.get("mood_keywords") or []),
            "created_at": data.get("created_at"),
            "path": str(path),
        })
    return entries


def delete_brief(brief_id: str) -> bool:
    """Delete a catalog entry. Returns True if deleted, False if absent."""
    path = brief_path(brief_id)
    if not path.exists():
        return False
    path.unlink()
    return True
