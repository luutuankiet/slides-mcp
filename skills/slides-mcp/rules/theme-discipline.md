# Theme discipline (v0.7.0) — greenfield → brownfield → ship

This rule is prescriptive. Follow it by default; deviate only with explicit
reason. Theme discipline is what separates "the brief is decorative" from
"the brief is load-bearing."

## The pre-flight check — ALWAYS first in any session

On entry to a deck you haven't seen this session:

```python
orient = orient_to_deck(deck_url, outline_limit=30)
```

One call returns brief + coherence + archetype histogram + dominant font +
first-30 outline. Scales constant with deck size (outline capped); use
`outline_limit=0` for summary-only on huge decks.

Branch on what comes back:

```
orient["brief"] is None      →  NO BRIEF: go to § Brownfield entry ritual
orient["coherence_score"] < 0.7  →  DRIFTY DECK: go to § Pre-ship cleanup
orient["coherence_score"] < 0.9  →  MINOR DRIFT: decide case-by-case
orient["coherence_score"] ≥ 0.9  →  COHERENT: proceed with create_slide
```

## Greenfield workflow — commit brief FIRST

The #1 sin in a fresh deck is passing per-call hexes to `create_slide`. The
brief is the right vector; use it.

```python
# 1. Propose + preview
briefs = propose_brief_variants(intent, n=3)
render_brief_swatch_grid(briefs)          # human picks

# 2. Commit FIRST — before any content slide
set_theme_brief(deck_url, briefs[chosen])

# 3. Every create_slide inherits — zero per-call palette args
for content in contents:
    response = create_slide(deck_url, archetype, content)
    # Verify lineage: brief_fields_used tells you what was resolved
    assert "palette.accent" in response["brief_fields_used"]
```

**What you do NOT do:** pass `title_accent_hex`, `body_text_color_hex`,
`pill_palette`, etc. on greenfield unless the content *explicitly* demands
it (a "danger" column on one slide, a pull-quote with editorial color, etc.).
If you do, note WHY in the presenter notes on that slide.

## Brownfield entry ritual — never write before you read

When `orient_to_deck` reports `brief is None`:

```python
# 1. Propose a brief from the existing palette
proposed = extract_theme_brief(deck_url)

# 2. Show the human a swatch BEFORE committing
render_brief_swatch(proposed["proposed_brief"])

# 3. Human confirms or tweaks. Only then commit.
if user_approves:
    set_theme_brief(deck_url, proposed["proposed_brief"])
else:
    # Iterate on the brief before committing
    ...
```

The key discipline: `extract_theme_brief` is a PROPOSAL tool, not a commit
tool. Never commit blind.

## Pre-ship cleanup — close the coherence loop

After a generation pass (or when entering a drifted brownfield deck), run the
coherence check and act on the hint:

```python
report = audit_brief_coherence(deck_url)

if report["coherence_score"] < 0.7:
    # Moderate-to-major drift → repaint across the board
    restyle_slides(
        deck_url,
        slide_ids="all",
        normalize_fonts=True,
        confirm_destructive=True,
    )
elif report["coherence_score"] < 0.9:
    # Minor drift → surgical
    drift_ids = [s["slide_id"] for s in report["slides_with_drift"]]
    restyle_slides(deck_url, slide_ids=drift_ids, confirm_destructive=True)
```

**Before you ship the deck:** one more `audit_brief_coherence` to confirm
score ≥ 0.9. That's the gate.

## The override convention — when per-call hexes ARE justified

Per-call palette overrides are allowed for:

| Case | Example override | Notes |
|------|------------------|-------|
| Danger / error column | `pill_hex: "#DC2626"` | Column calls out an anti-pattern or risk |
| Hero / pull-quote spotlight | `title_color_hex: "<alt>"` | One spotlight slide per section, no more |
| Inverted cover over dark hero | `title_color_hex: "#FFFFFF"` | Contrast override for legibility |
| Brand element cameo | `pill_palette: [brand_hex, ...]` | Co-branding where the brief isn't the brand |

For EACH override, write a ONE-LINE note in the presenter notes explaining
why. This makes the deviation reviewable and reversible.

**NOT allowed** (these are smells, not overrides):

- "I forgot to read the brief." → run `get_theme_brief` before `create_slide`.
- "I want to try another accent." → set the alternative via
  `update_theme_brief` so every slide follows, or `restyle_slides(brief_overrides=…)`.
- "The brief's color clashes with this content." → that's a brief bug, not
  a per-call fix. Escalate to the user.

## brief_fields_used — observability for overrides

Every `create_slide` response now carries `brief_fields_used: list[str]`. If
you expect `palette.accent` to be in there and it isn't, the brief DIDN'T
fill it — either the brief has no accent, or your content has an override.
Catch these in agent-side assertions during autonomous loops:

```python
resp = create_slide(deck_url, "3col_pill_cards", content)
assert "palette.category_set" in resp["brief_fields_used"], \
    f"expected brief to drive pill colors; got overrides: {content}"
```

## Anti-patterns

- **Skipping `orient_to_deck` on a brownfield deck.** You'll commit slides
  that clash with the existing voice.
- **Committing a brief before showing it to the user.** `set_theme_brief` is
  load-bearing — every future `create_slide` inherits. A bad brief is
  expensive to undo.
- **Running `restyle_slides` without `audit_brief_coherence` first.** You
  don't know what's actually drifted; blind restyle may "correct" legitimate
  deliberate overrides.
- **Committing a deck without a pre-ship coherence pass.** The score is the
  last gate; use it.

## See also

- `rules/preview-workflow.md` — the "approve before commit" PIL primitives
- `rules/theme-coherence.md` — meta-slide mechanics
- `rules/brownfield-workflow.md` — restyle_slides + audit_typography loop
