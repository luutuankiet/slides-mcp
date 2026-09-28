---
title: Read path (outline, read, search, thumbnail)
covers: how a deck URL becomes the slide dicts an agent sees, where detail modes, selectors, archetypes, titles and speaker notes come from
verified: 2026-09-28
---

# Read path

Every read tool follows the same pipeline:

```
deck URL → slides_api (one GET) → normalize → classify → projection → tool return
```

Line numbers below are **a starting point, not an address**. Jump roughly
there and confirm by what the code says. If a range is off by more than a
screen, fix it here and re-date the page.

## The tools

All five read tools live in `src/slides_mcp/server.py` (959 lines):

| tool | lines | notes |
|---|---|---|
| `auth_status` | 164–168 | calls `auth.credentials_info()`, never exposes the token |
| `get_deck_outline` | 170–189 | thin wrapper over `_project_deck_outline` (117–146) |
| `read_slides` | 191–288 | the primary read tool; validates `detail`, `include_images` and `notes_format` itself |
| `search_deck` | 290–371 | checks title, then body, then notes; reports the **first** match per slide only |
| `render_thumbnail` | 373–398 | returns an MCP `Image`; fetches bytes from a short-lived Google URL |

`_project_deck_outline` is shared with the write path: the `post_state`
envelope reuses it, so outline changes show up in both places.

## One whole-deck GET per call

`slides_api.get_presentation` (`slides_api.py:119`) fetches the whole deck with
the `DECK_FIELDS` field mask (lines 30–63), even when the caller asked for one
slide. Selection happens afterwards, in memory. The mask includes masters,
layouts, page size and every page's background and colour scheme, because
theme colours and inherited backgrounds resolve through them. Group children
are fetched to one level of nesting inside `ELEMENT_FIELDS`.
`DECK_OUTLINE_FIELDS` is an alias kept for older imports. `get_slide` and
`SLIDE_FULL_FIELDS` exist (lines 67–71, 125–133) but nothing calls them.

If a projection needs a field the API does not return, add it to the field
mask first; otherwise the key is just missing and the projection silently
emits nothing.

## Slide selectors

`_resolve_slide_ids` (`server.py:50–120`) turns the `slides` argument into an
ordered list of slide objectIds: `None`, a single id, a `"3-7"` range
(1-indexed, inclusive), a list of ids or 1-indexed ints, or a dict with
`first`, `last`, `hidden`, `with_notes` or `with_image`. Unknown ids and
out-of-range ints raise `ValueError`. `add_section_footers` reuses the same
function for its `slide_range` / `slide_ids` / `slide_positions`.

## Normalize

`src/slides_mcp/normalize.py` converts Slides API `pageElement` JSON into
`FlatShape` dataclasses (lines 17–43), so nothing downstream touches raw API
shapes.

- **Geometry is the page-space bounding box.** Each element's transform is
  turned into a matrix (`_matrix`, 88–113), composed with its parent group's
  (`compose`, 115–127), then the box corners are projected (`bbox`, 129–135).
  So `left/top/w/h_in` are correct for scaled, rotated and grouped elements.
  The API omits zero-valued transform fields, and a missing scale means 1
  only when no scale or shear key exists at all; `_matrix` documents this.
  `rotation_deg` (137–144) is derived from the same matrix.
- **Fills say what is painted.** `fill_record` (180–209) returns
  `kind` none | solid | image | inherit | other. An unrendered fill is `none`
  even though the API still sends a white `solidFill` beside it; an empty
  `rgbColor` is black; an empty fill object (in practice a gradient) is
  `other`.
- **Theme colours resolve to hex.** `DeckContext` (460–501) follows the
  colour scheme and background from slide to layout to master. Runs keep the
  slot name (`color_theme`) beside the hex, and a run with no colour of its
  own is flagged `color_inherited`.
- **Text runs carry UTF-16 offsets** (`_extract_text`, 239–276), because that
  is what `textRange` indices in write requests count. `utf16_len` (278) is
  the helper; emoji are two units.
- A shape is `kind="text"` if it has non-blank text or is a `TEXT_BOX`;
  otherwise `"shape"` (`_normalize_element`, 283–367).
- Groups keep children; `flatten` (382–390) recurses them away for the
  classifier and projections. A group with no size of its own gets the union
  of its children's boxes.
- Speaker notes come from the notes page's `BODY` placeholder (`extract_notes`
  393–411, `notes_shape` 503–516). Hidden = `slideProperties.isSkipped`
  (418–424). `read_slides(notes_format="markdown")` renders them through
  `notes_md.to_markdown`, the same codec `write_speaker_notes` writes with.

## Classify

`src/slides_mcp/classify.py:42–90` assigns one archetype label per slide from
element topology, not the layout name: tables, charts, logo strips,
hero covers, 4- and 3-column layouts, text+image, text-heavy, else
`generic_layout`. First match wins, most specific first. The label is a hint
for choosing which slides to read, nothing depends on it.

## Projection

`src/slides_mcp/projection.py` has one dispatcher, `project` (260–), over
four modes:

| mode | function | what it adds |
|---|---|---|
| `outline` | `_outline` 84–100 | title (≤120 chars), counts, `has_notes`, `notes_chars` |
| `summary` | `_summary` 103–138 | joined body capped at 1500 chars, full notes |
| `full` | `_full` 141–181 | every body string uncapped, image refs, table/chart counts, full notes |
| `raw` | `_raw` 184–259 | an `elements` list with `id`, `kind`, `at` = `[left, top, w, h]`, style runs, and `fill`, `outline`, `autofit`, `rotation_deg`, `parent_id` only when they are not the default |

The title is the text with the largest first-run font size, tie-broken by
width (`best_title`, 44–53), regardless of position. Speaker notes are never
truncated in `summary`/`full`/`raw`; that is deliberate, because working decks
keep their narrative in notes. `position`, `hidden` and `layout_id` are added
to every mode by `_emit_meta` (62–81).

## Tests

`tests/unit/test_normalize.py`, `test_classify.py` and `test_projection.py`
cover this path with hand-built fixtures; no network. The richer read model
(fills, rotation, groups, theme colours, UTF-16 offsets, notes Markdown) is
tested in `tests/unit/test_deck_script.py` against `tests/fake_api.py`, which
serves a scrubbed four-slide recording plus hand-built edge cases.
