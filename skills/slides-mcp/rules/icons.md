# Icons — vanilla primitives (v0.6.0)

Icons are **first-class vanilla primitives**, not images. They render via
native Slides API shape types (`RIGHT_ARROW`, `STAR_5`, `HEART`, `LIGHTNING_BOLT`…)
composed at call time; no SVGs, no bundled bitmaps, no CDN.

## Why not SVG?

Slides API rejects inline `data:image/svg+xml` URLs (400 “URL is invalid” —
probed in v0.6.0 kickoff). A CDN-hosted SVG would work but introduces an
external dependency that contradicts the “vanilla primitives only” vision.
Shape composition keeps icons theme-color native, offline-safe, and infinitely
scalable.

## The tools

| Tool | Purpose |
|------|---------|
| `list_registry(kind="icons", filter=keyword?)` | Browse the catalog (name + category + keywords). Filter is substring match against name+category+keywords. |
| `create_icon(deck_url, slide_id, at, name, fill_hex?, outline_hex?)` | Draw an icon onto a slide. Defaults fill to `brief.palette.accent`. |

## Workflow

1. **Discover**: `list_registry(kind="icons", filter="chart")` → `chart-up`, `chart-down`
2. **Place**: `create_icon(deck_url, slide_id, at=[13, 4, 1.2, 1.2], name="chart-up")`
3. **Verify**: `render_thumbnail(slide_id)` — native MCP `ImageContent` back.

`at` is `[left_in, top_in, width_in, height_in]` in inches, same shape as
`create_shape.at`.

## Fill resolution order

`fill_hex` argument (per call) > `brief.palette.accent` (from meta-slide) >
neutral gray `#888888` (safety). Per-shape `fill_hex` overrides in the registry
(e.g. target’s middle ring) always win over the resolved default so layered
icons look right.

## Integration with archetypes

- **3col_pill_cards** (v0.6.0): optional `icon_names: [str ×3]` — one icon
  above each pill, auto-colored to match its pill. Icon REPLACES the default
  dot accent when present. Unknown names are silently skipped (partial
  delivery is better than a failed slide).
- **text_left_image_right** (v0.6.1 target): optional `icon_name` overlay.
  Not yet landed; use `create_icon` after `create_slide` as a two-step.

## Icon catalog (v0.6.0)

| Category | Icons |
|----------|-------|
| arrows | arrow-right, arrow-left, arrow-up, arrow-down |
| symbols | plus, minus, multiply, divide, equal, not-equal, star, heart, bolt, ribbon, no-entry |
| nature | sun, moon, cloud |
| faces | smiley |
| geometric | circle, square, rounded-square, triangle, right-triangle, diamond, pentagon, hexagon, octagon |
| business | chart-up, chart-down, target, bullseye, stack |

**30+ icons.** If you need something not listed, compose it from `create_shape`
(RECTANGLE/ELLIPSE/LINE) — icons are the curated shortcut, not the only path.

## When to reach for icons

- Pill cards need visual anchors → `icon_names` + matching pill colors
- A hero slide needs an accent overlay → `create_icon` after `create_slide`
- A flow diagram needs directional cues → `arrow-right` / `arrow-left`
  between boxes
- A stat slide needs a trend indicator → `chart-up` / `chart-down` beside
  the big number

## Anti-patterns

- **Using `create_image` with an `[IMAGE: …]` placeholder** when an icon
  would nail it. Placeholders should describe raster needs (photos, screenshots,
  logos), NOT generic decoration a vanilla icon handles fine.
- **Hand-composing shapes** for something already in `list_registry(kind="icons")` — the
  registry is the fast path.
- **Picking an icon without `list_registry(kind="icons", filter="keyword")` first** — unknown names
  silently skip in archetype integrations but `create_icon` raises KeyError.
- **Passing a `fill_hex` that fights the brief** — let the default resolve
  from `brief.palette.accent` so icons inherit the deck voice.
