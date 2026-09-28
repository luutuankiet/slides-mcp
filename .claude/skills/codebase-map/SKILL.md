---
name: codebase-map
description: Where behaviour lives in this repo — a per-area map of which files and line ranges own what, so you can open the right file instead of searching for it. Use before hunting for where something is implemented, before adding a feature that touches existing behaviour, and when a change seems to need edits in more places than expected.
---

# Where things live

This repo is large enough that finding the right file costs more than reading it.
The pages below are maps: for each area, which files own it and roughly where in
them.

**Pick the area, open that one page, then go straight to the file.** Do not read
every page — that defeats the point.

<!-- BEGIN GENERATED INDEX -- edit the pages, not this block -->

## Where things live

One page per area of the system. Read before going looking for where
something is implemented.

| page | covers | verified |
|---|---|---|
| [Auth and entry points](../../../docs/architecture/auth-and-entry-points.md) | where token.json is read from, how OAuth refresh and scopes work, what slides-mcp and slides-mcp-auth do on startup | 2026-09-28 |
| [Deck scripts (run_deck_script)](../../../docs/architecture/deck-scripts.md) | how an agent's JavaScript runs against a deck, the read model the script sees, the worker process and its timeouts, commit phases, dry run, what is refused before any API call, where errors and warnings come from | 2026-09-28 |
| [Packaging, release and the shipped skill](../../../docs/architecture/packaging-and-release.md) | how a version gets to PyPI and GitHub Releases, what the release workflow checks, where the agent skill that ships with the package lives | 2026-09-28 |
| [Read path (outline, read, search, thumbnail)](../../../docs/architecture/read-path.md) | how a deck URL becomes the slide dicts an agent sees, where detail modes, selectors, archetypes, titles and speaker notes come from | 2026-09-28 |
| [Write wedge (exec_batch_update, add_section_footers, write_speaker_notes)](../../../docs/architecture/write-wedge.md) | where writes to a deck happen, the destructive-request guard, dry run, the audit line, how post_state and affected_slide_ids are built, how section footers are placed, how Markdown notes become requests | 2026-09-28 |

## Guides

Long-form pages that belong to no single area.

| page | summary | verified |
|---|---|---|
| [Deploying the shared server to Cloud Run (draft)](../../../docs/deploying-to-cloud-run.md) | the Google Cloud setup a deployer does once before running slides-mcp as a shared remote server, with the Firestore database, service account access and settings it needs; draft until HTTP mode ships | 2026-09-28 |

<!-- END GENERATED INDEX -->

The same table, browsable, is [docs/README.md](../../../docs/README.md).

## How to read a line range

Every entry is `file — lines — what lives there`. **The line numbers are a
starting point, not an address.** They drift with every commit that touches the
file above them, and nothing regenerates them.

So: jump to roughly that line, then confirm you are in the right place by what the
code says, not by the number. If a range is off by more than a screen or two, fix
it in the page and re-date it — that is a one-line edit and it is how the map
stays worth having.

Line ranges are given at all because one file matters more than the rest:
`src/slides_mcp/server.py` is about 850 lines and holds all seven MCP tools, the
slide selector, the destructive-request guard and the post-state builder. "It's
in `server.py`" is not an answer.

## What this map does not tell you

It tells you **where**, not **why it is dangerous**. Several of the areas below
have failure modes that produce no error message. Those are catalogued
separately, by symptom, in the `repo-maintenance` skill and in
[docs/README.md](../../../docs/README.md). If you are about to change a tool
docstring (agents read it verbatim) or anything on the `exec_batch_update`
path, which every write goes through, look there first.

## Keeping it accurate

A map that lies is worse than none, because it sends people confidently to the
wrong file. Two habits keep it honest:

- **Re-date what you touch.** If you worked in an area and the page was right,
  bump `verified:`. If it was wrong, fix it and bump.
- **Add an area when you create one, not later.** A new subsystem with no page is
  invisible; the next person re-derives its shape from scratch.

To add a page, follow the same rules as any other doc — see the `repo-maintenance`
skill. Frontmatter for an area page is `title`, `covers` (what someone would be
looking for, phrased as they would phrase it), and `verified`. Then run
`scripts/gen-docs-index.sh`; the block above is generated and hand edits to it
are overwritten.
