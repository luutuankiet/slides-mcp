"""install-skill CLI — Install slides-mcp skill docs into a Claude Code skills directory.

Dispatched from slides_mcp.cli when the user runs `slides-mcp install [...]`.

Usage:
    slides-mcp install                   # install to $CWD/.claude/skills/slides-mcp/
    slides-mcp install --global          # install to ~/.claude/skills/slides-mcp/
    slides-mcp install --path DIR        # install to DIR/.claude/skills/slides-mcp/

Namespace: slides-mcp (never touches .claude/skills/<other>/).
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

NAMESPACE = "slides-mcp"


def _candidate_source_paths() -> list[Path]:
    """Paths where the packaged skills/ might live, in priority order."""
    here = Path(__file__).resolve().parent
    return [
        here / "_skills_data" / NAMESPACE,
        here.parent.parent / "skills" / NAMESPACE,
        here / "skills" / NAMESPACE,
    ]


def get_skills_source() -> Path:
    for candidate in _candidate_source_paths():
        if (candidate / "SKILL.md").exists():
            return candidate
    tried = "\n  ".join(str(c) for c in _candidate_source_paths())
    raise FileNotFoundError(
        f"Cannot find skills/{NAMESPACE}/ directory. Looked in:\n  {tried}\n"
        f"If you installed from PyPI, this is a packaging bug — please report."
    )


def get_target_dir(args: list[str]) -> Path:
    if "--global" in args:
        return Path.home() / ".claude" / "skills" / NAMESPACE
    if "--path" in args:
        idx = args.index("--path")
        if idx + 1 >= len(args):
            raise SystemExit("error: --path requires a directory argument")
        return (
            Path(args[idx + 1]).expanduser().resolve()
            / ".claude"
            / "skills"
            / NAMESPACE
        )
    return Path.cwd() / ".claude" / "skills" / NAMESPACE


def _copy_tree(src: Path, dst: Path) -> int:
    dst.mkdir(parents=True, exist_ok=True)
    count = 0
    for entry in sorted(src.iterdir()):
        dst_path = dst / entry.name
        if entry.is_dir():
            count += _copy_tree(entry, dst_path)
        else:
            shutil.copy2(entry, dst_path)
            count += 1
    return count


HELP_TEXT = f"""
Usage: slides-mcp install [options]

Install slides-mcp skill docs for Claude Code agents.
Namespace: {NAMESPACE} (only touches .claude/skills/{NAMESPACE}/; other skills untouched)

Options:
  --global          Install to ~/.claude/skills/{NAMESPACE}/
  --path DIR        Install to DIR/.claude/skills/{NAMESPACE}/
  (default)         Install to ./.claude/skills/{NAMESPACE}/
  -h, --help        Show this help

Installed files:
  SKILL.md                            Entry point — tool priority + workflow index
  rules/workflow.md                   Decision tree: what to use when
  rules/theme-coherence.md        NEW Cross-slide visual DNA via meta-slide brief (v0.3.0+)
  rules/visual-presentation.md    NEW Renderer-not-brand; shapes-first; structural variety (v0.3.0+)
  rules/generate-from-intent.md   NEW Prompt → slides workflow: plan → create → verify → iterate (v0.3.0+)
  rules/read-deck.md                  Outline, slide, search, list_slides_by
  rules/write-deck.md                 patch_slide: text, translation, _object_ids
  rules/theme-hygiene.md              audit_deck_colors + promote_to_theme
  rules/bidi-edit.md                  See-and-move: render_thumbnail + elements
  rules/escape-hatch.md               exec_batch_update safely: dry_run, denylist

Re-run after each slides-mcp upgrade — rules files evolve between versions.
Agents reading a stale install miss new workflow guidance.
""".strip()


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)

    if "-h" in args or "--help" in args:
        print(HELP_TEXT)
        return 0

    source = get_skills_source()
    target = get_target_dir(args)

    print()
    print("  Installing slides-mcp skill docs...")
    print(f"  Source: {source}")
    print(f"  Target: {target}")
    print()

    count = _copy_tree(source, target)

    print(f"  OK Installed {count} files to {target}")
    print(f"  Namespace: {NAMESPACE} (other skills unaffected)")
    print()

    if "--global" in args:
        print("  Skills installed globally. Available in all Claude Code sessions.")
    else:
        print("  Skills installed for this project.")
        print("  Commit .claude/skills/ to share with your team.")
    print()
    print("  Tip: re-run `slides-mcp install [--global]` after upgrading slides-mcp.")
    print("       Rules files evolve between versions; a stale install means agents")
    print("       skip new workflow guidance (e.g. theme-coherence, visual-presentation).")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
