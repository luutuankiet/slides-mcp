# Preview workflow (v0.7.0) — approve before you commit

PIL-backed preview primitives let the human see theme / tone / accent / font /
shape-language candidates **before any slide is written**. The whole goal:
cut write-and-delete cycles out of the brief-discovery loop.

## The four preview tools

| Tool | What it shows | Writes? |
|------|---------------|---------|
| `render_brief_swatch(brief)` | Single tone card: palette + pill row + numbered chips + shape chevron + font sample | no |
| `render_brief_swatch_grid(briefs)` | N tone cards side-by-side in one PNG | no |
| `preview_archetype(archetype, content, brief)` | PIL sketch of what the actual slide would look like | no |
| `render_deck_contact_sheet(deck_url, slide_ids?, variant_id?)` | Thumbnail grid of every (or filtered) slide | no (fetches thumbnails) |

All four return native MCP `ImageContent`. Human eyeballs them, picks, then
the agent commits the chosen brief via `set_theme_brief` + `create_slide`.

## When to reach for each

```
                       Human decision to make
                              │
     ┌────────────────────────┼─────────────────────────┐
     │                        │                         │
  which brief?           which archetype?          does the deck
  (greenfield)           (undecided layout)        feel coherent?
     │                        │                         │
     ▼                        ▼                         ▼
swatch_grid(briefs)    preview_archetype(N×)     contact_sheet(deck)
     │                        │                         │
     ▼                        ▼                         ▼
set_theme_brief          create_slide              ← no action, ship
```

## Greenfield loop — "Approve before you commit"

```python
# 1. Propose candidates from intent
briefs = propose_brief_variants("Series B board pitch for a fintech", n=3)

# 2. Show them side-by-side (ONE image, human decides in seconds)
render_brief_swatch_grid(briefs)

# 3. Human picks (say index 1). Commit it.
set_theme_brief(deck_url, briefs[1])

# 4. Generate deck — every create_slide now inherits the brief
for content in contents:
    create_slide(deck_url, archetype, content)
```

## Archetype pick — one content, N layouts

```python
content = {"title": "Q2 metrics", "columns": [...]}
brief = get_theme_brief(deck_url)

for arch in ("3col_pill_cards", "4col_numbered_flow", "text_left_image_right"):
    preview_archetype(arch, content, brief)  # each returns an ImageContent

# Human picks the layout that reads best for this content, then commit:
create_slide(deck_url, chosen_arch, content)
```

## Pre-ship coherence pass

```python
sheet = render_deck_contact_sheet(deck_url)  # every non-meta slide, 4-col grid
# Human scans for any tile that doesn't fit the voice.
# If one does, surgical fix via restyle_slides(slide_ids=[that one], ...).
```

Cap: default `max_slides=36`. Narrow with `slide_ids=[...]` or `variant_id="v0_"`
on bigger decks.

## Variant comparison after generate

If you DID write variants (via `generate_variants`), contact-sheet them by prefix:

```python
render_deck_contact_sheet(deck_url, variant_id="v0_")  # all v0_* slides
render_deck_contact_sheet(deck_url, variant_id="v1_")  # all v1_* slides
render_deck_contact_sheet(deck_url, variant_id="v2_")  # all v2_* slides
# Human picks → lock_variant(winner_id, ...) deletes losers.
```

This is the v0.7.0 upgrade over pure `render_thumbnail` loops: one call per
variant instead of N.

## Anti-patterns

- **Skipping the grid and picking the first mood that scores well.** The grid
  exists because intent → brief is fuzzy; let the human disambiguate.
- **Using `preview_archetype` as a substitute for `create_slide`.** Preview is
  a SKETCH, not the real render. Always verify with `render_thumbnail` after
  `create_slide`.
- **Calling `render_deck_contact_sheet` on a 200-slide deck without filtering.**
  Default cap is 36 tiles for a reason — excess gets truncated. Pass
  `slide_ids=[...]` to narrow.
- **Rendering variant grids when a single swatch suffices.** If the user already
  approved the mood family, you don't need all 5 variants — render just that
  one swatch, confirm, commit.

## See also

- `rules/variant-generation.md` — deeper dive on propose → generate → lock
- `rules/theme-discipline.md` — when to override vs let brief win
- `rules/theme-coherence.md` — the meta-slide mechanics underneath all this
