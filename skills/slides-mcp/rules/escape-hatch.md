# The escape hatch — `exec_batch_update`

For Slides API requests that no bespoke tool wraps (`insertTableRows`, `updateTableCellProperties`, `updatePageProperties`, deep master-slide edits, etc.).

**Character styling (`updateTextStyle` / `updateParagraphStyle`) now has bespoke tools:** use `update_text_style` + `update_paragraph_style` (`rules/character-styling.md`). The escape hatch is for the remaining API surface where no bespoke wrapper exists.

## Safety protocol

Always start with `dry_run=True`. ALWAYS.

```
# Step 1: preview the kinds
exec_batch_update(deck_url, requests=[...], dry_run=True)
→ {
    request_kinds: ["updateTextStyle", "updateTextStyle", ...],
    preview: [first 5 requests],
  }

# Step 2: review. Does any kind match the destructive denylist?

# Step 3: fire.
exec_batch_update(
    deck_url,
    requests=[...],
    dry_run=False,
    confirm_destructive=<True iff denylist hit>,
)
```

## Destructive denylist

The tool REFUSES to fire (even with `dry_run=False`) if any of these are present and `confirm_destructive=False` (the default):

- `deleteObject`
- `deleteSlide`
- `deleteText`
- `deleteTableRow`
- `deleteTableColumn`
- `deleteParagraphBullets`
- `replaceAllText` — because it can silently over-match across slots

Set `confirm_destructive=True` only after you've verified the request list is what you expect.

NOT destructive (creates, doesn't destroy):

- `duplicateObject`
- `replaceAllShapesWithImage`

## Audit log

Every call — fire, dry-run, or refusal — writes ONE JSONL line to:

```
$XDG_CONFIG_HOME/slides-mcp/audit.jsonl
```

Format (compact):

```json
{"ts":"2026-04-22T10:15:23Z","deck_id":"1iGC...","dry_run":false,"confirmed":false,"kinds":{"updateTextStyle":12,"updateShapeProperties":3},"applied":15}
```

Kinds + counts only — NEVER request bodies. IOError on write is swallowed; an audit-log failure does not break the tool call.

## Error propagation

Errors come back verbatim from Google. Their error message typically names the offending request index and field, e.g.:

```
Invalid requests[3].updateTextStyle: The object (g1e34ab500_0_17) does not have text.
```

Parse the index, inspect your `requests[3]`, fix.

## When to use vs. when NOT to

| Use `exec_batch_update` | Don't — use the bespoke tool |
|-------------------------|------------------------------|
| Table operations (rows, cells, columns) | N/A — no bespoke wrapper |
| Slide properties, master edits | N/A — no bespoke wrapper |
| One-off Slides API kinds not in any bespoke tool | |
| Anything with an async batch pattern | |
| `updateShapeProperties` (fill beyond theme) | new shape → `create_shape` |
| ~~`updateTextStyle` / `updateParagraphStyle`~~ | → `update_text_style` / `update_paragraph_style` (v0.5.0) |
| ~~Bulk font-family changes~~ | → loop `update_text_style` per object_id with `range="all"` |
| Text content change | → `patch_slide` |

## Example: insert a table row

```
requests = [{
    "insertTableRows": {
        "tableObjectId": table_id,
        "cellLocation": {"rowIndex": 2, "columnIndex": 0},
        "insertBelow": True,
        "number": 1,
    }
}]

# 1. Dry-run — preview kinds
exec_batch_update(deck_url, requests=requests, dry_run=True)
# → kinds: ["insertTableRows"], no destructive hit

# 2. Fire
exec_batch_update(deck_url, requests=requests, dry_run=False)
```

## Wrappers — what exists now

The plan-advisor rule (LOG-006) was **ship the hatch first, watch real usage,
build narrow wrappers matching observed patterns.** That played out:

- `updateTextStyle` usage was dominant enough to warrant a wrapper →
  `update_text_style` (v0.5.0, `rules/character-styling.md`).
- `updateParagraphStyle` followed the same path → `update_paragraph_style`.
- Table cell / row / column operations remain escape-hatch-only; usage is
  infrequent and the shape changes per case.

If a new pattern recurs 3+ times across sessions, file it as a follow-up
tool — don't speculate-wrap.
