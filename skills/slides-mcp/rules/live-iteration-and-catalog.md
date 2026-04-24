# Live brief iteration + portable catalog (v0.8.0)

The natural-language loop for *alter* an active brief on a live deck.

## The canonical loop

```
1. tweak_brief(deck_url, "warmer and more editorial")
   → {delta, candidate_brief, matched_axes, unresolved_terms, confidence}

2. tweak_brief(deck_url, directive, preview="slides", candidate_brief=candidate_brief)
   → writes 4 real slides into the deck (2 current + 2 candidate)
   → returns thumbnails + variants_manifest
   → HUMAN opens Google Slides, flips through, picks

3. (if approved)
   apply_brief_and_restyle(deck_url, brief=candidate_brief,
                           confirm_destructive=True)
   → commits candidate to meta-slide + repaints every existing slide

4. (cleanup)
   delete_slide on each id in preview_slide_ids_{candidate,current}
   OR lock_variant(deck_url, variant_id, variants_manifest) to prune in one call
```

## Why `tweak_brief(preview="slides")`, not `preview(kind="brief_swatch")`

`preview(kind="brief_swatch"|"brief_swatch_grid")` returns PIL ImageContent — the agent sees it in
the chat window; the HUMAN doesn't (especially in autonomous / async sessions
where the user isn't watching the chat). The vision bar is
**"us humans see through the actual slides or draft slides created."**
`tweak_brief(preview="slides")` writes real slides the user opens in Google Slides.

| Need | Use | NOT |
|------|-----|-----|
| Agent self-check on a brief | `preview(kind="brief_swatch")` (PIL) | N/A |
| Human approval gate on a brief | `tweak_brief(preview="slides")` (real slides) | PIL swatches — user can't see them async |
| Committing a brief + repainting | `apply_brief_and_restyle` (one call) | manual `write_theme_brief(mode="merge")` + `restyle_slides` |

## tweak_brief axes (v0.8.0)

Deterministic substring match. Unmatched terms bubble up in `unresolved_terms`:

- **Temperature:** `warmer`, `cooler`, `more red`, `more blue`
- **Saturation:** `more saturated`, `more vibrant`, `more muted`, `more subdued`
- **Surface value:** `darker surface`, `lighter surface`, `night mode`, `day mode`
- **Shape language:** `sharper`, `more angular` vs. `rounder`, `softer shapes`
- **Font pairing (mood-based):** `more editorial`, `more tech`, `bolder font`, `elegant fonts`
- **Numbering:** `bolder numbering`, `outlined numbering`, `dot numbering`, `hide numbers`

Anything else returns `confidence: low`. Surface the unresolved terms to the
user and ask for a rephrase using one of the supported phrases.

## Brownfield pre-req

`tweak_brief` requires an existing meta-slide. If `get_theme_brief(deck_url)`
returns `brief: None`, run this bootstrap first:

```
1. extract_theme_brief(deck_url)        # proposes from deck histogram
2. (review proposed_brief with user)
3. write_theme_brief(deck_url, mode="replace", brief=proposed)  # creates the meta-slide
4. (NOW tweak_brief works)
```

## Portable catalog

User-owned library at `$XDG_CONFIG_HOME/slides-mcp/briefs/<id>.yaml` (override
with `SLIDES_MCP_CATALOG_DIR`). Orthogonal to the meta-slide: briefs you save
here are a REFERENCE LIBRARY, not a cache of any specific deck.

```
# Save an approved brief from deck A
catalog_brief(op="save", deck_url="A", name="Client X warm editorial",
                      mood_keywords=["warm", "editorial"])

# Browse library from deck B's context
list_registry(kind="catalog_briefs", filter="warm")

# Apply to deck B (commits to meta, DOES NOT repaint)
catalog_brief(op="use", deck_url="B", brief_id="client_x_warm_editorial")
# … then apply_brief_and_restyle if B has existing slides that need repaint
```

## Export / import (out-of-band)

For sharing briefs outside the catalog (review docs, client handoffs, VCS):

```
export_brief(deck_url="A")
  → {brief, brief_yaml, source_slide_id}
# paste brief_yaml anywhere

write_theme_brief(deck_url="B", mode="import", yaml_source=brief_yaml)              # accepts raw string
write_theme_brief(deck_url="B", mode="import", yaml_source="./brief.yaml", is_path=True)
```

Accepts either a bare brief dict OR a catalog-envelope shape (unwraps the
`brief:` field transparently).

## Anti-patterns

- **Calling `preview(kind="brief_swatch_grid")` as the human approval gate** in an
  autonomous session. The human can't see PIL images if they're not in the
  chat window. Use `tweak_brief(preview="slides")` instead.
- **Running `tweak_brief` on a brownfield deck without bootstrapping the meta
  first.** Raises FileNotFoundError — check `get_theme_brief` first.
- **Committing a `candidate_brief` without the human seeing it.** Always call
  `tweak_brief(preview="slides")` (or at minimum `apply_brief_and_restyle` after the
  human approves verbally).
- **Editing the meta-slide brief directly via `write_theme_brief(mode="replace")`** when you
  also wanted to repaint. Use `apply_brief_and_restyle` — one call, one response.
