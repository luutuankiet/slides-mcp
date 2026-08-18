# slides-mcp v2

A minimal MCP server for Google Slides as agent context. **5 read primitives + 2 curated write tools** (footers, batch text edits) for agent legwork. Token-efficient, mirrors `read_files` philosophy.

## What this is

You give Claude a Google Slides URL. Claude reads the deck (outline → drill-down → text or visual on demand) and discusses the content with you. No editing, no archetypes, no theme magic — pure read.

v2 is intentionally barebone: **be a great reader of slides, period.** The use case is conversation: paste a deck link, talk about it.

## Why v2 dropped writes

v0.x → v0.11 chased an ambitious write surface for Google Slides — archetypes, themes, briefs, restyle, plan_deck, theme_swap, exec_batch_update. After many sessions iterating against the live API on real decks, the conclusion was that brownfield slide editing via the Slides API is a losing game (autofit regressions, color drift, archetype mismatches, unpredictable rejects). The investment is sunk cost.

If you depended on the v0.x write tools: pin them — `uvx slides-mcp@0.11.0`.

## Why v2.1 brings back a narrow wedge

The lesson stands: brownfield slide editing via the API is a losing game when the goal is pixel-perfect creative authoring. v2.1 reframes the goal: **legwork, not authorship.** Things humans hate doing manually but where 1 cm of layout slop is fine — section footers, global font swaps, bulk timestamps, find-and-replace across a deck.

Two new tools (see "v2.1 write wedge" below). Both ship with a multi-granularity `post_state` envelope that returns the updated deck state in the same response — verifying the agent's own change without a second round-trip. (Pattern not found in any production MCP server we audited; documented in `releases/v2.1.0.md`.)

## Tool surface

| Tool | Purpose |
|------|---------|
| `auth_status()` | Token state without exposing secrets |
| `get_deck_outline(deck_url)` | ~20 tok/slide whole-deck index — first call on every new deck |
| `read_slides(deck_url, slides?, detail?, include_notes?, include_images?)` | Read one or many slides at chosen detail |
| `search_deck(deck_url, query, slides?, regex?, include_notes?)` | Substring or regex search across deck |
| `render_thumbnail(deck_url, slide_id, size?)` | One slide as native PNG (`ImageContent`) |
| `exec_batch_update(deck_url, requests, dry_run?, confirm_destructive?, post_state?)` | **(v2.1)** Raw passthrough to Slides `batchUpdate` + multi-granularity post-state return |
| `add_section_footers(deck_url, sections, template?, ...)` | **(v2.1)** Add chapter/section footer to every slide; idempotent re-runs |

## Detail modes

`read_slides` mirrors `read_files`: one tool, multi-mode, batched. Pick the cheapest detail level that answers the question.

| Mode | Per-slide tok | Returns |
|------|--------------|---------|
| `outline` | ~30 | title + archetype + element_count + has_notes/has_image flags + position + hidden + layout_id + notes_chars |
| `summary` | ~150 | title + joined body (cap 1500) + image_count + **full notes (no truncation)** + notes_chars + position/hidden/layout_id |
| `full` | ~300 | title + every body string (no cap) + image refs + table/chart counts + **full notes** + position/hidden/layout_id |
| `raw` | ~600 | every leaf shape with geometry + style + runs + full notes (debug; faithful) |

**Notes are content, not metadata.** Speaker notes are emitted verbatim in `summary`/`full`/`raw` so agents can read drafts where the narrative lives in notes (common case for working decks). The token budget reflects this — pay it; it's the difference between "reading the deck" and "reading the slide chrome".

**Hidden slides** (`slideProperties.isSkipped`) are flagged via `hidden: true` on every mode. Position is 1-indexed deck order. `layout_id` carries the source layout's `objectId` for grouping by template.

## v2.1 write wedge

Two tools for legwork-shaped edits. Read tools above are still the primary interface — these are the curated write subset.

| Tool | Purpose |
|------|---------|
| `exec_batch_update(deck_url, requests, dry_run?, confirm_destructive?, post_state?)` | Raw passthrough to Google Slides `batchUpdate`. Agent composes the Request list. Returns Slides API replies + multi-granularity post-state envelope. |
| `add_section_footers(deck_url, sections, template?, footer_position?, overwrite_existing?, confirm_destructive?, post_state?)` | Adds a chapter/section footer (e.g. "Section 2/4 · prev: Discovery · next: Build") to every slide. Idempotent re-runs via deterministic `slides_mcp_footer_*` objectIds. |

### Verify-after-write multi-granularity return

Every successful write returns a `post_state` envelope in the SAME response:

```python
{
  "applied_request_count": 5,
  "request_kinds": [...],
  "replies": [...],                    # Slides API replies (created object IDs, etc.)
  "warnings": [...],
  "affected_slide_ids": [...],         # server-derived
  "post_state": {
    "deck_outline": {...},             # whole deck index (~20 tok/slide)
    "slides": [...]                    # full projection of touched slides
  },
  "isError": false
}
```

Verbosity gated by `post_state` knob: `"none" | "outline" | "summary" | "full"` (default `"summary"`). Cuts the fire-then-read round-trip the agent would otherwise need. No production MCP server we audited (`matteoantoci/google-slides-mcp` 177★, `mcp/git`, `notion-mcp-server`) bundles this — documented in `releases/v2.1.0.md`.

### Layout caveat

Write tools accept that pixel-perfect alignment is impossible without visual feedback loops. v2.1 is for **legwork**: footers, font swaps, bulk text edits — work where 1 cm of slop is fine. v0.x tried for full creative authorship and failed; v2.1 explicitly does not.

### Composing requests

For `exec_batch_update`, the agent writes Slides API Request dicts directly. See **[`skills/slides-mcp/SKILL.md`](skills/slides-mcp/SKILL.md)** for:

- Common Request kinds (createShape / replaceAllText / updateTextStyle / updatePageElementTransform / deleteObject)
- objectId discovery flow (`get_deck_outline` → use slide_ids in requests; `read_slides(detail="raw")` → element ids)
- EMU cheat sheet (1 in = 914400 EMU; 16:9 deck = 9144000 × 5143500)
- The `autofit:NONE` invariant for shapes with text
- Worked examples (rename a deck, add timestamps, change global font)

### OAuth scope

v2.1 keeps the v2 default scope `presentations.readonly` for fresh consents. **Existing v0.x tokens (with `presentations` write scope) keep working.** Fresh-v2-token holders need to re-run `slides-mcp-auth` with a write-scope client to use write tools. The server surfaces a `403 PERMISSION_DENIED` with an actionable error message ("Re-run `slides-mcp-auth` to mint a token with write scope") when the scope is insufficient.

## Slide selectors

Mirrors the `read_files` per-file flexibility:

```
slides=None              # all slides (default)
slides="slide_id"        # single slide
slides="3-7"             # 1-indexed inclusive range
slides=["id1","id2"]     # list of object_ids
slides=[1,3,5]           # list of 1-indexed positions
slides={"first":5}       # head
slides={"last":3}        # tail
slides={"with_notes":true}
slides={"with_image":true}
slides={"hidden":true}    # only skipped slides
slides={"hidden":false}   # only visible slides
```

## Workflow

```mermaid
flowchart TD
    user[User pastes deck URL] --> outline[get_deck_outline]
    outline -->|titles + archetypes + flags| pick[Pick slides of interest]
    pick -->|navigation only| done1[Discuss with user]
    pick --> read[read_slides detail=summary]
    read -->|need verbatim| full[read_slides detail=full]
    pick --> search[search_deck for topic]
    search --> read
    read --> visual{layout matters?}
    visual -->|yes| thumb[render_thumbnail]
    visual -->|no| done2[Discuss with user]
    full --> done2
    thumb --> done2
```

## Architecture

```
┌──────────────────────────────────────────┐
│ server.py — 7 FastMCP tools (5 read + 2 write) │
├──────────────────────────────────────────┤
│ projection.py — outline/summary/full/raw │
│   per-slide dict with token-budget tiers │
├──────────────────────────────────────────┤
│ classify.py — archetype label            │
│   topology-derived diagnostic            │
├──────────────────────────────────────────┤
│ normalize.py — pageElement → FlatShape   │
├──────────────────────────────────────────┤
│ slides_api.py — REST wrapper             │
│   read: get + thumbnail; write: batchUpdate │
├──────────────────────────────────────────┤
│ auth.py — token.json refresh             │
└──────────────────────────────────────────┘
```

## Installation

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
uvx slides-mcp@latest
```

## Auth setup (one-time)

1. On a machine with a browser, drop a Google Cloud OAuth client JSON at `~/.config/slides-mcp/client_secret.json`. Configure the OAuth consent screen with scope `https://www.googleapis.com/auth/presentations.readonly` (read-only, the v2 default). To use the v2.1 write tools (`exec_batch_update`, `add_section_footers`), use the broader `https://www.googleapis.com/auth/presentations` scope instead.
2. Run `slides-mcp-auth --client-secret ~/.config/slides-mcp/client_secret.json --out ~/.config/slides-mcp/token.json` and complete the consent flow.
3. Token lands at the canonical `~/.config/slides-mcp/token.json` path.
4. Copy the token to your headless host if running there: `scp ~/.config/slides-mcp/token.json host:~/.config/slides-mcp/`.

> ⚠️ **Don't use `--out ./token.json`.** That writes to your CWD, not the canonical path the server reads from. The server falls back to `~/.config/slides-mcp/token.json` (or `SLIDES_MCP_TOKEN_PATH` if set) — a token in your project directory will be silently ignored on next start. Always pass an absolute path.

The server reads `SLIDES_MCP_TOKEN_PATH` env if set, otherwise falls back to the default location.

## MCP client config

```json
{
  "mcpServers": {
    "slides-mcp": {
      "command": "uvx",
      "args": ["slides-mcp@latest"],
      "env": {
        "SLIDES_MCP_TOKEN_PATH": "/home/you/.config/slides-mcp/token.json"
      }
    }
  }
}
```

The server runs over stdio and auto-refreshes the OAuth token in-process.

## Token budget guidance

For a 50-slide deck:

| Operation | Approx cost |
|-----------|-------------|
| `get_deck_outline` whole deck | ~1000 tok |
| `read_slides(detail="summary")` whole deck | ~4000 tok |
| `read_slides(detail="full")` whole deck | ~7500 tok |
| `read_slides(detail="raw")` whole deck | ~20000 tok |
| One MEDIUM thumbnail | ~1300 tok |

Don't pull `full` on every slide. Start with outline, drill into the 5–10 slides that matter.

## Anti-patterns

- ❌ Calling `read_slides(detail="full")` with no slide selector on first contact — wastes tokens. Outline first.
- ❌ Calling `render_thumbnail` for every slide "just to see what they look like." Pick 1–3 with genuine visual interest.
- ❌ Using `detail="raw"` for content reasoning. It's geometry/style for debugging only — text is cleaner in `full`.
- ❌ Asking for v0.x archetype builders / theme briefs / restyle / preview / catalog tools. v2.1 ships only `exec_batch_update` + `add_section_footers`; the v0.x conveniences stay scrapped. Pin `uvx slides-mcp@0.11.0` if you really need them.
- ❌ Expecting pixel-perfect layout from `exec_batch_update`/`add_section_footers`. v2.1 is legwork, not authorship — fine-tune in the Slides UI.

## Status

v2.1.0 — released May 2026. Adds curated write wedge: `exec_batch_update` (Slides API passthrough) + `add_section_footers` (proof tool). Both ship with multi-granularity `post_state` envelope (deck_outline + touched slides[]) — a verify-after-write pattern not found in production MCP servers we audited. Read surface (5 tools) unchanged from v2.0.1.

v2.0.1 — April 2026. Patch over v2.0.0: OAuth refresh `invalid_scope` fix + slide metadata expansion (position, hidden, layout_id, notes_chars) + notes-verbosity (full notes verbatim in summary/full/raw, no truncation).

## Troubleshooting

### `403: Request had insufficient authentication scopes` on a write call

**Symptom:** any of `exec_batch_update`, `add_section_footers` fails with:
```
Slides API error 403: Request had insufficient authentication scopes.. If your token was minted by slides-mcp v2.0+, it likely has `presentations.readonly` scope only.
```

**Cause:** v2.0.0+ defaults fresh OAuth consents to `presentations.readonly`. Write tools need the broader `presentations` scope.

**Fix:** re-run `slides-mcp-auth` with an OAuth client whose consent screen has `presentations` (not `presentations.readonly`) configured. The new token replaces the old one; existing v0.x tokens that already had `presentations` keep working untouched.

### `invalid_scope: Bad Request` on first tool call

**Symptom:** any tool call (except `auth_status`) errors with:
```
('invalid_scope: Bad Request', {'error': 'invalid_scope', 'error_description': 'Bad Request'})
```

**Cause:** v2.0.0 had a bug where token-refresh forced the narrow `presentations.readonly` scope onto the credential, which Google rejects when the token was minted under a different (broader) grant. Fixed in v2.0.1.

**Fix:** upgrade to v2.0.1 (`uvx slides-mcp@latest`). No re-auth needed; existing tokens keep working.

### Server says "no token at `~/.config/slides-mcp/token.json`"

You likely ran `slides-mcp-auth --out ./token.json` and the file landed in your project CWD. Move it to the canonical path:
```bash
mkdir -p ~/.config/slides-mcp
mv ./token.json ~/.config/slides-mcp/token.json
chmod 600 ~/.config/slides-mcp/token.json
```
Or set `SLIDES_MCP_TOKEN_PATH` to wherever it actually lives.

### Notes are huge — can I cap them?

No, by design. v2.0.1 emits full notes verbatim in `summary`/`full`/`raw` because notes are where the deck's narrative lives in working drafts. If you need to bound cost, narrow the `slides` selector (range, `with_notes`, `hidden:false`) instead of trimming each slide. `outline` mode skips note bodies entirely (only `notes_chars`).

## License

MIT.
