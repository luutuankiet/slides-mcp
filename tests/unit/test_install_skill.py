"""Tests for `install_skill` — the tool that hands the shipped skill to an agent.

Two seams: the tool's return value, and the built wheel (the wheel must carry
the skill folder, or installs from PyPI have nothing to serve).
"""
from __future__ import annotations

import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

from slides_mcp import __version__, auth
from slides_mcp.server import install_skill

REPO = Path(__file__).resolve().parents[2]
SKILL_SRC = REPO / "skills" / "slides-mcp"


def _repo_skill_files() -> dict[str, str]:
    return {
        p.relative_to(SKILL_SRC).as_posix(): p.read_text(encoding="utf-8")
        for p in SKILL_SRC.rglob("*")
        if p.is_file()
    }


def test_files_match_repo_skill_folder():
    out = install_skill()
    got = {f["path"]: f["content"] for f in out["files"]}
    assert got == _repo_skill_files()
    assert "SKILL.md" in got
    assert [f["path"] for f in out["files"]] == sorted(got)
    assert out["skill_name"] == "slides-mcp"


def test_version_and_source_url_match_server():
    out = install_skill()
    assert out["skill_version"] == __version__
    assert f"/tree/v{__version__}/skills/slides-mcp" in out["source_url"]


def test_install_locations_cover_known_agents_and_fallback():
    agents = {row["agent"] for row in install_skill()["install_locations"]}
    assert {"Claude Code", "Codex CLI", "Gemini CLI", "anything else"} <= agents
    assert install_skill()["instructions"]


def test_needs_no_credentials(monkeypatch, tmp_path):
    monkeypatch.setenv("SLIDES_MCP_TOKEN_PATH", str(tmp_path / "missing.json"))

    def boom(*_a, **_k):
        raise AssertionError("install_skill must not read credentials")

    monkeypatch.setattr(auth, "load_credentials", boom)
    assert install_skill()["files"]


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv not on PATH")
def test_wheel_bundles_skill(tmp_path):
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(tmp_path), str(REPO)],
        check=True,
        capture_output=True,
    )
    (wheel,) = tmp_path.glob("*.whl")
    prefix = "slides_mcp/_skills_data/slides-mcp/"
    with zipfile.ZipFile(wheel) as zf:
        bundled = [n for n in zf.namelist() if n.startswith(prefix) and not n.endswith("/")]
        assert prefix + "SKILL.md" in bundled
        for name in bundled:
            zf.read(name).decode("utf-8")
