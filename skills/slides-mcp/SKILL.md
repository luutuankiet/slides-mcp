---
name: slides-mcp
description: Editing Google Slides through the slides-mcp server - when to use run_deck_script (edits that depend on deck data), write_speaker_notes, or raw exec_batch_update requests, plus the Request cheat sheet. Skip this skill if you are only reading decks.
---
# Composing exec_batch_update requests

A reference for using `exec_batch_update` to make legwork-shaped edits to Google Slides via slides-mcp. The agent writes Slides API Request dicts directly — this doc is the cheat sheet.

> ⚠️ This tool is for **legwork** (bulk text edits, footers, global formatting). It accepts that pixel-perfect layout is impossible without visual feedback. If you need creative authorship, the answer is the human in the Slides UI.

## Pick the tool first

| The edit | Use |
|---|---|
| Depends on what is in the deck: re-theme dark slides, swap a palette, restyle every title, resize every box of one kind, write notes from data | `run_deck_script` |
| Speaker notes, with formatting | `write_speaker_notes` (Markdown in, real bold/italic/headers/bullets out) |
| A handful of requests whose ids you already know | `exec_batch_update` |
| A footer on every slide | `add_section_footers` |
| A diagram (SVG you write) or a picture (public URL) on a slide | `place_image` |

**Prefer `run_deck_script` whenever you would otherwise read the deck, compute
requests in your head and paste them back.** The script reads the deck inside
the server, so the deck never enters your context, and the geometry, fills,
theme colours and font weights it sees are already resolved (rotation and
groups applied, theme colours turned into hex, a transparent box reported as
`fill.kind == "none"` rather than white).

```js
// input: {"map": {"#1A73E8": "#0B8043"}}
let n = 0;
for (const slide of deck.slides) {
  for (const el of slide.elements) {
    const to = el.fill && input.map[el.fill.hex];
    if (to) { emit(setFill(el, to, el.fill.alpha)); n++; }
    for (const r of el.runs || []) {
      if (input.map[r.color]) { emit(styleText(el, {color: input.map[r.color]}, r)); n++; }
    }
  }
}
return {queued: n};
```

- `dry_run` is **true by default**. Read the per-slide preview (kinds, colours
  and fonts before and after), then call again with `dry_run=false`.
- Pass data through `input`, never by splicing it into the script string.
- `await commit()` only when a later step needs to see what an earlier one
  created; each phase is one atomic batch.
- A real apply returns thumbnails of up to 3 slides it touched, so you can
  check the result by eye; `render_slides="1-3"` picks the slides instead.
  Every write tool does this except `write_speaker_notes`. Pass
  `receipt="off"` on bulk edits you trust, `receipt="large"` to read small
  text.
- Read the `warnings`: a font change that drops a weight, or text that will
  likely overflow a fixed-size box.

The rest of this page is the Request reference, which you need for
`emit()` inside a script as much as for `exec_batch_update`.

## Quick reference

### objectId discovery

Slides API edits target objectIds. Two shapes:

- **Slide objectIds** — call `get_deck_outline(deck_url)` → each slide has `slide_id` field. Used in `pageObjectId` (for create-on-slide) or `pageObjectIds` (for `replaceAllText` scoping).
- **Element objectIds** — call `read_slides(deck_url, slides=[sid], detail="raw")` → each element has `id` field. Used in element-level `update*`/`deleteObject` requests.

### EMU cheat sheet

Slides API uses **EMU** (English Metric Unit). 1 inch = 914400 EMU.

| Item | EMU |
|---|---|
| 16:9 deck width | 9144000 (10 in) |
| 16:9 deck height | 5143500 (5.625 in) |
| 1 inch | 914400 |
| 1 cm | 360000 |
| 1 pt | 12700 |

### Common Request kinds

| Kind | When | Destructive? |
|---|---|---|
| `createShape` | Add a TEXT_BOX, RECTANGLE, ELLIPSE, etc. | No |
| `createImage` | Add an image from URL | No |
| `insertText` | Add text into a shape (after `createShape`) | No |
| `updateShapeProperties` | Set fill/border/autofit | No |
| `updateTextStyle` | Set font, size, color, bold | No |
| `updatePageElementTransform` | Move/resize/rotate | No |
| `replaceAllText` | Find-and-replace across slide(s) | **Yes** |
| `deleteObject` | Remove a shape or slide | **Yes** |
| `deleteSlide` | Remove an entire slide | **Yes** |
| `createSlide` | Add a new slide | No |
| `duplicateObject` | Clone a shape or slide | No |

For every destructive kind, pass `confirm_destructive=True` to `exec_batch_update`.

### The autofit:NONE invariant

When you create a `TEXT_BOX` and call `insertText`, Google Slides auto-applies non-NONE autofit which causes subsequent `updateShapeProperties` calls to fail with an opaque error. **Always emit:**

```python
{
  "updateShapeProperties": {
    "objectId": "<your shape's id>",
    "shapeProperties": {"autofit": {"autofitType": "NONE"}},
    "fields": "autofit.autofitType"
  }
}
```

… AFTER `insertText` and BEFORE any other `updateShapeProperties`. Lesson from a server-side regression caught in 2026-04 — trust the rule, don't relitigate it.

## Worked examples

### Example 1 — Rename a deck title

```python
exec_batch_update(
    deck_url="https://docs.google.com/presentation/d/.../edit",
    requests=[{
        "replaceAllText": {
            "containsText": {"text": "Old Title", "matchCase": True},
            "replaceText": "New Title"
        }
    }],
    confirm_destructive=True,  # replaceAllText is destructive
    post_state="summary"
)
```

`replaceAllText` is deck-wide unless you pass `pageObjectIds: [<slide_id>, ...]` to scope it.

### Example 2 — Add a timestamp footer to every slide (manual; for section-aware footers use `add_section_footers`)

```python
# 1. Get slide IDs
outline = get_deck_outline(deck_url)
slide_ids = [s["slide_id"] for s in outline["slides"]]

# 2. Build request list
DECK_W = 9144000
DECK_H = 5143500
FOOTER_W = 3000000
FOOTER_H = 250000
MARGIN = 100000

requests = []
for sid in slide_ids:
    oid = f"my_timestamp_{sid[-8:]}"
    requests.append({
        "createShape": {
            "objectId": oid,
            "shapeType": "TEXT_BOX",
            "elementProperties": {
                "pageObjectId": sid,
                "size": {"width": {"magnitude": FOOTER_W, "unit": "EMU"},
                         "height": {"magnitude": FOOTER_H, "unit": "EMU"}},
                "transform": {"scaleX": 1, "scaleY": 1,
                              "translateX": DECK_W - FOOTER_W - MARGIN,
                              "translateY": DECK_H - FOOTER_H - MARGIN,
                              "unit": "EMU"}
            }
        }
    })
    requests.append({
        "insertText": {"objectId": oid, "text": "Last updated: 2026-05-07", "insertionIndex": 0}
    })
    requests.append({  # autofit:NONE invariant — see above
        "updateShapeProperties": {
            "objectId": oid,
            "shapeProperties": {"autofit": {"autofitType": "NONE"}},
            "fields": "autofit.autofitType"
        }
    })
    requests.append({
        "updateTextStyle": {
            "objectId": oid,
            "textRange": {"type": "ALL"},
            "style": {"fontSize": {"magnitude": 9, "unit": "PT"}},
            "fields": "fontSize"
        }
    })

# 3. Dry run first
preview = exec_batch_update(deck_url, requests, dry_run=True)
# inspect preview["request_kinds"], preview["destructive_kinds_detected"]

# 4. Fire
result = exec_batch_update(deck_url, requests, post_state="summary")
# result["post_state"]["deck_outline"]   = whole deck index
# result["post_state"]["slides"]         = each touched slide projected at "summary" detail
# result["affected_slide_ids"]           = ["g1", "g2", …]
```

### Example 3 — Change the font on every title

An edit that depends on what is in the deck, so it is a script, not a loop of
`read_slides` calls:

```python
run_deck_script(
    deck_url,
    script="""
      let n = 0;
      for (const slide of deck.slides)
        for (const el of slide.find(e => ["TITLE", "CENTERED_TITLE"].includes(e.placeholder))) {
          emit(styleRuns(el, null, {fontFamily: input.font}));
          n++;
        }
      return {titles: n};
    """,
    input={"font": "Inter"},
)  # dry run: read the preview and warnings, then apply it without resending:

run_deck_script(deck_url, plan_id=preview["plan_id"], dry_run=False)
```

Applying a plan re-runs the stored script against the deck as it is now, so a
colleague's edits since the dry run are picked up. Plans last 1 hour and apply
once.

`styleRuns` keeps each run's weight across the font change.

### Example 4: Put a diagram into the user's placeholder

The user has a reference slide with a box holding the text `{{DIAGRAM}}`
(no outline: the image inherits it). Copy the slide, then place SVG into the
copy's box:

```python
exec_batch_update(deck_url, [
    {"duplicateObject": {"objectId": "ref_slide", "objectIds": {"ref_slide": "flow_slide"}}},
])  # the copy's marker box is now on flow_slide

place_image(
    deck_url,
    slide_id="flow_slide",
    placeholder="{{DIAGRAM}}",
    svg="""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 600 200">
      <rect x="20" y="60" width="160" height="80" rx="12" fill="#e3ecfa" stroke="#3366cc"/>
      <rect x="420" y="60" width="160" height="80" rx="12" fill="#e6f4ea" stroke="#188038"/>
      <line x1="180" y1="100" x2="420" y2="100" stroke="#444" stroke-width="3"/>
      <text x="100" y="107" font-family="sans-serif" font-size="22" text-anchor="middle">Draft</text>
      <text x="500" y="107" font-family="sans-serif" font-size="22" text-anchor="middle">Review</text>
    </svg>""",
    confirm_destructive=True,  # replacing the marker box is a destructive kind
)
```

- The image keeps the box's object id; the reply's thumbnail shows it in place.
- Use `sans-serif`, `serif` or `monospace`. Text the server has no font for
  is listed in `warnings`.
- `error.kind == "marker"` means the marker is not in a shape on that slide.
  A marker on the slide's layout or master cannot be reached.
- No placeholder? Pass `box={"x": 60, "y": 80, "width": 400, "height": 200}`
  (points) copied from a reference slide's `read_slides(detail="raw")`.
- SVG needs the hosted server. `image_url=` with a public PNG, JPEG or GIF
  works anywhere.

## Tips

- **Always `dry_run=True` first** for non-trivial batches. The preview surfaces `destructive_kinds_detected` so you can decide whether to pass `confirm_destructive=True`.
- **Pick the right `post_state`**: `"none"` for blind fire-and-forget, `"outline"` to confirm slide structure didn't break, `"summary"` (default) to read back text changes, `"full"` for debugging.
- **Footer positioning is approximate.** Pixel-precise placement requires visual feedback (Slides UI) — agents work blind here.
- **OAuth scope**: write tools need `presentations` (not `presentations.readonly`). v2.1 default mints readonly tokens; re-run `slides-mcp-auth` with a write-scope OAuth client to upgrade.

## Reference

- Slides API Request reference: <https://developers.google.com/slides/api/reference/rest/v1/presentations/request>
- slides-mcp v2.1.0 release notes: [`releases/v2.1.0.md`](../../releases/v2.1.0.md)
