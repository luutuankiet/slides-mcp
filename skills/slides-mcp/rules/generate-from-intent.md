# Generate slides from intent

Workflow for turning a prompt or document into finished slides via `create_slide`
+ `render_thumbnail`. Use when the user gives high-level direction ("slides for
Q2 QBR, here's the doc") and expects you to do the low-level legwork.

**READ FIRST:** [visual-presentation.md](visual-presentation.md) — the
renderer-not-brand contract. Plan the visual BEFORE the archetype, pass
content-driven colors per call, reach for `create_shape` before `create_image`,
drop `[IMAGE: prompt]` placeholders when no URL is handy.

## The loop (non-negotiable)

```
┌─────────────────────────────────────────────────────────────────┐
│  0. VISUAL   — plan the image anchor + palette per slide        │
│  1. PLAN     — decompose intent into slide sequence             │
│  2. CREATE   — one call per slide via create_slide              │
│  3. VERIFY   — render_thumbnail → consume ImageContent          │
│  4. ITERATE  — patch_slide text edits if content off;           │
│                delete_slide + re-create if archetype wrong      │
└─────────────────────────────────────────────────────────────────┘
```

**The VISION OUTPUT invariant:** never ship a written slide without consuming its
thumbnail. `create_slide` returns `thumbnail_url` + `slide_id` — follow up with
`render_thumbnail(slide_id)` to get native `ImageContent`. That's your eyes.
If you skip the render step, you are guessing.

## Step −1 — Establish the theme brief (pre-everything)

**This step happens ONCE per deck, before any `create_slide` call.** It's the
commitment that makes the other steps produce a *coherent deck* instead of a
pile of unrelated slides. See [theme-coherence.md](theme-coherence.md) for
the full workflow.

Three cases:

1. **Greenfield deck** (newly cloned or empty):
   - Translate user intent into brief fields (palette.surface / accent / text,
     category_set, tone, image_prompt_style).
   - `set_theme_brief(deck_url, brief)` → persists hidden meta-slide.
2. **Brownfield deck** (existing deck, e.g. user says "add slides to this deck"):
   - `get_theme_brief(deck_url)` → if `status: "absent"` proceed; else skip to step 0.
   - `extract_theme_brief(deck_url)` → proposal + evidence histograms.
   - Discuss with user: "based on 48 slides, proposing navy surface + orange
     accent; adjust?" — iterate until committed.
   - `set_theme_brief(deck_url, final_brief)`.
3. **Mid-session amendment** (user pivots):
   - `update_theme_brief(deck_url, {palette: {accent: "#NEW"}})` — forward-only.
     Existing slides unchanged; new slides pick up the amended brief.

From here on, every `create_slide` auto-reads the brief. You still pass
per-slide overrides for *deliberately* varying slides (a danger column in
red, a fullbleed cover needing white text) — Decision O is preserved.
If the response carries `brief_applied: true`, the server resolved from
the brief; `false` means either no brief exists or the deck has a
corrupted one.

## Step 0 — Visual plan (pre-PLAN)

For each slide you're about to create, decide:

1. **Visual anchor** — photo, diagram, icon, logo, chart, illustration, OR
   deliberate text-only structural variety. If you can't answer this, you
   haven't planned the slide yet.
2. **Palette overrides** — does THIS slide need a color different from the
   brief? If no (most slides), pass no color fields. If yes (danger / cover /
   one-off variation), pass the specific `pill_hex` / `accent_color_hex` /
   `title_color_hex`. Brief fills the rest.
3. **Raster availability** — do you have a URL? If yes, `image.url`. If no,
   `image.prompt` — the placeholder is a first-class deliverable. Use the
   brief's `image_prompt_style` + `tone` as tonal hints when composing the
   prompt string.

See [visual-presentation.md](visual-presentation.md) for the full rules,
pacing heuristics, and worked examples.

## Step 1 — Plan (archetype selection)

From the user's prompt + the Step-0 visual plan, decompose into a slide
sequence. Match each slide to an archetype that has a **content builder**:

| Slide role | Archetype | Content shape (essentials) |
|---|---|---|
| Deck opener / section | `cover_with_hero` | `{title, subtitle?, hero?: {url\|prompt, side?: left\|right\|fullbleed}, title_color_hex?}` |
| Narrative + visual anchor | `text_left_image_right` | `{title, body\|paragraphs, image?: {url\|prompt}, image_side?: left\|right, accent_color_hex?, body_text_color_hex?, image_caption?}` |
| 3-way parallel (comparison / pillars) | `3col_pill_cards` | `{title, lead?, columns: [{pill, body, pill_hex?}×3], pill_palette?, title_accent_hex?}` |
| 4-step process / numbered flow | `4col_numbered_flow` | `{title, columns: [{num, subtitle, body, num_color_hex?}×4], numbers_palette?, separator_color_hex?, separators?}` |
| Long-form paragraph (use sparingly) | `text_heavy_body` | `{title, paragraphs: [str, …]}` |

Those 5 are the **supported_archetypes** (returned in every `create_slide`
response). Other archetype names exist in `list_archetypes` but don't have
content builders yet — they return `reqs=[]` + a warning, leaving a blank slide
for `create_shape` / `exec_batch_update` follow-up.

**Selection heuristic (read top to bottom, stop at first match):**

1. Deck cover or section opener → `cover_with_hero`. Hero on fullbleed for
   maximum impact when you have the URL / placeholder prompt.
2. Content with an image anchor (photo, diagram, screenshot) → `text_left_image_right`.
   This is Gamma's dominant archetype — use it as the default for content slides
   unless another shape is a better fit.
3. 3 parallel ideas, categories, pillars → `3col_pill_cards` with a
   per-slide `pill_palette`.
4. 4-step process, numbered sequence, roadmap → `4col_numbered_flow` with
   `numbers_palette` cycling across the numbers.
5. Pure long-form narrative with no image hook → `text_heavy_body`. Use
   SPARINGLY — 1-2 per deck max; more = monoculture.
6. 5+ parallel ideas, tabular data, or unsupported shapes → `text_heavy_body`
   OR create a blank and compose via `create_shape` / `create_image` /
   `exec_batch_update`.

**Anti-pattern:** defaulting every content slide to `text_heavy_body`.
Re-read visual-presentation.md §5.

### Slide count — clarify before silently under-delivering

If the user specifies a target slide count (e.g. *"7 slides"*) AND enumerates
named slots that total less than N, **do not silently deliver fewer slides.**
The gap is the user's expectation, not a miscount on your part.

Two valid responses:

1. **Add a bridge slide** to reach N — an agenda, section break, quote, or
   recap. Name its role explicitly in your plan ("slide 2 = agenda between
   problem and pillars") so the user sees your interpretation.
2. **Clarify the mismatch** before creating: *"I see 6 named roles but you
   asked for 7 — want an agenda between problem and pillars, or call it
   at 6?"*

Silent under-delivery breaks trust. The user counted for a reason — either
honor the count with an explicit bridge, or surface the gap.

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

- **Archetype coverage:** 5 archetypes have content builders (listed above).
  The remaining 4 (`4col_card_with_image`, `table_slide`, `logo_strip`,
  `generic_layout`) leave the slide blank and emit a warning — compose via
  `create_shape` / `create_image` / `exec_batch_update` for those.
- **Deck dimensions:** archetype geometry is authored in 16×9-inch reference.
  `create_slide` scales at runtime based on the deck's actual pageSize
  (10×5.625, 13.33×7.5, 16×9, …) so content fits any deck without YAML edits.
  Text AUTOFIT is unsupported by Slides API on writes — very long pill /
  subtitle labels may wrap inside bounds. Prefer short labels.
- **Master branding inheritance:** `predefinedLayout: "BLANK"` inherits the
  deck's SLIDE MASTER, which often carries corporate logos + footers. The
  `create_slide` shapes stack on top of those. For a truly blank canvas either
  use a fresh deck OR strip master shapes per-slide via `exec_batch_update`.
- **Image URL reachability:** `createImage` (via `create_image` or the
  `text_left_image_right` / `cover_with_hero` builders) needs URLs Google's
  backend can fetch. Wikipedia thumbs often 400; gstatic, Unsplash direct
  URLs, and most CDN-hosted images work. When in doubt use placeholder mode
  (`image: {prompt: "..."}`) — visible first-class deliverable.

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
