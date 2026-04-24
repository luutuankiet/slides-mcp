# Brownfield workflow (v0.7.0, v0.9.0)

Use this loop when the user starts from an **existing deck** rather than a blank
canvas — consulting on a deck they already have, repainting to a new brand, or
unifying the voice across slides someone else built.

## v0.9.0 pre-requisite — scaffold the brief first

Before entering the repaint loop below, the deck must have a committed theme
brief. For a brand-new onboarding:

```
write_theme_brief(deck_url, mode="scaffold", auto_commit_if_high_confidence=True)
```

One-shot. Detects existing / absent / corrupted meta, extracts a proposal
from the palette + shape topology, auto-commits when confidence is high.
Replaces the legacy `get_theme_brief → extract_theme_brief → write_theme_brief(mode="replace")`
3-call dance for brownfield (the dominant entry mode). Low-confidence
proposals come back for agent/user review before committing.

Commits also populate speaker notes on the meta slide with rebuild
instructions — durability ensures the next human who finds the hidden slide
doesn't delete it. See `rules/theme-coherence.md` § Deletion safety.

Once the brief is committed, proceed with the loop below.

## v0.7.0 upgrades at a glance

- **`orient_to_deck(deck_url)` FIRST call** — composite snapshot (brief +
  coherence + histogram + dominant font + truncated outline). Cheaper than
  4 sequential reads.
- **`audit(deck_url, kind="brief_coherence")`** — single 0..1 score + drift breakdown
  + slide-level fix hints. Use this instead of walking `audit(kind="colors")` +
  `audit(kind="typography")` manually.
- **`restyle_slides(normalize_fonts=True)`** — font family repaint parity
  with the palette repaint. Now the full brief lands in one call.

## The three tools

| Tool | Purpose | Writes? |
|------|---------|---------|
| `audit(kind="colors")` | Colors/fonts not in the theme. Shows DRIFT vs. THEME. | read |
| `audit(kind="typography")` (v0.6.0) | Dominant font + outliers, size clusters, orphan bolds, color drift vs. BRIEF | read |
| `restyle_slides` (v0.6.0) | Retroactively repaint drifted text + fills per the brief. Destructive. | write |

`audit(kind="typography")` and `restyle_slides` share the **same 60 RGB-distance
threshold** — “what the audit reports” == “what restyle will rewrite”. No
surprises.

## The loop

```
1. orient_to_deck(deck_url, outline_limit=30)
     → one call returns brief + coherence + archetype histogram +
       dominant font + first-30 outline.
     → If brief absent:
         extract_theme_brief(deck_url) → preview(kind="brief_swatch", brief=proposed)
         → (user confirms) → write_theme_brief(deck_url, mode="replace", brief=brief)
     → If brief present but coherence < 0.7: go straight to step 3.
     → If brief present and coherence ≥ 0.9: deck is clean, likely no
       restyle needed; only go to step 3 for targeted edits.

2. audit(deck_url, kind="brief_coherence")   — single-call verdict with fix hints
                                        Replaces the audit(kind="colors") +
                                        audit(kind="typography") combo for coherence
                                        gating. Use the legacy audits for
                                        theme-level (not brief-level) drift.
     → slides_with_drift[0..20] carries per-slide fix_hint strings.
     → most_common_overrides[0..10] shows hexes outside the brief by
       frequency — sanity check whether drift is accidental or deliberate.

3. restyle_slides(
       deck_url,
       slide_ids="all" OR specific list,
       brief_overrides={…},      # optional in-flight palette tweak
       normalize_fonts=True,     # v0.7.0: also repaint fontFamily
       confirm_destructive=True,
   )
     → returns per_slide rewrite counts (fill_rewrites, text_rewrites,
       font_rewrites) + thumbnails.

4. preview(kind="deck_contact_sheet", deck_url=deck_url, slide_ids=[restyled ids])
     → one PNG grid confirms the one-voice result. Cheaper than calling
       render_thumbnail N times.

5. If the brief_overrides palette should persist:
     write_theme_brief(mode="merge", delta={…})    — amend the committed brief

6. If the user pushes back ("no, keep slide 5 in original colors"):
     restyle_slides(slide_ids=[…]) targeted re-run, OR
     exec_batch_update(updateTextStyle/updateShapeProperties…) surgical edits

7. Pre-ship: run audit(kind="brief_coherence") once more. Score ≥ 0.9 before
   shipping.
```

## `normalize_fonts=True` (v0.7.0)

When the brief carries `font_family.heading` and/or `font_family.body`,
setting `normalize_fonts=True` extends `restyle_slides` to emit
`updateTextStyle fontFamily` requests for every shape whose text families
don't match the brief axis:

- Shapes with max run size >= 24pt → heading family
- Shapes with max run size < 24pt → body family

It's backward-compatible (default False). Requires `brief.font_family` on
at least one axis or it's a no-op. Use this to kill Calibri-from-paste
pollution or to migrate a deck onto a Google Fonts pairing chosen via
`list_font_pairings`.



## `confirm_destructive=True` — why it's gated

`restyle_slides` overwrites per-slide hex that the agent (or the user) may have
set deliberately at creation time — e.g. `pill_hex: "#DB4437"` for a “danger”
column. The gate forces a conscious opt-in. When in doubt:

1. Run `audit(kind="typography")` first — see what _would_ change.
2. Show the user. Let them decide.
3. Pass `confirm_destructive=True` once they approve.

## Anti-patterns

- **Rewriting in a loop with `update_text(scope="run")`** when `restyle_slides`
  would handle every slide in one call. Restyle is the right tool for
  **brief-wide repaints**.
- **Asking the user for brief colors** when a deck already has a committed
  brief. Call `get_theme_brief` first — the intent is already on disk.
- **Skipping the audit** and running restyle blind. Without auditing first
  you can’t explain _what_ will change — the user will lose trust.
- **Forgetting `write_theme_brief(mode="merge")`** when the overrides should persist.
  `restyle_slides(brief_overrides=…)` only repaints; it does NOT update
  the committed brief. Follow up with `write_theme_brief(mode="merge")` if the
  overrides should stick for future `create_slide` calls.

## What `restyle_slides` will NOT touch

- Near-black text (distance < 60 from `#000000`) — body text stays legible
- Near-white text (distance < 60 from `#FFFFFF`) — inverted titles on dark
  backgrounds stay legible
- The hidden meta-slide (`theme_brief_*`) — reserved
- Any color already within 60 RGB distance of a brief palette role — treated
  as already-on-brand

Use `exec_batch_update` for edits outside this envelope (font-family changes,
paragraph alignment, text content edits).
