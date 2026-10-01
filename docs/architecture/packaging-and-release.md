---
title: Packaging, release and the shipped skill
covers: how a version gets to PyPI and GitHub Releases, what the release workflow checks, where the agent skill that ships with the package lives
verified: 2026-10-01
---

# Packaging, release and the shipped skill

## Package

`pyproject.toml` builds a wheel with hatchling from `src/slides_mcp`. Runtime
dependencies are `fastmcp` 4 and the Google API client libraries. The `http`
extra adds the Firestore auth store that `slides-mcp serve-http` needs; stdio
installs never pull it in. The `Dockerfile` installs that extra from
`uv.lock`; `pytest`,
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

The wheel carries a copy of that folder at `slides_mcp/_skills_data/slides-mcp/`
through a hatch `force-include` in `pyproject.toml`; the repo folder is not
moved. The `install_skill` tool (`skill_bundle.py`) reads the bundled copy
first and falls back to the repo folder, so a source checkout needs no build.
It returns every file under the folder, so adding pages needs no code change.
`tests/unit/test_install_skill.py` builds a wheel and fails if the copy is
missing.

`fastmcp` is capped below 5. `server.py` writes two things fastmcp would
otherwise get wrong for this server, and a test guards each:

- it registers every tool with `description=fn.__doc__`, because fastmcp
  moves `Args:` into parameter schemas and drops `Returns:` from the
  description agents read (`test_tool_descriptions_keep_returns_sections`);
- it sets the private `mcp._mcp_server.notification_options.tools_changed`
  to `False`, so HTTP clients are not told the tool list can change
  (`test_http_does_not_advertise_list_changed`). stdio still reports `true`,
  which fastmcp hardcodes; that is harmless.

`http_mode.py` also writes the private `GoogleProvider._token_validator`.
A fastmcp major release may break any of these, so the cap moves only with
those tests passing.
