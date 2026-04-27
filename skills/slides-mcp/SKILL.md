---
name: slides-mcp
description: REQUIRED before using any slides-mcp tools. v2 is a read-only deck-ingestion server — five tools (auth_status, get_deck_outline, read_slides, search_deck, render_thumbnail) for consuming Google Slides as conversation context.
metadata:
  tags: slides, google-slides, mcp, presentation, deck, read-only
---

## When to use

ALWAYS read this skill BEFORE calling any slides-mcp tool. Use when:

- The user pastes a Google Slides URL and you need to discuss the contents.
- You want to summarize, search, or quote from a deck without opening it manually.
- You need to understand the visual structure (archetype, image presence, notes presence) of a deck.
- You need a rendered slide thumbnail to reason about the visual layout.

**This is a READ-ONLY server.** It cannot edit, create, duplicate, or theme slides. If you need write capability, the user can pin v0.11 (`uvx slides-mcp@0.11.0`).

## The 5 tools

| Tool | When | Cost |
|------|------|------|
| `auth_status()` | Sanity check before first call | minimal |
| `get_deck_outline(deck_url)` | First call on every new deck — gives you the index | ~20 tok/slide |
| `read_slides(deck_url, slides?, detail?, ...)` | Drill into one or many slides | 20–400 tok/slide |
| `search_deck(deck_url, query, ...)` | Locate slides by text content | flat per deck |
| `render_thumbnail(deck_url, slide_id, size?)` | See the rendered visual layout | ~640–2700 tok per image |

## Detail modes (`read_slides`)

Pick the cheapest mode that answers your question.

| `detail=` | Per-slide tok | Fields returned |
|-----------|--------------|-----------------|
| `outline` | ~20 | `slide_id, title, archetype, element_count, has_notes, has_image` |
| `summary` | ~80 | `slide_id, title, archetype, body, image_count, notes_preview` |
| `full`    | ~150 | `slide_id, title, archetype, body[list], images[refs], tables, charts, notes` |
| `raw`     | ~400 | every leaf shape with `at, kind, text, shape_type, fill_hex, outline_hex, runs[]` (debug only) |

Rule of thumb:
- Default to `summary`. It's enough for 90% of "what's this slide about" conversation.
- Use `outline` when you only need to *navigate* (which slides have notes? which slides have images?).
- Use `full` when you need to quote text precisely or extract every body string.
- Use `raw` when you need geometry or character-level styling for debugging.

## Slide selectors (read_slides + search_deck)

| `slides=` | Meaning |
|-----------|---------|
| `None` (default) | All slides in deck order |
| `"slide_id_g123abc"` | Single slide |
| `"3-7"` | 1-indexed inclusive range |
| `["id1", "id2"]` | Explicit list of slide_ids |
| `[1, 3, 5]` | List of 1-indexed positions |
| `{"first": 5}` | Head — first N slides |
| `{"last": 3}` | Tail — last N slides |
| `{"with_notes": true}` | Filter: only slides that have speaker notes |
| `{"with_image": true}` | Filter: only slides that contain at least one picture |

## Recommended workflow

1. **Onboard:** `get_deck_outline(url)` first. Always. ~20 tok/slide → ~1000 tok for a 50-slide deck. You learn titles, archetypes, and which slides have notes/images.
2. **Pick targets:** scan the outline; choose 5–10 slides worth drilling into.
3. **Drill:** `read_slides(url, slides=[…], detail="summary")` for most discussions. Switch to `"full"` if you need verbatim text.
4. **Search:** `search_deck(url, "keyword")` to find slides about a topic. Returns `slide_id, where (title|body|notes), snippet`.
5. **Visual:** `render_thumbnail(url, slide_id)` ONLY when the layout / images / colors matter. Each PNG is expensive — don't render speculatively.

## Token budget guidance

For a 50-slide deck:

| Operation | Cost |
|-----------|------|
| `get_deck_outline` | ~1000 tok |
| `read_slides(detail="summary")` whole deck | ~4000 tok |
| `read_slides(detail="full")` whole deck | ~7500 tok |
| `read_slides(detail="raw")` whole deck | ~20000 tok |
| One MEDIUM thumbnail | ~1300 tok |

Don't pull `full` on every slide. Start with outline, drill into the slides that matter.

## Anti-patterns

- ❌ Calling `read_slides(detail="full")` with no slide selector on first contact — wastes tokens. Outline first.
- ❌ Calling `render_thumbnail` for every slide to "see what they look like." Pick 1–3 with visual interest.
- ❌ Using `detail="raw"` for content reasoning. It's geometry/style for debugging only — text is cleaner in `full`.
- ❌ Asking the server to edit a slide. v2 cannot. Tell the user, or pin v0.11.

## Auth

Requires `~/.config/slides-mcp/token.json` (or `SLIDES_MCP_TOKEN_PATH` env). One-time consent on a machine with a browser via `slides-mcp-auth`. See README §Auth.
