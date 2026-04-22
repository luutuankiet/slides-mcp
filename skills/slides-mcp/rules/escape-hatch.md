# The escape hatch — `exec_batch_update`

For Slides API requests that no bespoke tool wraps (`updateTextStyle`, `insertTableRows`, `updateTableCellProperties`, `updatePageProperties`, etc.).

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
| `updateTextStyle` (font, color, weight) | text CONTENT change → `patch_slide` |
| `updateShapeProperties` (fill beyond theme) | new shape → `create_shape` |
| Table operations (rows, cells, columns) | N/A |
| Slide properties, master edits | N/A |
| Bulk font-family changes across many objects | |
| One-off operation that doesn't recur | |
| Anything with an async batch pattern | |

## Example: bulk title-font change

```
# 1. Find the slides
slides = get_deck_outline(deck_url).slides

# 2. For each, collect the title objectId
requests = []
for s in slides:
    dsl = get_slide(deck_url, s.id).dsl_yaml
    title_id = dsl["_object_ids"]["title"]
    requests.append({
      "updateTextStyle": {
        "objectId": title_id,
        "textRange": {"type": "ALL"},
        "style": {"fontFamily": "Inter"},
        "fields": "fontFamily",
      }
    })

# 3. Dry-run
exec_batch_update(deck_url, requests=requests, dry_run=True)
# Review. No destructive kinds? Good.

# 4. Fire
exec_batch_update(deck_url, requests=requests, dry_run=False)
```

## Wrappers?

`update_text_style` / `update_shape_fill` convenience wrappers are deliberately NOT built. The plan-advisor call (LOG-006) was: **ship the hatch first, watch real usage, build narrow wrappers matching observed patterns.** If a specific pattern recurs, file it as a follow-up; don't speculate-wrap.
