"""Tests for slides_api module pieces that don't require live auth."""
from __future__ import annotations

import pytest

from slides_mcp.slides_api import deck_id_from_url


def test_parse_deck_url():
    url = "https://docs.google.com/presentation/d/1abcDEF_ghi-jklMNO/edit#slide=id.p"
    assert deck_id_from_url(url) == "1abcDEF_ghi-jklMNO"


def test_parse_raw_id():
    assert deck_id_from_url("1abcDEF_ghi-jklMNO") == "1abcDEF_ghi-jklMNO"


def test_parse_drive_open_url():
    url = "https://drive.google.com/open?id=1abcDEF_ghi-jklMNO"
    assert deck_id_from_url(url) == "1abcDEF_ghi-jklMNO"


def test_parse_bad_input():
    with pytest.raises(ValueError):
        deck_id_from_url("")
    with pytest.raises(ValueError):
        deck_id_from_url("not a valid id!!!")
