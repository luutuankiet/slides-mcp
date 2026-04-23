"""Unit tests for the icons registry + list/get API."""
from __future__ import annotations

import pytest

from slides_mcp import icons as icons_mod


def test_registry_has_canonical_icons():
    names = set(icons_mod.all_icon_names())
    # Spot-check a representative sample across categories.
    must_have = {
        "arrow-right", "arrow-left", "arrow-up", "arrow-down",
        "plus", "minus", "multiply", "star", "heart", "bolt",
        "sun", "moon", "cloud", "smiley",
        "circle", "square", "triangle", "diamond", "hexagon",
        "chart-up", "chart-down", "target", "bullseye", "stack",
    }
    missing = must_have - names
    assert not missing, f"missing canonical icons: {sorted(missing)}"


def test_list_icons_unfiltered_returns_all():
    out = icons_mod.list_icons()
    names = [e["name"] for e in out]
    assert "arrow-right" in names
    assert len(names) == len(icons_mod.all_icon_names())
    # Sorted by (category, name)
    sorted_out = sorted(out, key=lambda x: (x["category"], x["name"]))
    assert out == sorted_out


def test_list_icons_filter_by_category():
    arrows = icons_mod.list_icons("arrows")
    assert all(e["category"] == "arrows" for e in arrows)
    assert "arrow-right" in [e["name"] for e in arrows]
    assert "star" not in [e["name"] for e in arrows]


def test_list_icons_filter_by_keyword():
    growth = icons_mod.list_icons("growth")
    names = [e["name"] for e in growth]
    assert "arrow-up" in names  # keyword 'growth'
    assert "chart-up" in names  # keyword 'growth'


def test_list_icons_filter_by_name_substring():
    charts = icons_mod.list_icons("chart")
    names = [e["name"] for e in charts]
    assert "chart-up" in names
    assert "chart-down" in names


def test_list_icons_empty_filter_returns_empty_only_for_nomatch():
    # non-matching needle returns empty list
    out = icons_mod.list_icons("zzz_nonexistent_zzz")
    assert out == []


def test_get_icon_spec_single_shape():
    spec = icons_mod.get_icon_spec("arrow-right")
    assert spec["category"] == "arrows"
    assert len(spec["shapes"]) == 1
    assert spec["shapes"][0]["type"] == "RIGHT_ARROW"


def test_get_icon_spec_composed_chart_up():
    spec = icons_mod.get_icon_spec("chart-up")
    # 4 rising rectangles
    assert len(spec["shapes"]) == 4
    assert all(s["type"] == "RECTANGLE" for s in spec["shapes"])
    # Heights should rise left-to-right (smaller top → taller bar)
    tops = [s["at"][1] for s in spec["shapes"]]
    assert tops == sorted(tops, reverse=True), f"chart-up bars not ascending: {tops}"


def test_get_icon_spec_composed_bullseye():
    spec = icons_mod.get_icon_spec("bullseye")
    assert len(spec["shapes"]) == 4  # alternating fill/white for ring effect
    assert all(s["type"] == "ELLIPSE" for s in spec["shapes"])
    # Alternating shapes carry fill_hex=#FFFFFF overrides for the "white" rings
    overrides = [s.get("fill_hex") for s in spec["shapes"]]
    assert overrides.count("#FFFFFF") == 2


def test_get_icon_spec_target_has_ring_override():
    spec = icons_mod.get_icon_spec("target")
    assert len(spec["shapes"]) == 3
    assert spec["shapes"][1].get("fill_hex") == "#FFFFFF"  # middle ring contrast


def test_get_icon_spec_unknown_raises():
    with pytest.raises(KeyError, match="unknown icon"):
        icons_mod.get_icon_spec("rocket-ship-42")


def test_every_icon_has_at_least_one_shape():
    for name in icons_mod.all_icon_names():
        spec = icons_mod.get_icon_spec(name)
        assert spec.get("shapes"), f"icon '{name}' has no shapes"
        for s in spec["shapes"]:
            assert "type" in s, f"icon '{name}' shape missing type"
            at = s.get("at") or [0, 0, 1, 1]
            assert len(at) == 4
            # relative coords within [0, 1.01] — tolerate tiny rounding
            for v in at:
                assert -0.01 <= v <= 1.01, (
                    f"icon '{name}' has out-of-range rel coord: {v}"
                )
