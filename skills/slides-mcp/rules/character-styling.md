# Character styling — the typographic depth primitives

Read this when you need to emphasize a word, color a phrase, alight a quote,
loosen line spacing on a paragraph, or otherwise shape text at the character
or paragraph level inside an existing shape. You're in the right file if
your job is "the body reads monolithic — pop the first sentence."

Two bespoke tools cover this:

- `update_text(scope="run", ...)` — character-level (bold, italic, color, size, font, underline, baseline offset)
- `update_text(scope="paragraph", ...)` — paragraph-level (alignment, line spacing, indent, space above/below)

Under the hood these dispatch to `update_text_style` and `update_paragraph_style` — which are still importable for Python callers.

Both share ONE range language. The server resolves the range from the shape's
real text; you never compute UTF-16 indices.

## Range language (shared)

| Spec | Meaning |
|------|---------|
| `None` or `"all"` | Entire text of the shape |
| `{"paragraph": N}` | Nth VISIBLE paragraph, 0-indexed. Blank separators (`\n\n`) don't count — `{"paragraph": 1}` on `"A\n\nB"` selects `"B"`. |
| `{"chars": [start, end]}` | Raw UTF-16 code-unit indices. End is exclusive. Server bounds-checks against real text. |
| `{"match": "substring"}` | Unique substring match. ValueError on 0 hits OR on >1 hits (ambiguous). |

**`match` is the default — it's the one you want.** Use `chars` only when you
genuinely need a specific offset (e.g. 2nd of 3 "the" occurrences); in that
case, read the shape first with `get_slide(include_styles=True)` to see the
text, count the chars, then target.

## `update_text(scope="run")` — the style subset

| Key | Type | Example |
|-----|------|---------|
| `bold` | bool | `True` |
| `italic` | bool | `True` |
| `underline` | bool | `True` |
| `strikethrough` | bool | `True` |
| `smallCaps` | bool | `True` |
| `fontFamily` | str | `"Inter"` |
| `fontSize` | num (pt) | `28` |
| `foregroundColor` | hex | `"#E8612E"` |
| `backgroundColor` | hex | `"#FFF3E8"` |
| `baselineOffset` | enum | `"SUPERSCRIPT"` / `"SUBSCRIPT"` / `"NONE"` |
| `weightedFontFamily` | dict | `{"fontFamily": "Inter", "weight": 700}` |

Unknown keys raise `ValueError` — typos surface server-side, not in Google's
opaque error messages.

## `update_text(scope="paragraph")` — the paragraph subset

| Key | Type | Example |
|-----|------|---------|
| `alignment` | enum | `"START"` / `"CENTER"` / `"END"` / `"JUSTIFIED"` |
| `direction` | enum | `"LEFT_TO_RIGHT"` / `"RIGHT_TO_LEFT"` |
| `spacingMode` | enum | `"NEVER_COLLAPSE"` / `"COLLAPSE_LISTS"` |
| `lineSpacing` | num (%) | `100` = single, `150` = 1.5×, `200` = double |
| `spaceAbove`, `spaceBelow` | num (pt) | `12` |
| `indentStart`, `indentEnd`, `indentFirstLine` | num (pt) | `24` |

Paragraph style is applied to every paragraph that INTERSECTS the range —
practically, use `range="all"` for whole-shape changes or `{"paragraph": N}`
to target one paragraph.

## Thumbnail feedback loop (`verify="auto"`)

Both tools return a `thumbnail_url` by default. Chain `render_thumbnail`
to VISUALLY verify the edit. Don't trust the request echo — trust the render.

```
result = update_text_style(
    deck, slide_id, object_id=body_id,
    range={"match": "The problem"},
    style={"bold": True, "fontSize": 28, "foregroundColor": "#E8612E"},
)
render_thumbnail(deck, slide_id)  # confirm the visual
```

Pass `verify="never"` in tight loops where you're making several edits
consecutively and only want the final thumbnail.

## Discovery — what's already styled?

Before editing, read the shape's current runs:

```
get_slide(deck_url, slide_id, include_styles=True)
# → includes a top-level `_styles:` map keyed by object_id with runs:
# _styles:
#   c_body:
#     - {text: "The "}
#     - {text: "first sentence", bold: True, color_hex: "#E8612E"}
#     - {text: " sets the tone."}
```

`include_styles=True` costs ~30 tok/slide for styled shapes; 0 for uniform
plain text (the runs collapse and the channel is omitted). Default is False
to keep the 150 tok/slide read budget.

## Common recipes

### Emphasize the first phrase of a body

```
update_text_style(
    deck, slide_id, object_id=body_id,
    range={"match": "The problem"},
    style={"bold": True, "fontSize": 28, "foregroundColor": accent_hex},
)
```

### Italicize one paragraph as a quote

```
update_text_style(
    deck, slide_id, object_id=body_id,
    range={"paragraph": 1},
    style={"italic": True, "foregroundColor": "#666666"},
)
update_paragraph_style(
    deck, slide_id, object_id=body_id,
    range={"paragraph": 1},
    style={"alignment": "CENTER", "lineSpacing": 150, "spaceAbove": 12},
)
```

### Center + space-out a title

```
update_paragraph_style(
    deck, slide_id, object_id=title_id,
    range="all",
    style={"alignment": "CENTER", "spaceBelow": 18},
)
```

### Style a typographic hierarchy from scratch

After `create_slide` with plain content, layer in depth:

1. **Discover the object_ids.** `get_slide(deck, slide_id, include_styles=True)` returns the slide's structured runs + `_object_ids` map (`title`, `body_paragraph`, `paragraphs[i]`). Every `update_text` call needs an `object_id` — this is where you get it.
2. `update_text(deck_url, slide_id, title_id, scope="run", range="all", style={"fontSize": 48})` — size the hero
3. `update_text(deck_url, slide_id, body_id, scope="run", range={"paragraph": 0}, style={"bold": True})` — lead with weight
4. `update_text(deck_url, slide_id, body_id, scope="run", range={"match": accent_word}, style={"foregroundColor": accent_hex})` — color the keyword
5. `render_thumbnail` — see it
6. Iterate

**Tip:** `include_styles=True` on `get_slide` returns `_styles:` showing current runs — so you can see what's already styled before stacking more edits.

## Anti-patterns

| Anti-pattern | Why it's bad | Fix |
|--------------|-------------|-----|
| `exec_batch_update` with hand-rolled `updateTextStyle` | Agent-blind UTF-16 math; no match-mode safety; no auto-thumbnail | Use `update_text(scope="run")` with range language |
| `range={"match": "the"}` on ambiguous text | Raises `ValueError` | Pick a unique substring OR use `chars` with `get_slide(include_styles=True)` to count |
| Blind edit, no thumbnail | Silent mis-target possible; style lands on wrong range | Keep `verify="auto"` default; chain `render_thumbnail` |
| Asking for paragraph N when text has blank separators | "Paragraph 1" of `"A\n\nB\n\nC"` is `B` (2nd VISIBLE), not the empty string | Already handled — just trust it |
| Applying character styling via `patch_slide` DSL | `patch_slide` diffs the DSL at slot level; fine-grained runs don't survive | Use `update_text(scope="run")` directly on the slot's `object_id` |
