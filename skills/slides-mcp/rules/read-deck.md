# Reading a deck

## Start with the outline (always)

```
get_deck_outline(deck_url)
```

Returns ~20 tok/slide: index, title (if extracted), archetype. Use this to decide which slides to zoom in on.

## Zoom to a specific slide

### Text-only read (default, ~100–150 tok)

```
get_slide(deck_url, slide_id, mode="clean")
```

Returns semantic slots: `title`, `subtitle`, `body_paragraph`, `columns: [...]`, `notes`, `_object_ids`. Use this for 90% of read intents.

### Geometry read (opt-in, +50 tok)

```
get_slide(deck_url, slide_id, mode="clean", include_elements=True)
```

Adds a top-level `elements: [{id, at: [x, y, w, h]}]` list. Set `include_elements=True` ONLY when you intend to MOVE shapes next — otherwise you're paying tokens for nothing.

### Faithful read (escape hatch, full geometry always)

```
get_slide(deck_url, slide_id, mode="faithful")
```

Raw per-element dicts with every style field. Use when:

- A `clean`-mode projector emits `fallback_reason` (4 archetypes don't have clean projectors yet: `4col_numbered_flow`, `4col_card_with_image`, `table_slide`, `logo_strip`)
- You need every style field for debugging a write that went wrong
- The deck is not in any recognized archetype (`generic_layout`)

## Find slides matching a pattern

### By text substring

```
search_deck(deck_url, query="gemini")
```

Returns `{slide_id, title, matches: [snippet]}` per hit.

### By archetype

```
list_slides_by(deck_url, archetype="3col_pill_cards")
```

### Combined filter (AND semantics)

```
list_slides_by(deck_url, archetype="text_left_image_right", contains_text="roadmap")
```

## Archetype inventory

```
list_deck_layouts(deck_url)
```

Returns per-archetype counts + first-slide-id per bucket. Useful for "how consistent is this deck's structure?"

## Presenter notes

Notes are ALWAYS returned in full — never truncated. They're in the `notes` field of the DSL. QBR decks had max ~1,600 chars / ~400 tok of notes. Don't re-fetch notes; they come with `get_slide`.

## The `_object_ids` map

Clean-mode DSL emits `_object_ids: {title, subtitle, body_paragraph, paragraphs[]}` — a map from semantic slot to the underlying pageElement objectId. You don't normally touch this yourself — `patch_slide` uses it to scope text edits per objectId (avoids `replaceAllText` duplicate-hit).

If you're composing `exec_batch_update` requests (e.g., bulk `updateTextStyle`), harvest the objectIds from this map.

## Performance notes

- `get_deck_outline` uses a narrow FieldMask — cheap to call even on a 100-slide deck
- `get_slide` FieldMask carries full geometry + style — topology-based classifier and drift audit both need it
- `search_deck` does a slide-scoped scan — token-cheap
