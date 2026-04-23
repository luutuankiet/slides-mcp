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

| Tool | Purpose |
|------|---------|
| `auth_status` | Diagnostic: report token.json state without exposing secrets |
| `list_themes` / `list_archetypes` | Discover bundled + user themes / archetype YAMLs |
| `list_deck_layouts` / `list_slides_by` | Archetype inventory + structural grep over a deck |
| `get_deck_outline` | Whole-deck index, ~20 tok/slide |
| `get_slide` | One slide as compact YAML; `mode=clean/faithful`, opt-in `include_elements` |
| `search_deck` | Text substring search across slides |
| `patch_slide` | Apply DSL patch — text edits + translation writes in one call |
| `create_slide` | Create a new slide from archetype + semantic content; returns `thumbnail_url` + `slide_id`. Follow up with `render_thumbnail` for visual verification. |
| `create_shape` | Insert a new shape at `[l,t,w,h]` with optional text / fill. Shapes-first — prefer over `create_image` for decoration. |
| `create_image` | Insert a raster (via `image_url`) OR a placeholder RECTANGLE with embedded `[IMAGE: prompt]` text (via `image_prompt`). Dual mode — placeholder is first-class. |
| `duplicate_slot` | Duplicate an existing pageElement, optionally translate by delta |
| `delete_slide` | Delete a single slide. Intent-explicit; collapses the 3-call escape-hatch pattern. |
| `clone_deck` | Drive copy a deck, optionally with cmd+F-style text replacements |
| `audit_deck_colors` | Walk the whole deck, report colors / fonts not in the active theme |
| `promote_to_theme` | Add a drift value to the theme as a named role (writes to user config) |
| `get_theme_brief` | Read the deck's theme brief (hidden meta-slide) — palette + tone carried across all slides. See `rules/theme-coherence.md`. |
| `set_theme_brief` | Create / replace the deck's theme brief. Appends a hidden `isSkipped` meta-slide. |
| `update_theme_brief` | Forward-only deep-merge patch on the brief. Existing slides untouched. |
| `extract_theme_brief` | Brownfield: propose a brief from an existing deck's palette histogram. Does NOT commit. |
| `render_thumbnail` | Render a slide as PNG and return as native MCP `ImageContent` |
| `render_thumbnail_url` | Return the short-lived contentUrl only (for non-agent callers) |
| `exec_batch_update` | Raw batchUpdate passthrough — ALWAYS `dry_run=True` on first call |

## Workflow Guides

Read these before starting. Each rule doc is scoped to one axis of the workflow:

- [rules/workflow.md](rules/workflow.md) — **Start here.** Decision tree: what to use when
- [rules/theme-coherence.md](rules/theme-coherence.md) — **READ BEFORE GENERATING A DECK.** Cross-slide visual DNA via the hidden meta-slide brief. The `set_theme_brief` → `create_slide` → coherent output flow. Resolution order: per-slide > brief > theme YAML.
- [rules/visual-presentation.md](rules/visual-presentation.md) — **READ BEFORE GENERATING A DECK.** Renderer-not-brand contract; shapes-first; placeholder-as-deliverable; content-driven palette; structural variety; pacing heuristic. The difference between a presentation and a text doc.
- [rules/read-deck.md](rules/read-deck.md) — Outline, slide, search, list_slides_by; clean vs faithful; `include_elements` gating
- [rules/write-deck.md](rules/write-deck.md) — `patch_slide` semantics; text edits, translation, `_object_ids`, `clone_deck` replacements
- [rules/theme-hygiene.md](rules/theme-hygiene.md) — `audit_deck_colors` + `promote_to_theme`; the living-theme workflow
- [rules/bidi-edit.md](rules/bidi-edit.md) — The see-and-move loop: `render_thumbnail` + `include_elements` + RELATIVE transforms
- [rules/generate-from-intent.md](rules/generate-from-intent.md) — **Prompt → slides workflow:** `create_slide` primitive, archetype selection heuristic, plan→create→verify→iterate loop. Read visual-presentation.md FIRST.
- [rules/escape-hatch.md](rules/escape-hatch.md) — `exec_batch_update` safely: `dry_run`, destructive denylist, audit log
