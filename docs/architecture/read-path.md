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

All five read tools live in `src/slides_mcp/server.py` (847 lines):

| tool | lines | notes |
|---|---|---|
| `auth_status` | 257–260 | calls `auth.credentials_info()`, never exposes the token |
| `get_deck_outline` | 263–281 | thin wrapper over `_project_deck_outline` (123–151) |
| `read_slides` | 284–360 | the primary read tool; validates `detail` and `include_images` itself |
| `search_deck` | 363–443 | checks title, then body, then notes; reports the **first** match per slide only |
| `render_thumbnail` | 446–470 | returns an MCP `Image`; fetches bytes from a short-lived Google URL |

`_project_deck_outline` is shared with the write path: the `post_state`
envelope reuses it, so outline changes show up in both places.

## One whole-deck GET per call

`slides_api.get_presentation` (`slides_api.py:117`) fetches the whole deck with
the `DECK_OUTLINE_FIELDS` field mask (lines 30–49), even when the caller asked
for one slide. Selection happens afterwards, in memory. `get_slide` and
`SLIDE_FULL_FIELDS` exist (lines 51–69, 123–131) but nothing calls them.

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

- Geometry is in inches. **Rendered size = intrinsic `size` × `transform.scale`**
  (`_extract_transform`, 63–89; applied at 162–165). Using `size` alone
  overstates scaled elements; the docstring there has the worked example.
- A shape is `kind="text"` if it has non-blank text or is a `TEXT_BOX`
  (line 215); otherwise `"shape"`.
- Groups keep children; `flatten` (239–247) recurses them away for the
  classifier and projections.
- Speaker notes come from the notes page's `BODY` placeholder (`extract_notes`,
  250–267). Hidden = `slideProperties.isSkipped` (275–281).
- Theme colours are not resolved; only explicit RGB becomes a hex (101–110).

## Classify

`src/slides_mcp/classify.py:42–90` assigns one archetype label per slide from
element topology, not the layout name: tables, charts, logo strips,
hero covers, 4- and 3-column layouts, text+image, text-heavy, else
`generic_layout`. First match wins, most specific first. The label is a hint
for choosing which slides to read, nothing depends on it.

## Projection

`src/slides_mcp/projection.py` has one dispatcher, `project` (238–267), over
four modes:

| mode | function | what it adds |
|---|---|---|
| `outline` | `_outline` 84–100 | title (≤120 chars), counts, `has_notes`, `notes_chars` |
| `summary` | `_summary` 103–138 | joined body capped at 1500 chars, full notes |
| `full` | `_full` 141–181 | every body string uncapped, image refs, table/chart counts, full notes |
| `raw` | `_raw` 184–235 | an `elements` list with `id`, `kind`, `at` = `[left, top, w, h]`, style runs |

The title is the text with the largest first-run font size, tie-broken by
width (`best_title`, 44–53), regardless of position. Speaker notes are never
truncated in `summary`/`full`/`raw`; that is deliberate, because working decks
keep their narrative in notes. `position`, `hidden` and `layout_id` are added
to every mode by `_emit_meta` (62–81).

## Tests

`tests/unit/test_normalize.py`, `test_classify.py` and `test_projection.py`
cover this path with hand-built fixtures; no network.
