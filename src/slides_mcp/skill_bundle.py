"""The agent skill that ships with the server, served by the `install_skill` tool.

The repo keeps the skill at `skills/slides-mcp/`; the wheel build copies it to
`slides_mcp/_skills_data/slides-mcp/` (see `force-include` in pyproject.toml).
Lookup tries the bundled copy first, then the source checkout.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from . import __version__

SKILL_NAME = "slides-mcp"
REPO_URL = "https://github.com/luutuankiet/slides-mcp"

INSTALL_LOCATIONS: list[dict[str, str]] = [
    {"agent": "Claude Code", "user": "~/.claude/skills/", "project": ".claude/skills/"},
    {"agent": "Codex CLI", "user": "~/.agents/skills/", "project": ".agents/skills/"},
    {
        "agent": "Gemini CLI",
        "user": "~/.gemini/skills/ (also reads ~/.agents/skills/)",
        "project": ".gemini/skills/ (also reads .agents/skills/)",
    },
    {
        "agent": "anything else",
        "user": "ask the user where skills go",
        "project": "ask the user",
    },
]

INSTRUCTIONS: list[str] = [
    "Identify which agent tool you are running in.",
    "Ask the user whether to install for their user account or the current project, "
    "and show the matching folder from install_locations. If your tool is not listed, "
    "ask the user where skills go.",
    f"If <root>/{SKILL_NAME}/ exists, delete it entirely.",
    f"Write every entry in files to <root>/{SKILL_NAME}/<path>, creating folders as needed.",
    "Tell the user what was installed, the version, and that a restart or new session "
    "may be needed before the skill is picked up.",
    "If you cannot write files, use files as reference for this conversation and tell "
    "the user the skill could not be saved.",
    "To update later, call this tool again after the server is upgraded.",
]


def _candidate_dirs() -> list[Path]:
    here = Path(__file__).resolve().parent
    return [
        here / "_skills_data" / SKILL_NAME,  # installed wheel
        here.parent.parent / "skills" / SKILL_NAME,  # source checkout: src/slides_mcp -> repo
    ]


def skill_dir() -> Path:
    for candidate in _candidate_dirs():
        if (candidate / "SKILL.md").is_file():
            return candidate
    tried = ", ".join(str(c) for c in _candidate_dirs())
    raise FileNotFoundError(
        f"The {SKILL_NAME} skill files are missing from this installation (looked in: "
        f"{tried}). This is a packaging bug; please report it at {REPO_URL}/issues."
    )


def _skipped(rel: Path) -> bool:
    return any(part.startswith(".") or part == "__pycache__" for part in rel.parts)


def skill_files(root: Path) -> list[dict[str, str]]:
    files = []
    for path in root.rglob("*"):
        rel = path.relative_to(root)
        if path.is_file() and not _skipped(rel):
            files.append({"path": rel.as_posix(), "content": path.read_text(encoding="utf-8")})
    return sorted(files, key=lambda f: f["path"])


def bundle() -> dict[str, Any]:
    return {
        "skill_name": SKILL_NAME,
        "skill_version": __version__,
        "source_url": f"{REPO_URL}/tree/v{__version__}/skills/{SKILL_NAME}",
        "files": skill_files(skill_dir()),
        "install_locations": INSTALL_LOCATIONS,
        "instructions": INSTRUCTIONS,
    }
