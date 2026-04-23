"""Core sanity for export_brief / import_brief (Scope E)."""
from __future__ import annotations

import pytest
import yaml

from slides_mcp import server as server_mod
from slides_mcp.server import export_brief, import_brief


def _brief() -> dict:
    return {
        "version": 1,
        "palette": {
            "surface": "#134E4A", "accent": "#B45309", "text": "#1F2937",
            "category_set": ["#B45309", "#134E4A"],
        },
        "shape_language": "sharp", "numbering_style": "outlined",
        "tone": "t", "image_prompt_style": "p",
    }


def test_roundtrip_export_import(monkeypatch):
    captured: list = []
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

    exported = export_brief("any")
    # YAML parses back to the brief exactly
    assert yaml.safe_load(exported["brief_yaml"]) == _brief()

    # Import the exported yaml string — brief gets re-committed
    import_brief("any", exported["brief_yaml"])
    assert captured[-1] == _brief()


def test_invalid_yaml_raises():
    with pytest.raises(ValueError, match="not valid YAML"):
        import_brief("any", "[[[nope: oops: :::]")
