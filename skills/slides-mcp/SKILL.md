---
name: slides-mcp
description: REQUIRED before using any slides-mcp tools. Covers reading decks as compact YAML, text edits, translation writes, theme hygiene, thumbnail rendering, and the exec_batch_update escape hatch. Read rules/*.md for the workflow slices.
metadata:
  tags: slides, google-slides, mcp, presentation, deck, theme, bidi-edit
---

## When to use

ALWAYS read this skill BEFORE calling any slides-mcp tool. Use when:

- Reading a deck outline or individual slides as compact YAML DSL
- Editing slide text, moving shapes, or cloning decks for new clients
- Auditing color / font drift against a brand theme and promoting drift to the theme
- Rendering slide thumbnails for visual verification (bidi loop)
- Running arbitrary Slides API requests via the `exec_batch_update` escape hatch

**CRITICAL:** The DSL is ~100–150 tok/slide in `clean` mode. Use `faithful` mode only when you need raw geometry. Set `include_elements=True` on `get_slide` only when you intend to MOVE shapes next — the geometry channel costs ~50 extra tokens per slide.

## Tool Priority — Bespoke First, Escape Hatch Last

The bespoke tools (`patch_slide`, `create_shape`, `duplicate_slot`, `clone_deck`) are always preferred over `exec_batch_update`. They are type-safe, theme-aware, and emit minimal requests. `exec_batch_update` is the escape hatch for rare operations outside the happy path — NOT the default.

| Need | Use | NOT (escape hatch) | Why bespoke wins |
|------|-----|-------------------|------------------|
| Text edit a slot | `patch_slide` with DSL change | hand-rolled `replaceAllText` | Uses `_object_ids` to avoid duplicate-hit |
| Move an existing shape | `patch_slide` with `elements[].at` diff | `updatePageElementTransform` | Emits RELATIVE mode — preserves scale/rotation |
| **Create a new slide from intent** | **`create_slide(archetype, content)`** | hand-composed `createSlide + createShape × N + insertText × N + updateTextStyle × N` | One semantic call; archetype + theme resolved internally; returns `thumbnail_url` + `slide_id` |
| Add a new shape | `create_shape` | `createShape + updateShapeProperties + insertText` | One tool, theme-aware fills |
| Duplicate an existing shape | `duplicate_slot` | `duplicateObject + updatePageElementTransform` | Handles objectIds map + optional translation |
| Clone a deck (template) | `clone_deck` (with optional `replacements` map) | Drive copy + manual replaceAllText per pair | Batched cmd+F replacement in one call |
| Notes edit | `patch_slide` (notes field in DSL) | `deleteText + insertText` on notesPage | Threaded through `notes_object_id` |
| Any other Slides API Request | `exec_batch_update` (with `dry_run=True` first) | N/A | No bespoke wrapper — dry-run before firing |

**Rule:** If a bespoke tool exists for your task, use it. Only reach for `exec_batch_update` when none of the bespoke tools cover your operation (e.g. `updateTextStyle`, `insertTableRows`, `updateTableCellProperties`).

## Tools Overview

**v0.9+ surface collapse:** families of single-purpose tools are now dispatchers with a `kind`/`mode`/`op`/`scope` argument. The underlying Python functions still exist (callable from tests + internal helpers), but the MCP catalog shows the dispatcher. Migration map:

| Old tools | New call |
|-----------|----------|
| `list_themes` / `list_archetypes` / `list_icons` / `list_font_pairings` / `list_catalog_briefs` / `list_deck_layouts` | `list_registry(kind="themes|archetypes|icons|font_pairings|catalog_briefs|deck_layouts", filter=?, deck_url=?)` |
| `set_theme_brief` / `update_theme_brief` / `scaffold_meta_brief` / `import_brief` | `write_theme_brief(deck_url, mode="replace|merge|scaffold|import", brief=?, delta=?, yaml_source=?, is_path=?, auto_commit_if_high_confidence=?)` |
| `audit_deck_colors` / `audit_typography` / `audit_brief_coherence` | `audit(deck_url, kind="colors|typography|brief_coherence", slide_ids=?)` |
| `update_text_style` / `update_paragraph_style` | `update_text(deck_url, slide_id, object_id, scope="run|paragraph", style, range=?)` |
| `render_brief_swatch` / `render_brief_swatch_grid` / `render_deck_contact_sheet` / `preview_archetype` | `preview(kind="brief_swatch|brief_swatch_grid|deck_contact_sheet|archetype", ...)` |
| `render_thumbnail` / `render_thumbnail_url` | `render_thumbnail(deck_url, slide_id, size, mode="bytes|url")` |
| `tweak_brief` / `preview_brief_tweak` | `tweak_brief(deck_url, directive, preview="none|slides", ...)` |
| `save_brief_to_catalog` / `use_catalog_brief` | `catalog_brief(op="save|use", deck_url=?, brief_id=?, name=?, mood_keywords=?, brief=?, overwrite=?)` |

| Tool | Purpose |
|------|---------|
| `auth_status` | Diagnostic: report token.json state without exposing secrets |
| `list_registry` | Browse themes / archetypes / icons / font_pairings / catalog_briefs / deck_layouts. Dispatches by `kind`; optional `filter` (icons/fonts/catalog) or required `deck_url` (deck_layouts). |
| `list_slides_by` | Structural grep over one deck: archetype + contains_text filters (AND semantics). Kept separate from `list_registry` — its multi-filter schema doesn't dispatch. |
| `get_deck_outline` | Whole-deck index, ~20 tok/slide |
| `get_slide` | One slide as compact YAML; `mode=clean/faithful`, opt-in `include_elements`, opt-in `include_styles` (see `rules/character-styling.md`) |
| `search_deck` | Text substring search across slides |
| `patch_slide` | Apply DSL patch — text edits + translation writes in one call |
| `create_slide` | Create a new slide from archetype + semantic content; returns `thumbnail_url` + `slide_id`. Follow up with `render_thumbnail` for visual verification. |
| `create_shape` | Insert a new shape at `[l,t,w,h]` with optional text / fill. Shapes-first — prefer over `create_image` for decoration. |
| `create_image` | Insert a raster (via `image_url`) OR a placeholder RECTANGLE with embedded `[IMAGE: prompt]` text (via `image_prompt`). Dual mode — placeholder is first-class. |
| `create_icon` | **v0.6.0.** Draw a vanilla icon on a slide by composing Slides API shape primitives. Theme-color native. See `rules/icons.md`. |
| `duplicate_slot` | Duplicate an existing pageElement, optionally translate by delta |
| `delete_slide` | Delete a single slide. Intent-explicit; collapses the 3-call escape-hatch pattern. |
| `clone_deck` | Drive copy a deck, optionally with cmd+F-style text replacements |
| `audit` | Deck-level audits: `kind="colors"` (drift vs theme), `"typography"` (dominant font + outliers + size clusters + orphan bolds), `"brief_coherence"` (composite 0..1 score + fix hints; `slide_ids=` to scope). See `rules/brownfield-workflow.md` / `rules/theme-discipline.md`. |
| `restyle_slides` | **v0.6.0.** Retroactively repaint drifted colors + fills per the deck brief. Destructive (`confirm_destructive=True`). See `rules/brownfield-workflow.md`. |
| `promote_to_theme` | Add a drift value to the theme as a named role (writes to user config) |
| `get_theme_brief` | Read the deck's theme brief (hidden meta-slide) — palette + tone carried across all slides. See `rules/theme-coherence.md`. |
| `write_theme_brief` | Commit or amend the deck's brief: `mode="replace"` (requires `brief`), `"merge"` (requires `delta`), `"scaffold"` (brownfield detect-and-propose; optional auto-commit on high confidence), `"import"` (from YAML string/path). See `rules/theme-coherence.md`. |
| `extract_theme_brief` | Brownfield: propose a brief from an existing deck's palette histogram. Does NOT commit. Pair with `write_theme_brief(mode="replace", brief=...)` to persist. |
| `render_thumbnail` | Render a slide thumbnail: `mode="bytes"` (default; MCP ImageContent) or `mode="url"` (short-lived contentUrl). |
| `update_text` | Text styling via `scope="run"` (char-level: bold/italic/color/size/font) or `scope="paragraph"` (alignment/indent/line spacing). Shared range language: `all` / `{paragraph}` / `{chars}` / `{match}`. See `rules/character-styling.md`. |
| `propose_brief_variants` | Pure: N distinct-mood theme briefs from natural-language intent. Seeds variant selection. See `rules/variant-generation.md`. |
| `generate_variants` | Render the same content_list under N briefs side-by-side. See `rules/variant-generation.md`. |
| `lock_variant` | Commit one variant's brief + delete losers' slides. See `rules/variant-generation.md`. |
| `preview` | Zero-write preview primitives. `kind="brief_swatch"` (one PIL tone card; requires `brief`), `"brief_swatch_grid"` (N side-by-side PNG; requires `briefs`), `"deck_contact_sheet"` (real thumbnails grid; requires `deck_url`; optional `slide_ids` / `variant_id`), `"archetype"` (PIL archetype sketch; requires `archetype` + `content`). All return MCP ImageContent. See `rules/preview-workflow.md`. |
| `orient_to_deck` | **v0.7.0.** Composite onboarding: brief + coherence + archetype histogram + dominant font + outline in one call. First move on any brownfield deck. See `rules/theme-discipline.md`. |
| `tweak_brief` | Natural-language directive → brief-delta + validated candidate. `preview="none"` (default; pure compute) or `preview="slides"` (writes sample slides into the deck for human-eye approval, meta restored at end). Requires an existing meta-slide. See `rules/live-iteration-and-catalog.md`. |
| `apply_brief_and_restyle` | **v0.8.0.** One-call commit + repaint. Pass `brief=` (full replacement) OR `delta=` (merged into current). Forwards to set_theme_brief + restyle_slides with `normalize_fonts=True` default. `confirm_destructive=True` required. |
| `catalog_brief` | Personal brief library ops: `op="save"` (requires `name`; optional `mood_keywords`, `brief_id`, `brief`, `overwrite`) or `op="use"` (requires `deck_url` + `brief_id`). Browse via `list_registry(kind="catalog_briefs", filter=mood)`. |
| `export_brief` | **v0.8.0.** Return the deck's brief as a portable YAML string + dict. Pairs with `write_theme_brief(mode="import", ...)` for deck-to-deck transfer. |
| `plan_deck` | **v0.11.0.** Propose a deck-level narrative plan (vision / arc / sections / slides / worklog). `source="free_text"` (default; intent → vision), `"brownfield_deck"` (read outline; section_opener transitions split sections), `"doc"` (requires `doc_path`; H1 → vision, H2 → sections/slides). `commit=True` merges the plan onto the meta-slide via `write_theme_brief(mode="merge", delta={"plan": ...})`. |
| `theme_swap` | **v0.11.0.** Clone source deck, apply target brief, swap brand assets. `target_brief=` (wholesale) or `target_brief_delta=` (merge) trigger `apply_brief_and_restyle` on the clone. `asset_overrides={asset_id: new_value}` emits `replaceAllText` (type=text) or `replaceImage` (type=image) scoped to the new deck. `confirm_destructive=True` required. Returns `{new_deck_url, new_deck_id, assets_swapped, restyle_applied, warnings}`. |
| `exec_batch_update` | Raw batchUpdate passthrough — ALWAYS `dry_run=True` on first call. Now narrower: character styling moved to bespoke tools. |

## Workflow Guides

Read these before starting. Each rule doc is scoped to one axis of the workflow:

- [rules/workflow.md](rules/workflow.md) — **Start here.** Decision tree: what to use when
- [rules/theme-coherence.md](rules/theme-coherence.md) — **READ BEFORE GENERATING A DECK.** Cross-slide visual DNA via the hidden meta-slide brief. The `set_theme_brief` → `create_slide` → coherent output flow. Resolution order: per-slide > brief > theme YAML.
- [rules/visual-presentation.md](rules/visual-presentation.md) — **READ BEFORE GENERATING A DECK.** Renderer-not-brand contract; shapes-first; placeholder-as-deliverable; content-driven palette; structural variety; pacing heuristic. The difference between a presentation and a text doc.
- [rules/read-deck.md](rules/read-deck.md) — Outline, slide, search, list_slides_by; clean vs faithful; `include_elements` gating
- [rules/write-deck.md](rules/write-deck.md) — `patch_slide` semantics; text edits, translation, `_object_ids`, `clone_deck` replacements
- [rules/theme-hygiene.md](rules/theme-hygiene.md) — `audit(kind="colors")` + `promote_to_theme`; the living-theme workflow
- [rules/bidi-edit.md](rules/bidi-edit.md) — The see-and-move loop: `render_thumbnail` + `include_elements` + RELATIVE transforms
- [rules/generate-from-intent.md](rules/generate-from-intent.md) — **Prompt → slides workflow:** `create_slide` primitive, archetype selection heuristic, plan→create→verify→iterate loop. Read visual-presentation.md FIRST.
- [rules/character-styling.md](rules/character-styling.md) — **Typographic depth (v0.5.0):** `update_text(scope="run"|"paragraph")` + range language + `get_slide(include_styles=True)` discovery.
- [rules/variant-generation.md](rules/variant-generation.md) — **Variant selection (v0.5.0):** propose → generate → render → lock workflow. For moody-but-underspecified intent.
- [rules/brownfield-workflow.md](rules/brownfield-workflow.md) — **Brownfield repaint (v0.6.0):** `audit(kind="colors"|"typography")` → `restyle_slides` loop. Retroactively unify the voice of an existing deck.
- [rules/icons.md](rules/icons.md) — **Vanilla icons (v0.6.0):** `list_registry(kind="icons")` + `create_icon` — 30+ shape-composed icons, theme-color native. Use in pill cards, heroes, flow diagrams.
- [rules/preview-workflow.md](rules/preview-workflow.md) — **Approve before you commit (v0.7.0):** `preview(kind="brief_swatch"|"brief_swatch_grid"|"archetype"|"deck_contact_sheet")`. Zero-write preview primitives.
- [rules/theme-discipline.md](rules/theme-discipline.md) — **Theme discipline (v0.7.0):** prescriptive greenfield → brownfield → ship workflow. `orient_to_deck` + `audit(kind="brief_coherence")` + when per-call hex overrides are justified.
- [rules/live-iteration-and-catalog.md](rules/live-iteration-and-catalog.md) — **Live iteration + catalog (v0.8.0):** `tweak_brief(preview="none"|"slides")` → `apply_brief_and_restyle` loop + portable brief library (`catalog_brief`) + `export_brief` / `write_theme_brief(mode="import")`.
- [rules/escape-hatch.md](rules/escape-hatch.md) — `exec_batch_update` safely: `dry_run`, destructive denylist, audit log. Narrower in v0.5.0 — character styling moved to `update_text`.
