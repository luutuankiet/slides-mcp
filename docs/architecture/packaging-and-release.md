---
title: Packaging, release and the shipped skill
covers: how a version gets to PyPI and GitHub Releases, what the release workflow checks, where the agent skill that ships with the package lives
verified: 2026-09-28
---

# Packaging, release and the shipped skill

## Package

`pyproject.toml` builds a wheel with hatchling from `src/slides_mcp`. Runtime
dependencies are the MCP SDK and the Google API client libraries; `pytest`,
`pytest-asyncio`, `ruff` and `mypy` are in the `dev` dependency group.
`uv.lock` is committed, and CI installs with `uv sync --frozen`.

Users run it with `uvx slides-mcp@latest`; older write-heavy versions stay
installable by pinning, e.g. `uvx slides-mcp@0.11.0`.

## Release workflow

`.github/workflows/release.yml` runs only on a pushed tag matching `v*.*.*`.

| job | step | fails when |
|---|---|---|
| `build` | compare tag to `project.version` in `pyproject.toml` | they differ |
| `build` | `uv run pytest tests/unit/ -q` | any test fails |
| `build` | `uv run ruff check src/ tests/` | any lint error, including trailing whitespace |
| `build` | `uv build` | packaging breaks |
| `release` | look for `releases/<tag>.md` | the file is missing |
| `release` | `gh release create --notes-file releases/<tag>.md` | |
| `publish` | PyPI upload via OIDC trusted publishing, `skip-existing: true` | |

Nothing gates on push to `main`; the checks only run on a tag. Run the test
and lint commands locally before tagging. Release notes are written by hand
and used verbatim as the GitHub Release body; they are not generated from
commits.

## The shipped skill

`.claude-plugin/plugin.json` declares one skill, `skills/slides-mcp/`. Its
`SKILL.md` is the reference agents use to compose `exec_batch_update`
requests: request kinds, objectId discovery, EMU units, the `autofit: NONE`
ordering, worked examples. It is written for users of the server, not for
people working on this repository.

Tool docstrings in `server.py` remain the primary reference for the read
tools; the skill only covers composing write requests.
