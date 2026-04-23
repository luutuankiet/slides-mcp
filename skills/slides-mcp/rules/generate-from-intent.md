# Generate slides from intent

Workflow for turning a prompt or document into finished slides via `create_slide`
+ `render_thumbnail`. Use when the user gives high-level direction ("slides for
Q2 QBR, here's the doc") and expects you to do the low-level legwork.

## The loop (non-negotiable)

```
┌─────────────────────────────────────────────────────────────────┐
│  1. PLAN     — decompose intent into slide sequence             │
│  2. CREATE   — one call per slide via create_slide              │
│  3. VERIFY   — render_thumbnail → consume ImageContent          │
│  4. ITERATE  — patch_slide text edits if content off;           │
│                delete + re-create if geometry / archetype wrong │
└─────────────────────────────────────────────────────────────────┘
```

**The VISION OUTPUT invariant:** never ship a written slide without consuming its
thumbnail. `create_slide` returns `thumbnail_url` + `slide_id` — follow up with
`render_thumbnail(slide_id)` to get native `ImageContent`. That's your eyes.
If you skip the render step, you are guessing.

## Step 1 — Plan

From the user's prompt, decompose into a slide sequence. For each slide pick:

| Slide role | Archetype | Content shape |
|---|---|---|
| Cover / title | `cover_with_hero` | `{title, subtitle?}` |
| Long-form paragraph content | `text_heavy_body` | `{title, paragraphs: [str, …]}` |
| 3-way comparison / three pillars | `3col_pill_cards` | `{title, lead?, columns: [{pill, body}, ×3]}` |

Those are the **supported_archetypes** (returned in every `create_slide`
response). Other archetype names exist in `list_archetypes` but don't have
content builders yet — they return `reqs=[]` + a warning, leaving a blank slide
for `create_shape` / `exec_batch_update` follow-up.

**Selection heuristic:**
- 1 idea with narrative → `text_heavy_body`
- 3 parallel ideas → `3col_pill_cards`
- Section opener or deck title → `cover_with_hero`
- 4+ parallel ideas, tabular data, logos, or image-heavy → create a blank
  slide (`exec_batch_update` with `createSlide`) and `create_shape` the pieces

## Step 2 — Create

```python
# one slide at a time — atomic, easy to verify, easy to revert
create_slide(
    deck_url="https://docs.google.com/presentation/d/…/edit",
    archetype="3col_pill_cards",
    content={
        "title": "Compact reads",
        "lead": "Every tool compresses raw JSON into YAML.",
        "columns": [
            {"pill": "Clean DSL", "body": "~100–150 tok/slide."},
            {"pill": "Deck outline", "body": "Whole-deck index at ~20 tok/slide."},
            {"pill": "Structural grep", "body": "search_deck + list_slides_by."},
        ],
    },
    slide_id="cap_read",  # optional but recommended — lets you reference later
)
```

`insertion_index=-1` (default) appends at deck end. Pass an explicit index when
inserting mid-deck.

The call returns:
```
{
  slide_id, deck_id, archetype, insertion_index,
  applied_request_count,   # → ~28 requests for a populated pill_cards slide
  thumbnail_url,           # short-lived; use render_thumbnail for agent consumption
  warnings,                # missing required slots, unsupported archetype, etc.
  supported_archetypes,    # the ones with a content builder
  next_step_hint,          # literal instruction to render the thumbnail
}
```

## Step 3 — Verify (VISION OUTPUT)

**Always, immediately:**

```python
render_thumbnail(deck_url=…, slide_id="cap_read", size="MEDIUM")
```

The returned `ImageContent` is consumed natively by the agent. Look at it. Ask
yourself:
- Is the title present + readable?
- Are all expected slots populated?
- Does nothing clip off-slide? (Common: decks sized 13.33×7.5" not 16×9 — text
  at `top_in > 7.0` will clip. If you see clipping, reduce top_in in the next
  iteration's `content` or post-patch the geometry.)
- Is the theme color landing on pills / accents? (Off = theme didn't resolve
  the role — check `sub_theme` arg.)

If the output is wrong, iterate. If right, move to next slide.

## Step 4 — Iterate

| Problem | Fix |
|---|---|
| Typo / wording change | `patch_slide` with text edit |
| Shape clipping off-slide | `patch_slide` with `elements[].at` (move shape up/left) |
| Wrong archetype choice | `exec_batch_update` `deleteObject` on slide_id + `create_slide` with correct archetype |
| Content missing (warning in response) | `patch_slide` or `create_shape` the missing slot |

Don't try to "salvage" a wrong-archetype slide with 10 patches — delete and
re-create. `create_slide` is cheap (1 call); iteration friction over archetype
choice is expensive.

## Limits to know (as of current slides-mcp)

- **Archetype coverage:** only the 3 listed above have content builders. The
  other 6 (`4col_numbered_flow`, `4col_card_with_image`, `table_slide`,
  `logo_strip`, `text_left_image_right`, `generic_layout`) leave the slide
  blank and emit a warning — compose via `create_shape` / `exec_batch_update`.
- **Deck dimensions:** archetype geometry assumes 16×9 inches but Google
  Slides defaults to 13.33×7.5 widescreen. Columns / body positioned at
  `top_in > 7.0` will clip on default decks. Either pick short content or
  resize the deck page first via `exec_batch_update` with `updatePageProperties`.
- **Master branding inheritance:** `predefinedLayout: "BLANK"` inherits the
  deck's SLIDE MASTER, which often carries corporate logos + footers. The
  `create_slide` shapes stack on top of those. For a truly blank canvas either
  use a fresh deck OR strip master shapes per-slide via `exec_batch_update`.
- **No hero image in cover_with_hero yet:** hero slot is acknowledged but
  unimplemented. Use `exec_batch_update` with `createImage` when you need one.

## Shape-only additions (not a full archetype)

If the user asks for a single shape added to an existing slide, skip
`create_slide` and use `create_shape` directly. `create_slide` is for slide-
level composition from archetype + content; `create_shape` is for surgical
additions to slides that already exist.

## Example: 3-slide deck from a prompt

User: *"Draft 3 slides covering compact reads, edit path, theme hygiene."*

```
# 1. Plan
#    - 3 parallel pillars → all 3col_pill_cards
#    - slide_ids: r1 / r2 / r3 for chaining

# 2. Create slide 1
result_1 = create_slide(deck_url, "3col_pill_cards", {
    "title": "Compact reads",
    "lead": "…",
    "columns": […, …, …],
}, slide_id="r1")

# 3. Verify
render_thumbnail(deck_url, "r1")
# [consume ImageContent; confirm content on slide]

# 4. Next slide. Repeat.
result_2 = create_slide(deck_url, "3col_pill_cards", {…}, slide_id="r2")
render_thumbnail(deck_url, "r2")
…
```

Do NOT batch-create all 3 slides before rendering any thumbnails. You lose
per-slide verification, and if slide 1 is off-archetype you've compounded the
error by 3×.
