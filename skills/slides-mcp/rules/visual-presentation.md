# Visual presentation — the renderer-not-brand contract

**Read this BEFORE generate-from-intent.md.** The rules below are the
difference between a presentation and a markdown-rendered text doc.

## The last mile — typography + variant selection (v0.5.0)

The difference between a slide that reads as a DECK and a slide that reads as
an export isn't raster imagery — it's **typographic depth** and **mood coherence**.
Raster images (stock, AI-gen, logos) are the LONG mile; the placeholder
`[IMAGE: prompt]` is the final deliverable for raster needs, not a stepping
stone (Decision P).

Two v0.5.0 tool families cover the real last mile:

- **Character-range styling** — `update_text(scope="run"|"paragraph")`
  (see `rules/character-styling.md`). Pop a word, italicize a quote, space
  out a title. Vanilla primitives — fonts, colors, alignment — land the
  editorial depth that image-heavy decks lean on but native-typography decks
  get from structure alone.
- **Variant selection** — `propose_brief_variants` + `generate_variants` +
  `lock_variant` (see `rules/variant-generation.md`). When intent is moody
  but underspecified, render 3 options, let the user pick, then commit.
  Beats guessing a mood and shipping it blind.

**Rule of thumb:** if your output is "functional but flat", look BEFORE raster.
(a) Is the typographic hierarchy doing work? (b) Did you pick the mood, or
pick the first brief you thought of? Typography and variant selection close
that gap with no external deps, no API keys, no bytes to fetch.

## 1. MCP is a renderer, not a brand

slides-mcp has NO opinion on what your slides should look like. It exposes a
set of archetype-shaped builders + primitives, and YOU — the agent — pick the
visual story per slide. Colors, image anchors, accent placement, hero side,
dividers, separators: all decided at call-site from the user's intent.

The theme is a FALLBACK, not a brand. If you don't pass a color, the theme's
`brand_accent` resolves. That resolution exists so tooling doesn't break when
a role is missing. It is NOT the intended UX.

**Rule:** every visual decision is either:

- derived from the user's prompt (brand terms, subject matter, tone), OR
- driven by archetype semantics (numbered steps cycle a palette; pills split
  roles).

NEVER default to "the theme color" as the answer. Theme fallback is what fires
when you didn't think about color. Think about color. Pass the hex.

## 2. Content-driven visual identity (Decision O)

Every Phase 1 builder + tool accepts visual-decision slots at call-site.
Use them:

| Builder / tool | Visual slots (content keys) |
|---|---|
| `3col_pill_cards` | `pill_palette` (cycled) · per-col `pill_hex` · `title_accent_hex` |
| `text_left_image_right` | `image_side` ("left"/"right") · `accent_color_hex` · `body_text_color_hex` · `image_caption` |
| `cover_with_hero` | `hero.side` ("left"/"right"/"fullbleed") · `title_color_hex` · `subtitle_color_hex` |
| `4col_numbered_flow` | `numbers_palette` (cycled) · per-col `num_color_hex` · `separator_color_hex` · `separators` (bool) |
| `create_image` | `image_url` (raster) OR `image_prompt` (placeholder) |
| `create_shape` | `shape_type` · `fill_role` OR `fill_hex` |

Pick values per slide. Do not reach for "the brand color" unless the user's
prompt specifies one. The agent is the art director.

## 3. Shapes-first, image-second (Decision P)

Before calling `create_image`, ask: *"is this genuine raster — photo,
screenshot, diagram, logo — or decoration?"*

If decoration: **use `create_shape`.** The Slides API shape types cover most
Gamma-style flourishes natively:

| Decoration | Shape type |
|---|---|
| Header bars, dividers, rules | `RECTANGLE` (thin) |
| Card backgrounds, color panels | `RECTANGLE` |
| Rounded pills, category headers | `ROUND_RECTANGLE` |
| Color dots (palette accent above pill) | `ELLIPSE` |
| Arrows, flow indicators | `RIGHT_ARROW`, `ARROW_CALLOUT`, etc. |

`create_image` is reserved for real rasters: photos, product screenshots,
diagrams from stock, brand logos that exist as PNG / JPG / GIF.

## 4. Placeholder-as-deliverable

When the slide CALLS FOR a raster but you don't have a URL, DO NOT skip the
image slot. Drop a placeholder:

```python
create_image(
    deck_url, slide_id,
    at=[1.0, 2.0, 6.0, 4.0],
    image_prompt="a stacked bar chart showing Q2 revenue by region",
)
```

This emits a RECTANGLE with the literal text `[IMAGE: a stacked bar chart
showing Q2 revenue by region]`. The slide renders as-is — placeholder visible
in the thumbnail, intent preserved. The user (or a downstream image-gen pass)
fills in the real image later.

Same pattern inside builder content: use `image: {prompt: "..."}` instead of
`image: {url: "..."}`:

```python
create_slide(deck_url, "text_left_image_right", content={
    "title": "Daily digest",
    "body": "Morning summary of key metrics.",
    "image": {"prompt": "a morning dashboard on a phone screen"},
})
```

This is a FIRST-CLASS deliverable. Do not apologize for "not having an image."
Do not skip the slot. Drop the placeholder, move on.

## 5. Image planning comes BEFORE archetype selection

The Round-1 intern antipattern: pick archetype → realize no image handy →
fall back to text-only. Output: 8 `text_heavy_body` slides from the same
prompt Gamma produced 16 image-anchored slides from.

**Correct order:**

1. Read the user's prompt + source material.
2. For each slide, ask: *what's the visual anchor?* — photo, diagram, icon,
   logo, chart, illustration, OR deliberate text-only structural variety.
3. Pick archetype based on that visual + content shape:
   - Image anchor + narrative → `text_left_image_right` or `cover_with_hero`
   - Parallel categories, no image → `3col_pill_cards` or `4col_numbered_flow`
   - Long-form exposition (rare, use sparingly) → `text_heavy_body`
   - Deck opener → `cover_with_hero` (fullbleed hits hardest)
4. Supply image via URL if known, else placeholder prompt. **Never skip the
   image slot on an archetype that has one.**

## 6. Structural variety across a deck

Avoid monoculture. Eight `text_heavy_body` in a row reads like a word doc.
Eight `3col_pill_cards` in a row is visual fatigue in the opposite direction.

**Pacing heuristic for a ~10-slide deck:**

```
1   cover_with_hero (fullbleed)          deck opener, hero image
2   text_left_image_right                first content with visual anchor
3   3col_pill_cards                      structured comparison
4   text_left_image_right (image left)   variety in side
5   4col_numbered_flow                   process / sequence
6   3col_pill_cards                      contrast with slide 3
7   text_left_image_right                narrative + viz
8   text_heavy_body                      deep-dive / details (use sparingly)
9   4col_numbered_flow                   roadmap / next steps
10  cover_with_hero (side mode)          thank-you / contact
```

Don't follow literally — use it as a variety check. **No two adjacent slides
should share an archetype** unless you have a strong narrative reason (e.g.
two comparison slides back-to-back).

## 7. Variety audit

After generating a deck, run `get_deck_outline` and scan the `archetype`
column. If you see `text_heavy_body` more than twice in a row, redistribute.
If every slide is the same archetype, you failed the visual brief — consider
`delete_slide` + re-create the ones that can use a different shape.

## 8. Examples

### Prompt → palette + archetype

> *"Three new capabilities for v2: semantic layer, empowered users, untapped
> potential."*

Decision: parallel triad → `3col_pill_cards`. Content-driven palette
`["#DB4437", "#0F9D58", "#4285F4"]` (Google quadra signals "3 product
capabilities"). `title_accent_hex` matches col1. No image. Structural variety
alone carries the slide.

### Prompt → placeholder when no URL

> *"Cover slide with a hero image showing a city skyline at dusk."*

Decision: `cover_with_hero` with:

```python
content = {
    "title": "The city at dusk",
    "subtitle": "Urban analytics, nightly",
    "hero": {"prompt": "a city skyline at dusk, dark blue hour",
             "side": "fullbleed"},
    "title_color_hex": "#FFFFFF",
    "subtitle_color_hex": "#DDDDDD",
}
```

Deck renders as-is with `[IMAGE: a city skyline at dusk, dark blue hour]`
visible. User swaps in a real image later.

### Prompt → structural variety over images

> *"Walkthrough of our 4-step onboarding flow."*

Decision: `4col_numbered_flow`. Num labels 01/02/03/04 cycle colors via
`numbers_palette: ["#DB4437", "#F4B400", "#0F9D58", "#4285F4"]`. Separator
color `#CCCCCC`. No image — structure IS the visual.

### Prompt → image + accent on content slide

> *"Slide introducing the agent's proactive notifications feature."*

Decision: `text_left_image_right` with `image_side: "right"`, body on left.
`accent_color_hex: "#4285F4"` draws a blue bar under the title (content-
driven, not theme). `image: {prompt: "a notification popup on a mobile
dashboard screen"}` if no screenshot URL is at hand.

## 9. Anti-patterns (🚨 = critical)

- 🚨 **Text-heavy monoculture** — defaulting every slide to `text_heavy_body`.
  Use image-anchored archetypes when the content has any visual hook.
- 🚨 **Brand-default reach** — picking theme color because no palette was
  provided. Pick FROM the user's prompt or from a content-appropriate palette.
- 🚨 **Skipped image slots** — leaving a blank where a raster belonged.
  Always drop a placeholder prompt.
- **Archetype-first, visual-last ordering** — picking `text_heavy_body`
  because you didn't plan the visual. Plan the visual first.
- **Adjacent-same archetype** — two identical archetypes back-to-back unless
  there's a narrative reason. Run `get_deck_outline` to audit.
- **Hardcoded fill hex in bundled code** — if you're writing server-side
  code, this is a review-fail. Content-driven only.

---

## v0.6.0 addendum — icons are vanilla too

The "shapes-first, image-placeholder-as-fallback" rule extends to icons. Use
`list_registry(kind="icons")` → `create_icon()` (or `3col_pill_cards.icon_names`) before
reaching for `create_image([IMAGE: decorative-arrow])`. Icons are native Slides
shapes under the hood — same theme-color flow, same crisp rendering. An agent
that picks an Inter-bold-italic run + a `chart-up` icon + a teal accent bar has
built a pop slide out of vanilla primitives alone. That's the whole vision.

See `rules/icons.md` for the 30+ icon catalog and the `create_icon` workflow.
