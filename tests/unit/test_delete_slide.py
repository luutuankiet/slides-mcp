"""Unit tests for the `delete_slide` MCP tool.

delete_slide is a thin bespoke wrapper over `slides_api.batch_update`
emitting a single `deleteObject` request. The tests verify:
- correct request shape passed to batch_update
- deck_id parsed from URL
- return payload structure
- API errors (missing slide, last-slide constraint) propagate
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from slides_mcp import server as server_mod
from slides_mcp import slides_api


def _call_tool(**kwargs):
    """Unwrap the FastMCP-registered tool to its underlying callable.

    FastMCP's @mcp.tool() decorator preserves the raw function on `.fn`.
    """
    fn = getattr(server_mod.delete_slide, "fn", server_mod.delete_slide)
    return fn(**kwargs)


def test_delete_slide_emits_single_deleteobject_request():
    """delete_slide emits exactly one deleteObject request for the given slide_id."""
    with patch.object(
        server_mod.slides_api, "batch_update", return_value={"replies": [{}]},
    ) as batch_mock:
        result = _call_tool(
            deck_url="https://docs.google.com/presentation/d/DECK123/edit",
            slide_id="s_abc",
        )
    assert batch_mock.call_count == 1
    args, _ = batch_mock.call_args
    deck_id_arg, requests_arg = args
    assert deck_id_arg == "DECK123"
    assert requests_arg == [{"deleteObject": {"objectId": "s_abc"}}]
    assert result["deck_id"] == "DECK123"
    assert result["slide_id"] == "s_abc"
    assert result["applied_request_count"] == 1
    assert result["status"] == "deleted"


def test_delete_slide_parses_raw_deck_id():
    """delete_slide accepts a raw deck ID (not just full URL) — matches other tools."""
    with patch.object(
        server_mod.slides_api, "batch_update", return_value={"replies": [{}]},
    ) as batch_mock:
        result = _call_tool(
            deck_url="RAW_DECK_ID_42",
            slide_id="s_xyz",
        )
    assert batch_mock.call_count == 1
    assert result["deck_id"] == "RAW_DECK_ID_42"


def test_delete_slide_propagates_api_errors():
    """If the Slides API refuses (missing slide, last-slide constraint, etc.),
    the SlidesApiError bubbles up without being swallowed."""
    err = slides_api.SlidesApiError(
        "Slides API error 400: The page cannot be deleted because it is the only slide",
        status=400,
    )
    with patch.object(server_mod.slides_api, "batch_update", side_effect=err):
        with pytest.raises(slides_api.SlidesApiError, match="only slide"):
            _call_tool(
                deck_url="https://docs.google.com/presentation/d/DECK123/edit",
                slide_id="s_last_remaining",
            )
