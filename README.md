# slides-mcp

An MCP server that lets Claude (and other agents) turn prompts into finished Google Slides decks — and edit existing ones — through a compact YAML DSL. Built for multi-hundred-turn editing sessions without token bloat, with first-class brand-theme enforcement, presenter-note support, and a true bidirectional agent loop.

The agent can **see** the rendered slide (native `ImageContent`), **edit** text and shape positions at coordinate level, **compose** new slides from archetype templates, **choose per-slide visual identity** via content-driven color palettes, and **commit a deck-level theme brief** (hidden meta-slide carrying palette + tone + shape language) that every subsequent `create_slide` call resolves from — one commitment, coherent deck — all in the same session.

## Quick mental model

```mermaid
flowchart LR
    A[Agent<br/>Claude / other LLM] -- "MCP stdio" --> B[slides-mcp server]
    B -- "Google Slides REST v1<br/>+ Drive v3" --> C[(Your deck)]
    C -- "rendered PNG<br/>native ImageContent" --> A
    C -- "compact YAML DSL<br/>~100-150 tok/slide" --> A

    subgraph config["On-disk config — never committed"]
        T[theme.yaml<br/>palette + fonts]
        A2[archetypes/*.yaml<br/>layout templates]
        TK[token.json<br/>OAuth refresh]
    end
    config -. read at call time .-> B
```

Core idea: **a slide is compressed into ~100-150 tokens of structured YAML keyed to (a) an archetype — what KIND of slide this is — and (b) a theme — what colors and fonts the brand uses.** The agent reasons in that compact space; the server translates to Google Slides `batchUpdate` requests.

## Why this exists

Thin JSON-passthrough Google Slides MCPs cost ~2 KB per edit (1 KB to read + 1 KB to write). A 100-turn editing session dies of token exhaustion. This server projects each slide into ~100-150 tokens and returns rendered thumbnails as native MCP `ImageContent` so the agent can visually verify in one call instead of fetching a URL.

## The three YAMLs (and one DSL)

### 1. `theme.yaml` — your brand

```yaml
name: mybrand
sub_themes:
  primary:
    palette:
      brand_accent:  "#3366CC"
      text_body:     "#333333"
      surface_card:  "#F3F3F3"
    fonts:
      display:      {family: "Inter", size_pt: 36, weight: 700}
      body:         {family: "Inter", size_pt: 18, weight: 400}
      pill_header:  {family: "Inter", size_pt: 22, weight: 600}
```

Palette keys are **roles**, not hex codes. DSL references `palette.brand_accent`; change the theme once and every slide follows. Multiple `sub_themes` let one brand carry multiple moods (e.g. `primary` vs `google_cobrand`).

**Where it lives (resolution order, first match wins):**

1. `$SLIDES_MCP_THEMES_DIR`
2. `$XDG_CONFIG_HOME/slides-mcp/themes` (default `~/.config/slides-mcp/themes`)
3. `./slides-mcp-themes` (project-local, add it to your own `.gitignore`)
4. Bundled `src/slides_mcp/themes/example.yaml` (fallback)

Your real brand theme stays outside the repo. The bundled `example.yaml` is a generic placeholder.

### 2. `archetype.yaml` — what KIND of slide

```yaml
# 3col_pill_cards.yaml — three parallel ideas with colored pill headers
name: 3col_pill_cards
slots:
  required: [title, columns]
  optional: [lead]
  constraints: {columns_count: 3}

geometry_defaults:
  title:     {left_in: 0.9, top_in: 0.6, w_in: 14.4, h_in: 0.9, font_role: display}
  lead:      {left_in: 0.9, top_in: 1.8, w_in: 14.4, h_in: 1.5, font_role: body}
  column_1:  {left_in: 0.9,  top_in: 4.0, w_in: 4.6, h_in: 4.5}
  column_2:  {left_in: 5.7,  top_in: 4.0, w_in: 4.6, h_in: 4.5}
  column_3:  {left_in: 10.5, top_in: 4.0, w_in: 4.6, h_in: 4.5}
  pill:         {font_role: pill_header, color_role: brand_accent}
  title_accent: {h_in: 0.08, w_in: 2.4, top_offset_in: 1.0}
  column_dot:   {r_in: 0.15}
```

Archetypes describe layouts in **inches against a 16×9 reference deck**. The server **auto-scales at runtime** to your actual deck size (Google default is 10×5.625, legacy widescreen is 13.33×7.5 — all 16:9 aspect). Authors of new archetypes do not need to worry about deck-size variants.

Nine archetypes ship: `text_heavy_body`, `cover_with_hero`, `3col_pill_cards`, `4col_numbered_flow`, `4col_card_with_image`, `text_left_image_right`, `table_slide`, `logo_strip`, `generic_layout`. **Five have content builders** wired to `create_slide` in v0.3.0 (`text_heavy_body`, `cover_with_hero`, `3col_pill_cards`, `text_left_image_right`, `4col_numbered_flow` — see `supported_archetypes` in every `create_slide` response); the rest leave the slide blank and emit a warning for agent fallback to `create_shape` / `exec_batch_update`.

### 3. The DSL — how the agent reads and edits one slide

What `get_slide` returns (a `3col_pill_cards` slide, clean mode):

```yaml
id: sl_abc123
layout: 3col_pill_cards
mode: clean
title: Theme hygiene
lead: Brand drift is the default of a team deck. Surface it. Accept what belongs.
columns:
  - {pill: audit_deck_colors, body: "Walk every shape. Report off-theme colors..."}
  - {pill: promote_to_theme,  body: "Accept drift per role. Writes to user config..."}
  - {pill: Living theme,      body: "Theme is a doc, not a gatekeeper."}
notes: null
_object_ids: {title: t_title, paragraphs: [p_1, p_2]}
```

Edit this YAML, pass it back as `new_dsl_yaml` to `patch_slide`, and the server computes the minimum `batchUpdate` request list. Text edits, element moves, notes updates — one call.

**Clean mode** is the default, ~100-150 tok/slide. **Faithful mode** preserves raw per-element geometry for slides the classifier can't match to a known archetype.

## Plus one more: the theme brief (v0.3.0)

The theme + archetype + DSL trio covers *per-slide* identity. The theme brief covers *deck-level* coherence.

A brief is persisted as a **hidden meta-slide inside the deck** (`isSkipped=True`, title `__SLIDES_MCP_THEME_BRIEF__ — DO NOT DELETE`, body carries the YAML). The agent commits it once per deck from the user's intent; every subsequent `create_slide` call resolves unset visual fields from the brief.

```yaml
version: 1
palette:
  surface: "#0F1A4A"          # header bars, backgrounds
  accent:  "#E8612E"          # titles, dividers, highlights
  text:    "#000000"          # body text
  category_set:               # 3-5 hex for N-slot archetypes (pill cards, columns)
    - "#E8612E"
    - "#0F1A4A"
    - "#5A6B9A"
shape_language: "sharp"       # sharp | rounded | mixed
numbering_style: "bold"       # bold | outlined | dot | hidden
tone: "clean editorial"       # free-text — informs image prompts + copy register
image_prompt_style: "documentary photography, warm light"  # free-text
```

**Resolution order in every `create_slide`:** `per_slide_content > brief.palette.* > theme YAML > safety default`. Per-slide overrides still win (Decision O preserved); the brief is a *default*, not a gatekeeper.

Four tools — `get_theme_brief`, `set_theme_brief`, `update_theme_brief` (forward-only patch), and `extract_theme_brief` (brownfield audit: propose a brief from an existing deck's dominant palette + shape topology before committing). See `skills/slides-mcp/rules/theme-coherence.md` for the greenfield / brownfield / amendment workflows.

## Core flow — creating a slide from intent

```mermaid
sequenceDiagram
    participant Agent as Agent
    participant MCP as slides-mcp
    participant API as Google Slides API

    Agent->>MCP: create_slide(deck_url, archetype="3col_pill_cards",<br/>content={title, lead, pill_palette, columns})
    MCP->>API: get_presentation(fields=pageSize)
    API-->>MCP: pageSize = 10 × 5.625 in
    Note over MCP: compute sx=10/16, sy=5.625/9<br/>scale archetype geometry
    MCP->>API: batchUpdate([createSlide, createShape×N,<br/>insertText×N, updateTextStyle×N, ...])
    API-->>MCP: applied
    MCP->>API: getThumbnail(slide_id)
    API-->>MCP: contentUrl (short-lived)
    MCP-->>Agent: {slide_id, thumbnail_url, warnings}
    Agent->>MCP: render_thumbnail(slide_id)
    MCP->>API: getThumbnail + fetch bytes
    API-->>MCP: PNG bytes
    MCP-->>Agent: ImageContent (native)
    Note over Agent: consume image, iterate if wrong
```

The agent passes **semantic content** (title / lead / per-column pill + body) plus optional **visual intent** (`pill_palette`, `title_accent_hex`, per-column `pill_hex`). The server resolves geometry against the actual deck size and emits a single batched `batchUpdate`.

## Core flow — editing an existing slide

```mermaid
sequenceDiagram
    participant Agent as Agent
    participant MCP as slides-mcp
    participant API as Google Slides API

    Agent->>MCP: get_slide(deck_url, slide_id, mode=clean)
    MCP->>API: GET slide (FieldMask-projected)
    API-->>MCP: raw pageElements JSON
    Note over MCP: normalize → classify →<br/>project to compact YAML
    MCP-->>Agent: dsl_yaml (~100-150 tok)

    Agent->>Agent: edit the YAML
    Agent->>MCP: patch_slide(deck_url, slide_id, new_dsl_yaml)
    Note over MCP: fetch current state, diff against<br/>new YAML → minimum batchUpdate
    MCP->>API: batchUpdate([insertText / deleteText /<br/>updatePageElementTransform])
    API-->>MCP: applied
    MCP->>API: (if geometry changed) getThumbnail
    MCP-->>Agent: {applied_request_count, new_dsl_yaml, thumbnail}
```

## What the MVP does

### Write / compose

| Tool | What |
|------|------|
| `create_slide(deck_url, archetype, content, theme_brief?, ...)` | Compose a new slide from archetype + content. Pass `pill_palette` or per-column `pill_hex` for per-slide overrides. `theme_brief=True` (default) auto-reads the deck's meta-slide brief for unset fields. Response includes `brief_applied: bool`. |
| `create_image(deck_url, slide_id, at, url?, prompt?, caption?)` | Insert a raster (via `url`) OR a RECTANGLE placeholder with `[IMAGE: prompt]` text (via `prompt`) — the placeholder is a first-class deliverable, fill in later. |
| `patch_slide(deck_url, slide_id, new_dsl_yaml, ...)` | Apply a DSL diff — text edits + element translations, one call. |
| `create_shape(deck_url, slide_id, at, shape_type, ...)` | Insert a single shape surgically (when `create_slide` is too coarse). |
| `duplicate_slot(deck_url, slide_id, source_id, translate_in)` | Duplicate-and-offset an existing element. |
| `delete_slide(deck_url, slide_id)` | Intent-explicit single-slide deletion — collapses the 3-call escape-hatch pattern. |
| `exec_batch_update(deck_url, requests, confirm_destructive?)` | Raw `batchUpdate` escape hatch. `confirm_destructive=True` required for any `deleteObject` / `deleteSlide` / `replaceAllText`. |
| `clone_deck(src_url, new_title, replacements?)` | Copy a deck via Drive with optional find/replace map. |

### Read

| Tool | What |
|------|------|
| `get_deck_outline(deck_url)` | Whole-deck index, ~40 tok/slide — one call for deck-level reasoning. |
| `get_slide(deck_url, slide_id, mode?, include_elements?)` | One slide as DSL. `include_elements=True` opts into the geometry channel for shape moves. |
| `search_deck(deck_url, query)` | Substring search across slide title + body text. |
| `list_slides_by(deck_url, filters)` | Structural grep — filter slides by archetype, has-image, etc. |
| `render_thumbnail(deck_url, slide_id, size?)` | Rendered PNG as native `ImageContent`. |
| `render_thumbnail_url(deck_url, slide_id, size?)` | Same, but returns URL only (no bytes). |
| `list_themes()` / `list_archetypes()` / `list_deck_layouts(deck_url)` | Registry + deck inventory. |

### Theme hygiene

| Tool | What |
|------|------|
| `audit_deck_colors(deck_url, theme?, sub_theme?)` | Walk every shape; report colors and fonts not in the active theme, with nearest-role suggestions. |
| `promote_to_theme(theme, sub_theme, role_name, kind, value)` | Write a drift value into the user theme file under a named role. |

### Theme coherence (v0.3.0) — in-deck brief

| Tool | What |
|------|------|
| `get_theme_brief(deck_url)` | Read the deck's active brief from its hidden meta-slide. Returns `{brief, slide_id, status}` or `status: "absent"`. |
| `set_theme_brief(deck_url, brief)` | Create or replace the brief. Appends a hidden `isSkipped` meta-slide titled `__SLIDES_MCP_THEME_BRIEF__ — DO NOT DELETE`. |
| `update_theme_brief(deck_url, changes)` | Forward-only deep-merge patch — future slides pick up the change, existing slides untouched. |
| `extract_theme_brief(deck_url)` | Brownfield: propose a brief from an existing deck's dominant palette + shape topology. Does NOT commit — agent reviews with user, tweaks, commits via `set_theme_brief`. |

### Diagnostic

| Tool | What |
|------|------|
| `auth_status()` | Token path, scopes, expiry — no secrets. |

## What it does NOT (yet)

- **Resize / rotate** elements (warn-only in diff; translation-only writes in v0.3.0)
- **Archetype swap** ("relayout to 3 columns" — requires delete-all + recreate; reachable today via `exec_batch_update`)
- **Retroactive repaint from brief updates** (`update_theme_brief` is forward-only; named Phase 2.5 follow-up `restyle_slides` not yet shipped)
- **AI / stock image pipeline** (image URL passthrough works; placeholder-with-prompt works; no Imagen/Pexels/etc. integration yet)
- **Phosphor icon library** (no curated icon set yet — use `create_shape` + `exec_batch_update` manually)
- **`.pptx` target** (Google Slides only)

## Installation

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
# Install and drop the Claude Code skill (recommended)
uvx slides-mcp@latest install

# OR clone + run locally
git clone https://github.com/luutuankiet/slides-mcp.git
cd slides-mcp
uv sync
```

## Auth setup (one-time)

The MCP uses user-OAuth to talk to Google Slides. You need a Google Cloud project with Slides + Drive APIs enabled and a Desktop OAuth client.

1. Create a Google Cloud project; enable `slides.googleapis.com` and `drive.googleapis.com`
2. Create an OAuth 2.0 client (type: **Desktop app**), download `client_secret.json`
3. On any machine with a browser:

    ```bash
    uv run slides-mcp-auth --client-secret /path/to/client_secret.json --out ./token.json
    ```

    This opens a browser, you consent, `token.json` is written. The refresh token inside is long-lived.

4. If your MCP server runs headless, copy `token.json` over (`scp token.json devbox:~/.config/slides-mcp/token.json`). Point the server at it via `$SLIDES_MCP_TOKEN_PATH`.

## MCP client configuration

```json
{
  "mcpServers": {
    "slides": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/slides-mcp", "slides-mcp"],
      "env": {
        "SLIDES_MCP_TOKEN_PATH": "/home/me/.config/slides-mcp/token.json",
        "SLIDES_MCP_THEMES_DIR": "/home/me/.config/slides-mcp/themes"
      }
    }
  }
}
```

| env var | purpose | default |
|---------|---------|---------|
| `SLIDES_MCP_TOKEN_PATH` | Absolute path to `token.json` (OAuth) | `./token.json` |
| `SLIDES_MCP_THEMES_DIR` | Folder with your theme YAML files | — (falls through resolution order) |
| `XDG_CONFIG_HOME` | Standard XDG config root | `~/.config` |

## Architecture

Four layers — see `src/slides_mcp/server.py` for the MCP surface, `projection.py` for the compression core, `create.py` for archetype-to-requests composition.

```mermaid
flowchart TB
    subgraph L4["Layer 4 — MCP tool surface (server.py)"]
        T1[create_slide / patch_slide / exec_batch_update]
        T2[get_slide / get_deck_outline / render_thumbnail]
        T3[audit_deck_colors / promote_to_theme]
    end
    subgraph L3["Layer 3 — Theme + archetype registries"]
        TH[theme.py<br/>YAML parse + role resolve]
        AR[archetypes.py<br/>slot schema + geometry]
    end
    subgraph L2["Layer 2 — DSL projection + diff"]
        NM[normalize.py<br/>raw → FlatShape]
        CL[classify.py<br/>topology → archetype]
        PR[projection.py<br/>FlatShape → YAML]
        DF[diff.py<br/>YAML → batchUpdate]
        AU[audit.py<br/>theme drift walk]
        CR[create.py<br/>archetype + content → batchUpdate]
    end
    subgraph L1["Layer 1 — Google APIs + auth"]
        SA[slides_api.py<br/>FieldMask GETs + batchUpdate]
        AT[auth.py / bootstrap.py<br/>OAuth token load]
    end

    L4 --> L3
    L4 --> L2
    L2 --> L3
    L4 --> L1
    L2 --> L1
```

## Status

**v0.3.0** — 23 MCP tools, 5 content builders, in-deck theme coherence, brownfield extraction. 181 unit tests pass, ruff clean, live-verified on real decks (bidi loop + cross-archetype brief coherence). Pre-v1, actively iterated. No committed production users. Use at your own risk, expect rough edges. See `releases/` for per-version narratives.

## Contributing

Issues welcome. PRs should include tests — `uv run pytest tests/unit/` runs in under a second and uses mocked Slides API JSON fixtures (no network).

## License

MIT. See [LICENSE](LICENSE).
