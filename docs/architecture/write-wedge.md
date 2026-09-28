---
title: Write wedge (exec_batch_update, add_section_footers)
covers: where writes to a deck happen, the destructive-request guard, dry run, how post_state and affected_slide_ids are built, how section footers are placed
verified: 2026-09-28
---

# Write wedge

Two tools, both in `src/slides_mcp/server.py`. One path to the API:

```
add_section_footers ──builds requests──▶ exec_batch_update ──▶ slides_api.batch_update
                                              │
                                              └─▶ re-read deck ─▶ post_state envelope
```

Line numbers are **a starting point, not an address**. Confirm by what the
code says, and re-date this page if you correct a range.

## `exec_batch_update` (server.py 473–624)

The agent passes Slides API Request dicts; the server forwards them verbatim.
In order:

1. Validate `post_state` and non-empty `requests` (527–532).
2. Take each request's first key as its kind; intersect with
   `DESTRUCTIVE_KINDS` (34–44): `deleteObject`, `deleteSlide`, `deleteText`,
   `deleteTableRow`, `deleteTableColumn`, `deleteParagraphBullets`,
   `replaceAllText`, `replaceAllShapesWithImage`,
   `replaceAllShapesWithSheetsChart`.
3. `dry_run=True` returns kinds, the first five requests and the destructive
   kinds found, without calling the API (537–546).
4. Destructive kinds without `confirm_destructive=True` return
   `isError: True` with a warning; **they do not raise** (548–558).
5. Call `slides_api.batch_update` (`slides_api.py:163–193`). A 403 is re-raised
   with a hint to re-run `slides-mcp-auth` for a write-scope token (562–573).
6. `post_state="none"` returns now. Otherwise re-read the deck **once** and
   build `deck_outline` plus, for `summary`/`full`, a projection of each
   affected slide (586–624).

## `affected_slide_ids` (server.py 171–251)

Derived from the requests, not reported by Google. It collects slide ids from
`pageObjectId`, `pageObjectIds`, `elementProperties.pageObjectId`, `objectId`
(slide or element), `objectIds` / `childrenObjectIds`, and from
`createSlide` / `duplicateObject` replies. An unscoped `replaceAllText` marks
every slide.

Element ids are mapped to slides using the deck **as re-read after the
write**. An element the batch deleted is no longer in that deck, so a
`deleteObject` on an element contributes no slide id.

## `add_section_footers` (server.py 637–838)

Turns `[{name, slide_range | slide_ids | slide_positions}]` into four
requests per slide: `createShape` (TEXT_BOX), `insertText`,
`updateShapeProperties` setting `autofit` to `NONE`, then `updateTextStyle`
(9 pt grey). It then calls `exec_batch_update` directly and adds
`_proof_tool`, `sections_applied`, `footers_added` and `skipped_slide_ids`.

- **Idempotent ids.** Footer objectId is `slides_mcp_footer_` + the last 12
  characters of the slide id (743). Re-runs find existing footers by that
  prefix (720–725) and either delete-then-recreate
  (`overwrite_existing=True`, which needs `confirm_destructive=True`) or skip.
- **Fixed geometry.** Position comes from constants at 628–634 that assume a
  16:9 deck, 10 × 5.625 in. The page size of the actual deck is not read.
- **Autofit order.** The `autofit: NONE` update is emitted after `insertText`
  and before any other shape-property update. The shipped skill
  (`skills/slides-mcp/SKILL.md`) tells agents to do the same in their own
  batches.

## Tests

`tests/unit/test_write_wedge.py` monkeypatches `slides_api` and covers the
destructive guard, dry run, each `post_state` level, the 403 message,
affected-slide extraction and footer request building.
