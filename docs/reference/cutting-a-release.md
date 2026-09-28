---
title: Cutting a release
summary: the ordered steps that get a version onto GitHub Releases and PyPI without the tag-triggered workflow failing
verified: 2026-09-28
---

# Cutting a release

The release workflow runs only on a pushed `vX.Y.Z` tag, and it checks
several files at once. Doing these in order avoids a failed release and a
second tag.

1. **Bump `version` in `pyproject.toml`** to `X.Y.Z`. The workflow compares it
   with the tag and stops with
   `Tag (X.Y.Z) != pyproject.toml (…). Bump pyproject.toml before tagging.`
2. **Write `releases/vX.Y.Z.md`.** Hand-written; it becomes the GitHub Release
   body verbatim. Without it the `release` job fails with
   `Missing releases/vX.Y.Z.md`.
3. **Run the two CI gates locally:**

   ```sh
   uv run pytest tests/unit/ -q
   uv run ruff check src/ tests/
   ```

   Ruff runs with `W` rules on, so trailing whitespace fails the build. The
   v2.1.0 release needed a follow-up commit for exactly that.
4. **Update `README.md`** if the tool surface or behaviour changed; agents and
   users read it as the manual.
5. **Commit, tag `vX.Y.Z`, push the tag.** PyPI upload uses
   `skip-existing: true`, so re-running a failed workflow for the same version
   will not double-publish.

The workflow never runs on an ordinary push to `main`, so steps 1 to 3 are the
only checks a change gets before release.
