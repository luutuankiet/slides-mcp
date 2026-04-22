# slides-mcp

An MCP server that lets Claude (and other agents) edit Google Slides through a compact YAML DSL. Built for multi-hundred-turn editing sessions without token bloat, with first-class brand-theme enforcement, presenter-note support, and a true bidirectional agent loop — the agent can **see** the rendered slide (native `ImageContent`) AND **move** shapes (coordinate-level writes) in the same session.

## Why this exists

Existing Google Slides MCP servers are thin JSON passthroughs over the Slides REST API. A single text edit costs ~2,000 tokens (1K to read + 1K to write), and a rendered slide costs another round-trip. That math kills 100-turn editing sessions.

This server projects each slide into ~100–150 tokens of structured YAML, keyed to a small vocabulary of **archetypes** (layout templates) and a **theme** (palette + fonts). Writes produce the updated DSL and (for geometry changes) a rendered thumbnail in a single call. Thumbnails return as native MCP `ImageContent` — no URL-fetch round-trip.

## Core ideas

- **Archetypes over layouts.** PowerPoint/Slides layout names are unreliable (one real deck used `CUSTOM_7_1_1_1_1_1_1_1_1_1_1_1_1_1_1_1` as the name for 11 structurally-different slides). The classifier reasons about element topology — column counts, separator lines, picture-to-text ratios — not layout strings.
- **Theme roles, not hex codes.** A slide references `palette.brand_accent`, not `#3366CC`. Change the theme once; the deck follows. Drift (hex values not in the theme) is surfaced by `audit_deck_colors` and can be accepted via `promote_to_theme`.
- **Presenter notes are first-class.** Read path preserves the full notes body on every slide (no truncation). Write-side emission is landing incrementally — current state is *detected and flagged* on diff; explicit emission is the next small task.
- **Bidi geometry.** Opt-in `include_elements=True` on `get_slide` returns `elements: [{id, at:[x,y,w,h]}]`. Editing those values produces `updatePageElementTransform` requests in `RELATIVE` mode, preserving scale and rotation on the underlying shape.
- **Privacy boundary.** Bundled theme + archetypes are generic. Your real brand theme lives in `~/.config/slides-mcp/themes/` and never enters the repo. Research artifacts, session state, and real client decks stay in local-only directories that are in `.gitignore`.

## What the MVP does

- Read a deck outline (1 call, whole deck, ~40 tok/slide) — `get_deck_outline`
- Read one slide as compact YAML (~100–150 tok typical) — `get_slide`
- Search a deck for text substrings — `search_deck`
- Patch a slide (text edits + translation of existing elements) in one call — `patch_slide`
- Render a slide as native `ImageContent` for visual verification — `render_thumbnail`
- Audit every color and font in the deck against the active theme — `audit_deck_colors`
- Promote a drift value to the theme as a named role — `promote_to_theme`
- Copy a deck via Drive — `clone_deck`
- List themes / archetypes / deck archetype inventory — `list_themes`, `list_archetypes`, `list_deck_layouts`
- Diagnostic: `auth_status`

## What the MVP does NOT do (yet)

By design, the current scope is tightly scoped to "edit an existing brownfield deck." The following are deliberately deferred — if you need them, watch the roadmap issues:

- **Creating slides from scratch** (no `distill_doc_to_deck`, no `create_from_markdown`)
- **Inserting new shapes or icons** via DSL (moving existing elements works; creating new ones doesn't)
- **Resizing / rotating** elements (warn-only in diff; translation-only writes in MVP)
- **Archetype swap** ("relayout this slide to 3 columns" is a Phase 2 primitive)
- **Template replacement map** (`clone_deck` copies only; per-slide text replacements are multiple calls today)
- **Writing presenter notes** (reads work; write emission is a known ~15-line follow-up)
- **Slide variant ideation** ("give me 3 alternative versions of this slide")
- **`.pptx` target** (Google Slides only in this version)

## Status

MVP shipped. 13 MCP tools, 45 unit tests, live-verified bidi loop on real decks (read → edit → render → move → re-render, all in token budget). Still pre-v1: no public releases cut, no committed production users, tests cover the core layers but integration tests against live decks are empty. Use at your own risk and expect rough edges — especially on the text-edit side where `replaceAllText` is currently slide-scoped.

## Installation

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/YOUR-USER/slides-mcp.git
cd slides-mcp
uv sync
```

## Auth setup (one-time)

The MCP uses user-OAuth to access Google Slides. You need a Google Cloud project with the Slides + Drive APIs enabled and a desktop OAuth client.

1. Create a Google Cloud project; enable `slides.googleapis.com` and `drive.googleapis.com`
2. Create an OAuth 2.0 client (type: **Desktop app**), download `client_secret.json`
3. On any machine with a browser, run:

    ```bash
    uv run slides-mcp-auth --client-secret /path/to/client_secret.json --out ./token.json
    ```

    This opens a browser, you consent, and `token.json` is written. The refresh token inside is long-lived.

4. If your MCP server runs on a headless host (devcontainer, VPS), copy `token.json` over, e.g. `scp token.json user@devbox:~/.config/slides-mcp/token.json`. Point the server at it via `$SLIDES_MCP_TOKEN_PATH`.

## MCP client configuration

Add to your MCP client config:

```json
{
  "mcpServers": {
    "slides": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/slides-mcp", "slides-mcp"],
      "env": {
        "SLIDES_MCP_TOKEN_PATH": "/path/to/token.json",
        "SLIDES_MCP_THEMES_DIR": "/path/to/your/private/themes"
      }
    }
  }
}
```

## Theme setup

The bundled theme at `src/slides_mcp/themes/example.yaml` is a generic placeholder. For your real brand, drop a theme file into one of these locations (first match wins):

1. `$SLIDES_MCP_THEMES_DIR`
2. `$XDG_CONFIG_HOME/slides-mcp/themes` (default: `~/.config/slides-mcp/themes`)
3. `./slides-mcp-themes` (project-local; add it to your own `.gitignore`)
4. Bundled `example.yaml` (fallback only)

Theme files are never committed to this repo. See `src/slides_mcp/themes/example.yaml` for the schema.

## MCP tool reference

| Tool | Purpose |
|------|---------|
| `list_themes()` | All theme files discoverable in the search paths |
| `list_archetypes()` | Archetype templates (bundled + any user overrides) |
| `list_deck_layouts(deck_url)` | Archetype inventory of a deck, with counts and slide IDs |
| `get_deck_outline(deck_url, theme?, sub_theme?)` | Compact index of all slides — one call for whole-deck reasoning |
| `get_slide(deck_url, slide_id, theme?, sub_theme?, mode?, include_elements?)` | Single slide as YAML; `mode='faithful'` preserves raw geometry; `include_elements=True` opts in to the geometry channel |
| `search_deck(deck_url, query)` | Find slides whose text contains `query` |
| `patch_slide(deck_url, slide_id, new_dsl_yaml, theme?, sub_theme?, verify?)` | Apply a DSL patch (text + element translation). Returns new YAML + auto-thumbnail when geometry changed |
| `render_thumbnail(deck_url, slide_id, size?)` | Rendered PNG as native MCP `ImageContent` |
| `render_thumbnail_url(deck_url, slide_id, size?)` | Same, but returns a URL (for non-agent callers) |
| `audit_deck_colors(deck_url, theme?, sub_theme?)` | Report colors and fonts not in the active theme, with nearest-role suggestions |
| `promote_to_theme(theme, sub_theme, role_name, kind, value)` | Add a drift value (color hex or font spec) to the user theme file under a named role |
| `clone_deck(src_url, new_title)` | Copy a deck via Drive; returns new deck ID + URL |
| `auth_status()` | Diagnostic — token path, scopes, expiry (no secrets) |

## Architecture

A four-layer design:

1. **Google Slides + auth** — REST wrapper with FieldMask-projected GETs + `batchUpdate`; token.json load + silent refresh
2. **DSL projection + diff** — `pageElement` → `FlatShape` → archetype classifier (topology-based) → compact YAML; YAML diff → `batchUpdate` requests
3. **Theme + archetype registry** — YAML-driven, user-overridable
4. **FastMCP tool surface** — 13 tools over stdio

See `src/slides_mcp/server.py` for the tool definitions and `src/slides_mcp/projection.py` for the compression core.

## Contributing

Early project. Issues welcome. PRs should include tests — the unit suite runs `pytest tests/unit/` in under a second and uses mocked Slides API JSON fixtures (no network).

## License

MIT. See [LICENSE](LICENSE).
