"""Core sanity for catalog MCP tools (Scope D)."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from slides_mcp import server as server_mod
from slides_mcp.server import (
    list_catalog_briefs,
    save_brief_to_catalog,
    use_catalog_brief,
)


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


def test_save_list_use_full_flow(monkeypatch):
    captured: list = []

    # Wire the deck-side stubs
    monkeypatch.setattr(server_mod.slides_api, "deck_id_from_url", lambda u: "DECK")
    monkeypatch.setattr(server_mod, "_fetch_for_brief", lambda d: {"slides": []})
    monkeypatch.setattr(
        server_mod.theme_brief_mod, "find_meta_slide",
        lambda prez: {
            "slide_id": "meta", "marker_box_id": "m", "body_box_id": "b",
            "body_text": "\n---\n" + yaml.safe_dump(
                {"__slides_mcp_theme_brief": _brief()}, sort_keys=False
            ),
        },
    )
    monkeypatch.setattr(
        server_mod, "set_theme_brief",
        lambda url, b: captured.append(b) or {"action": "updated", "brief": b,
                                              "deck_id": "DECK", "slide_id": "meta"},
    )

    # Save from deck
    saved = save_brief_to_catalog(
        "any", name="Client warm", mood_keywords=["warm", "editorial"]
    )
    assert saved["id"] == "client_warm"
    assert saved["brief"] == _brief()

    # List returns it
    result = list_catalog_briefs(mood="warm")
    assert result["count"] == 1 and result["briefs"][0]["id"] == "client_warm"

    # Use applies it to a new deck
    applied = use_catalog_brief("any_other_deck", brief_id="client_warm")
    assert applied["brief"] == _brief()
    assert captured[-1] == _brief()
