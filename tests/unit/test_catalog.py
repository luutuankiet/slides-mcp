"""Core sanity tests for catalog.py (Scope D)."""
from __future__ import annotations

from pathlib import Path

import pytest

from slides_mcp import catalog


def _brief() -> dict:
    return {
        "version": 1,
        "palette": {
            "surface": "#0F1A4A", "accent": "#E8612E", "text": "#000000",
            "category_set": ["#E8612E", "#0F1A4A"],
        },
        "shape_language": "sharp", "numbering_style": "bold",
        "tone": "x", "image_prompt_style": "y",
    }


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SLIDES_MCP_CATALOG_DIR", str(tmp_path))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    return tmp_path


def test_roundtrip_save_load_list_delete(_isolated):
    # Save two with mood tags
    saved_a = catalog.save_brief(_brief(), name="Warm one", mood_keywords=["warm"])
    catalog.save_brief(_brief(), name="Cool one", brief_id="cool", mood_keywords=["cool"])
    # List with mood filter
    warm = catalog.list_briefs(mood="warm")
    assert len(warm) == 1 and warm[0]["id"] == saved_a["id"]
    # Load full envelope back
    loaded = catalog.load_brief(saved_a["id"])
    assert loaded["brief"] == _brief()
    # Delete removes it
    assert catalog.delete_brief(saved_a["id"]) is True
    assert len(catalog.list_briefs()) == 1


def test_collision_requires_overwrite():
    catalog.save_brief(_brief(), name="Dup", brief_id="dup")
    with pytest.raises(FileExistsError):
        catalog.save_brief(_brief(), name="Dup", brief_id="dup")
    catalog.save_brief(_brief(), name="Dup v2", brief_id="dup", overwrite=True)
    # Confirm replaced
    assert catalog.load_brief("dup")["name"] == "Dup v2"
