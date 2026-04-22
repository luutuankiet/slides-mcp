from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from slides_mcp import server as server_mod
from slides_mcp.server import (
    _DESTRUCTIVE_KINDS,
    _append_audit,
    _request_kinds,
    _scan_destructive,
)

# ---------- _scan_destructive ----------

def test_scan_destructive_finds_every_kind_individually():
    for kind in _DESTRUCTIVE_KINDS:
        found = _scan_destructive([{kind: {"objectId": "x"}}])
        assert found == [kind]


def test_scan_destructive_ignores_safe_kinds():
    reqs = [
        {"updateTextStyle": {}},
        {"updateShapeProperties": {}},
        {"duplicateObject": {}},          # creates, not destructive
        {"insertText": {}},
        {"createShape": {}},
        {"updatePageElementTransform": {}},
        {"updateParagraphStyle": {}},
        {"replaceAllShapesWithImage": {}},  # replaces, but creates an image; not flagged
    ]
    assert _scan_destructive(reqs) == []


def test_scan_destructive_collects_multiple_in_order():
    reqs = [
        {"updateTextStyle": {}},
        {"deleteObject": {"objectId": "x"}},
        {"replaceAllText": {"containsText": {"text": "y"}, "replaceText": "z"}},
        {"deleteObject": {"objectId": "q"}},  # duplicate: deduped
    ]
    found = _scan_destructive(reqs)
    assert found == ["deleteObject", "replaceAllText"]


def test_scan_destructive_handles_non_dict_entries():
    # Defensive: caller may pass malformed data; we should not crash
    assert _scan_destructive([None, "garbage", {"deleteSlide": {}}]) == ["deleteSlide"]  # type: ignore[list-item]


# ---------- _request_kinds ----------

def test_request_kinds_flat_list():
    reqs = [
        {"updateTextStyle": {}},
        {"updateShapeProperties": {}},
        {"insertText": {}},
    ]
    assert _request_kinds(reqs) == ["updateTextStyle", "updateShapeProperties", "insertText"]


def test_request_kinds_ignores_non_dict():
    reqs = [{"updateTextStyle": {}}, None, {"insertText": {}}]  # type: ignore[list-item]
    assert _request_kinds(reqs) == ["updateTextStyle", "insertText"]


# ---------- _append_audit ----------

def test_append_audit_writes_jsonl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    _append_audit(
        deck_id="abc123",
        requests=[{"updateTextStyle": {}}, {"insertText": {}}],
        dry_run=False,
        applied_count=2,
    )
    log = tmp_path / "slides-mcp" / "audit.jsonl"
    assert log.exists()
    lines = log.read_text().strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["deck_id"] == "abc123"
    assert entry["request_count"] == 2
    assert entry["request_kinds"] == ["updateTextStyle", "insertText"]
    assert entry["dry_run"] is False
    assert entry["applied"] == 2
    assert entry["refused"] is False
    assert "ts" in entry


def test_append_audit_appends_not_overwrites(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    _append_audit("d1", [{"a": {}}], False, 1)
    _append_audit("d2", [{"b": {}}], True, 0)
    log = tmp_path / "slides-mcp" / "audit.jsonl"
    lines = log.read_text().strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["deck_id"] == "d1"
    assert json.loads(lines[1])["deck_id"] == "d2"


def test_append_audit_silent_on_oserror(monkeypatch: pytest.MonkeyPatch):
    # Point at a path that cannot be created (file-as-parent)
    monkeypatch.setenv("XDG_CONFIG_HOME", "/dev/null/not-a-dir")
    # Must not raise
    _append_audit("abc", [{"a": {}}], False, 1)


# ---------- exec_batch_update (the tool) ----------
# FastMCP's @mcp.tool() preserves the wrapped callable on `.fn`.

def _call_tool(**kwargs):
    fn = getattr(server_mod.exec_batch_update, "fn", server_mod.exec_batch_update)
    return fn(**kwargs)


def test_exec_batch_update_refuses_destructive_without_confirm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    with patch.object(server_mod.slides_api, "batch_update") as batch_mock:
        result = _call_tool(
            deck_url="https://docs.google.com/presentation/d/DECK123/edit",
            requests=[{"deleteObject": {"objectId": "x"}}],
        )
    assert batch_mock.call_count == 0
    assert result["refused"] is True
    assert "deleteObject" in result["destructive_kinds"]
    # Audit should record the refusal
    entry = json.loads((tmp_path / "slides-mcp" / "audit.jsonl").read_text().strip())
    assert entry["refused"] is True


def test_exec_batch_update_dry_run_returns_preview_without_firing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    with patch.object(server_mod.slides_api, "batch_update") as batch_mock:
        result = _call_tool(
            deck_url="https://docs.google.com/presentation/d/DECK123/edit",
            requests=[{"updateTextStyle": {}}, {"insertText": {}}],
            dry_run=True,
        )
    assert batch_mock.call_count == 0
    assert result["dry_run"] is True
    assert result["would_apply"] == 2
    assert "updateTextStyle" in result["request_kinds"]
    assert len(result["preview"]) == 2


def test_exec_batch_update_happy_path_fires_and_returns_replies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    fake_replies = [{"createShape": {"objectId": "newId1"}}]
    with patch.object(
        server_mod.slides_api, "batch_update", return_value={"replies": fake_replies},
    ) as batch_mock:
        result = _call_tool(
            deck_url="https://docs.google.com/presentation/d/DECK123/edit",
            requests=[{"createShape": {"objectId": "newId1", "shapeType": "RECTANGLE"}}],
        )
    assert batch_mock.call_count == 1
    assert result["applied_request_count"] == 1
    assert result["total_request_count"] == 1
    assert result["replies"] == fake_replies


def test_exec_batch_update_destructive_fires_when_confirmed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    with patch.object(
        server_mod.slides_api, "batch_update", return_value={"replies": [{}]},
    ) as batch_mock:
        result = _call_tool(
            deck_url="https://docs.google.com/presentation/d/DECK123/edit",
            requests=[{"deleteObject": {"objectId": "toDelete"}}],
            confirm_destructive=True,
        )
    assert batch_mock.call_count == 1
    assert result.get("refused") is not True
    assert result["applied_request_count"] == 1


def test_exec_batch_update_empty_request_list_rejected():
    with pytest.raises(ValueError, match="non-empty list"):
        _call_tool(
            deck_url="https://docs.google.com/presentation/d/DECK123/edit",
            requests=[],
        )
