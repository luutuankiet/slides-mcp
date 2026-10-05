# slides-mcp

An MCP server that lets an agent read Google Slides decks as context: outline a
deck, read slides at four detail levels, search them, render one as a PNG. Since
v2.1 it also has a narrow write wedge: a raw `batchUpdate` passthrough and a
section-footer tool, both returning the post-write deck state in the same
response. v2.2 adds Markdown speaker notes and `run_deck_script`, which runs
agent JavaScript against the deck in a sandboxed V8 worker process. Published to PyPI and run by MCP clients over stdio with `uvx`.
v2.4 adds `slides-mcp serve-http`: a stateless HTTP mode for Cloud Run where each
caller signs in with their own Google account.

## Hard constraints

- **Tool docstrings are what agents read.** FastMCP sends every `@mcp.tool()`
  docstring to the client on connect, so a docstring edit changes agent
  behaviour. Keep them true.
- **Writes are legwork, not authorship.** Full slide authoring was cut in
  v2.0.0; don't add creative-layout tools (see `docs/adr/`). Placing content
  the agent supplies at a position the deck defines is legwork (`docs/adr/0009`).
- **Every write goes through `writes.apply_batch`.** Destructive request kinds
  need `confirm_destructive=True`; convenience tools build requests and delegate
  to `exec_batch_update`, and deck scripts send each phase through the same
  function. It also refuses sizes under 1 pt (inches passed as EMU).
  `create_deck` is the one exception: it makes a new deck, not a batch.
- **Never commit credentials.** `token.json` and client secrets are gitignored.

## Layout

```
src/slides_mcp/server.py      — every MCP tool, slide selectors, write guard, post-state
src/slides_mcp/svg_raster.py  - place_image's SVG to PNG, rendered by resvg in a child process
src/slides_mcp/svg_native.py  - place_image(editable=true): a supported SVG subset as native shapes
src/slides_mcp/writes.py      — the one write path: apply_batch, destructive kinds, audit line
src/slides_mcp/scripting.py   — run_deck_script runtime: limits, worker handle, phases, warnings
src/slides_mcp/sandbox/       — V8 worker process and the JS prelude scripts see
src/slides_mcp/deck_model.py  — the deck snapshot scripts read
src/slides_mcp/skill_bundle.py — finds the shipped skill (wheel copy or checkout) for install_skill
src/slides_mcp/receipts.py    - thumbnails of the slides a write touched, attached to its reply
src/slides_mcp/layout.py      - text warnings after a write: new elements at Google's default size, stacked, off page
src/slides_mcp/notes_md.py    — Markdown to speaker-notes requests and back
src/slides_mcp/slides_api.py  — Slides REST wrapper, field masks, deck-id parsing
src/slides_mcp/normalize.py   — Slides API pageElement JSON → FlatShape
src/slides_mcp/classify.py    — topology-based archetype label per slide
src/slides_mcp/projection.py  — FlatShape → outline / summary / full / raw dicts
src/slides_mcp/auth.py        — token.json load and refresh; stdio vs HTTP mode; the HTTP caller's credentials
src/slides_mcp/http_mode.py   — serve-http: settings check, Google sign-in proxy, Firestore auth store
src/slides_mcp/state_store.py - short-lived state: dry-run plans and temporary images; memory or Firestore+GCS
src/slides_mcp/bootstrap.py   — `slides-mcp-auth` OAuth consent; cli.py is the entry point
skills/slides-mcp/SKILL.md    — shipped agent skill: composing batchUpdate requests; the wheel
                                bundles it as slides_mcp/_skills_data/ for install_skill
.claude-plugin/plugin.json    — plugin manifest that ships that skill
Dockerfile                    — the serve-http image Cloud Run builds; .gcloudignore/.dockerignore allowlist it
releases/vX.Y.Z.md            — hand-written release notes, required per tag
tests/unit/                   — pytest, no network
tests/fake_api.py             — fake Slides API over a scrubbed deck recording
tests/integration/            - Firestore state store against the emulator in Docker; not in CI
```

## Commands

```sh
uv sync                              # install with dev tools
uv run pytest tests/unit/ -q         # tests (CI gate)
uv run ruff check src/ tests/        # lint (CI gate)
uv run slides-mcp                    # start the stdio server
docker compose -f tests/integration/compose.yaml run --rm tests   # Firestore emulator tests (not CI)
```

`mypy` is a dev dependency but is not clean and CI does not run it.

<!-- Standard block. Everything above belongs to this project; everything below is
     the pointer every repo laid out this way carries. -->

## Documentation

Indexed in [docs/README.md](docs/README.md). Every page is self-contained — it
assumes you opened that one file and have nothing else loaded.

| where | what | read it |
|---|---|---|
| [architecture/](docs/architecture/) | where behaviour lives, one page per area | before going looking for something |
| [traps/](docs/traps/) | failure modes with no error message, indexed by symptom | before debugging something wrong but not crashing |
| [reference/](docs/reference/) | simply true, expensive to re-derive | when you need the detail |
| [adr/](docs/adr/) | why the repo is the way it is | before changing something that looks odd |

## Before you wrap up

Leave the repo holding what this session cost you to find out. Four rules.

1. **Sort it, and expect most of it to go nowhere.** A next action is an issue. A
   durable, expensive-to-re-derive fact is a page. A choice that was hard to
   reverse, surprising without context and a real trade-off is a decision record
   under `docs/adr/`. Status, dates, version pins and plans are none of those —
   delete them.
2. **A doc is the last resort.** Type error → test → comment at the site → doc.
   Name the single line you would have commented instead; if you can name it,
   comment it and stop.
3. **Verify against running code before writing, and date the page `verified:`.**
   Anything remembered from earlier in the session is stale until re-read. Deleting
   a draft because the problem is already fixed is a success.
4. **Append, never rewrite.** Supersede a merged decision record with a new one
   naming what it replaces. A trap filename is an identifier quoted elsewhere:
   edit the body, never the name.

Then run `scripts/gen-docs-index.sh`. Never hand-maintain an index.
