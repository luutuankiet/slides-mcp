# Writing to a deck

## The diff-anchor rule

**Before writing, read.** `patch_slide` takes the new DSL you want and diffs it against a fresh `get_slide` fetch to emit minimal batchUpdate requests. If you don't anchor on the current DSL, you can't diff cleanly.

```
current = get_slide(deck_url, slide_id, include_elements=True)  # include_elements if moving shapes
# ... modify current's DSL ...
result = patch_slide(deck_url, slide_id, new_dsl_yaml, verify="auto")
```

## Text edits — use `_object_ids`

Clean-mode DSL emits `_object_ids: {title, subtitle, body_paragraph, paragraphs[]}` — a map from semantic slot to the underlying pageElement objectId. `patch_slide` uses these to emit `deleteText + insertText` PER OBJECT instead of slide-scoped `replaceAllText`.

**Why:** `replaceAllText` matches against ALL text on the slide — if two slots carry the same string (e.g., two "Overview" column headers), both get hit. `deleteText + insertText` on a specific objectId is immune to this.

If `_object_ids` is absent for a slot (faithful mode, or a column text not yet covered), the legacy `replaceAllText` scoped to the slide kicks in. It still works — just carries the duplicate-hit risk.

## Translation writes — `elements[].at`

To move an existing shape:

1. Read the slide with `include_elements=True`
2. Find the `id` of the shape in `elements: [...]`
3. Edit its `at: [x, y, w, h]` — change `x` or `y`
4. `patch_slide` diffs the new `elements` list and emits ONE `updatePageElementTransform` per shape whose `at[0]` or `at[1]` changed
5. The emit uses `applyMode: RELATIVE, scaleX/Y: 1, translateX/Y: Δ in EMU` — **RELATIVE preserves scale and rotation** on already-transformed shapes (like scaled icons)

`at[2]` / `at[3]` (width/height) diffs emit a WARNING, not a write — resize is deferred to Phase 2.

## Notes edits

Notes are a first-class DSL field. Edit `notes:` in the new DSL; `patch_slide` threads `notes_object_id` from the notesPage through and emits `deleteText + insertText` on that object.

If `notes_object_id` is absent (rare — notesPage not present, or FieldMask issue), the writer emits a warning instead of a write. Re-fetch with the standard full-field `get_slide`.

## Thumbnail verification

`patch_slide` auto-renders a thumbnail when:

| `verify=` | When thumbnail fires |
|-----------|---------------------|
| `"always"` | Every call |
| `"auto"` (default) | Only when geometry changed (any `elements[].at` diff or add / remove) |
| `"never"` | Skip |

The thumbnail comes back as an MCP `ImageContent` block. Consume it to visually verify your move landed where you intended.

## Cloning a deck

```
clone_deck(
    src_deck_url,
    new_title="Client X QBR Q1",
    replacements={"Client Y": "Client X", "Q4 2025": "Q1 2026"},
)
```

The `replacements` map is applied post-copy via batched `replaceAllText` (matchCase=False). It's the cmd+F template fill-in: one new deck, N text swaps, one call.

## What emits a warning (not a write)

- `elements[].at` width or height change → resize deferred
- `elements` add or remove via DSL → use `create_shape` / explicit `exec_batch_update` instead
- Archetype swap → not supported; returns empty diff + warning
- `hero` / `image` / `accent_panel` slot change in DSL → use `elements[].at` to move the underlying shape
- Notes edit without a `notes_object_id` → re-fetch with full FieldMask

## Return shape

```yaml
applied_request_count: 3
summary:
  - "text edit: title"
  - "translate: JOON_block by -0.5in y"
warnings: []
thumbnail_url: "https://docs.google.com/..."   # if verify fired
new_dsl_yaml: |
  title: ...
  columns:
    - ...
```

`new_dsl_yaml` is the freshly projected DSL after the write. Use it as your NEW diff anchor for the next edit on this slide — no re-fetch needed.
