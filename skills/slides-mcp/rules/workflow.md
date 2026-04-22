# Workflow — what to use when

The slides-mcp surface decomposes into four layers. Pick the layer matching your intent.

## Layer 1 — Reading (what's in this deck?)

| Intent | Tool | Shape |
|--------|------|-------|
| "What's the deck structure?" | `get_deck_outline` | 1 call, whole deck, ~20 tok/slide |
| "Show me slide N" | `get_slide` (mode=clean, include_elements=False) | ~100–150 tok |
| "Find slides mentioning X" | `search_deck` or `list_slides_by(contains_text="X")` | Snippets per match |
| "Where are all the 3-column slides?" | `list_slides_by(archetype="3col_pill_cards")` | Archetype filter |
| "Full faithful read of a weird slide" | `get_slide(mode="faithful")` | Raw geometry; use when clean fails |
| "I need to MOVE a shape" | `get_slide(include_elements=True)` | Adds ~50 tok: `elements: [{id, at:[x,y,w,h]}]` |

## Layer 2 — Writing (happy path)

| Intent | Tool | Notes |
|--------|------|-------|
| Edit text in an existing slot | `patch_slide` with new DSL | Use `_object_ids` emitted by `get_slide` to scope edits per-objectId |
| Move an existing shape | `patch_slide` with updated `elements[].at` | RELATIVE transform — scale / rotation preserved |
| Edit presenter notes | `patch_slide` with new `notes:` DSL field | Writer emits deleteText + insertText on notesPage objectId |
| Add a new shape to a slide | `create_shape(at, shape_type, text?, fill_role?)` | One call, theme-aware fills |
| Duplicate an existing shape | `duplicate_slot(source_id, translate_in?)` | Preserves styling |
| Clone a deck for a new client | `clone_deck(src, new_title, replacements?)` | cmd+F replacement map optional |

## Layer 3 — Theme hygiene

| Intent | Tool | Notes |
|--------|------|-------|
| "What colors / fonts are off-theme?" | `audit_deck_colors` | Drift report with nearest-role suggestions |
| "This off-theme color IS the brand" | `promote_to_theme(role_name, kind, value)` | Writes to user config theme, never bundled |

## Layer 4 — Escape hatch

If and ONLY if no bespoke tool covers your operation (e.g. `updateTextStyle`, `insertTableRows`):

1. Compose the request list from the [Slides API reference](https://developers.google.com/slides/api/reference/rest/v1/presentations/request)
2. `exec_batch_update(requests, dry_run=True)` — preview kinds + first 5 entries
3. Inspect the result. If the kind list includes anything from the destructive denylist, set `confirm_destructive=True` explicitly.
4. Re-call with `dry_run=False`.

See [escape-hatch.md](escape-hatch.md) for the denylist and audit log.

## Decision helper

```
Need to CHANGE something in a deck?
├── Text of an existing slot?           → patch_slide
├── Move a shape?                        → get_slide(include_elements=True), then patch_slide
├── Add a shape?                         → create_shape
├── Duplicate a shape?                   → duplicate_slot
├── Copy the whole deck?                 → clone_deck
├── Off-theme color to accept?           → promote_to_theme
└── Anything else                        → exec_batch_update (dry_run first)
    (text styles, tables, page props...)
```

## Always do

- **Before any write:** read the current slide with `get_slide` to anchor the DSL diff
- **Before `exec_batch_update`:** run with `dry_run=True` first to preview kinds
- **After a geometry write:** `patch_slide` auto-renders a thumbnail when `verify="auto"` (default) — consume it
- **After an audit:** decide per-drift — promote OR fix via `patch_slide`

## Never do

- Skip `get_slide` before editing — the old DSL is your diff anchor
- Fire `exec_batch_update` without `dry_run=True` first
- Call `replaceAllText` directly — use `patch_slide` so `_object_ids` scope the edit
- Edit the bundled `themes/example.yaml` — user themes live in `~/.config/slides-mcp/themes/`
