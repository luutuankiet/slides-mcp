# Variant generation — show 3 moods, pick one, commit

Read this when the user's intent is moody but under-specified — *"I want
something confident but approachable,"* *"here's the content, what should it
feel like?"* — or when YOU as the agent are genuinely uncertain which
palette/shape-language fits. Rather than pick one mood and pray, render
2-5 side by side and let the user (or a verifier) choose.

The workflow is **five steps** as of v0.7.0 — with a pre-commit swatch gate
that cuts the write-and-delete cost of uncertainty:

```
propose_brief_variants(intent, n=3)   → 3 candidate briefs
render_brief_swatch_grid(briefs)       → **ONE PNG** shows all 3 tones (v0.7.0)
                                          human picks — no slides written
                                          yet.

if human decides at this step:
  set_theme_brief(deck_url, chosen)
  create_slide(...) * N                 → single deck, no variants needed

else (still unsure between 2-3):
  generate_variants(deck, content_list, briefs, variant_prefix)
                                        → render the same content N ways
  render_deck_contact_sheet(deck_url,
                             variant_id="v0_")  → one PNG per variant (v0.7.0)
                                                  cheaper than N render_thumbnail
  lock_variant(winner_id, manifest)      → commit winner + delete losers
```

**The swatch_grid step is the v0.7.0 upgrade.** Many decks don't need the
full `generate_variants` pass — a swatch grid is enough for the human to
pick one mood on sight. Only escalate to `generate_variants` when the human
wants to see real slides under each brief (complex layouts, content-heavy
slides where palette alone won't tell the whole story).

No new archetype, no new palette, no new builder — this tool stacks cleanly
on top of the existing create_slide + theme-brief machinery. One content
list renders three times under three briefs.

## Step 1 — `propose_brief_variants(intent, n=3)`

Pure function, no deck I/O. Returns a list of ready-to-use briefs seeded
from 6 curated mood templates:

| Mood | Accent | Keywords that bias toward it |
|------|--------|------------------------------|
| clean editorial | `#E8612E` navy + orange | `editorial`, `magazine`, `narrative`, `story` |
| confident enterprise | `#B45309` teal + copper | `enterprise`, `corporate`, `b2b`, `cio`, `qbr` |
| minimalist technical | `#2563EB` blue on white | `tech`, `data`, `analytics`, `saas`, `platform` |
| warm and human | `#7C2D12` earth tones | `warm`, `human`, `organic`, `community`, `craft` |
| bold magazine | `#F59E0B` charcoal + amber | `bold`, `striking`, `creative`, `agency`, `pitch` |
| elegant editorial | `#78350F` sand + brown | `elegant`, `luxury`, `serif`, `refined`, `heritage` |

Keyword matching is case-insensitive substring; each match is +1 to the
template's score. Top-N by score (tiebreak by template order), with a
distinctness invariant: no two returned briefs share the same
`palette.accent`.

```
prose_brief_variants(
    intent="modern SaaS data platform launch for enterprise buyers",
    n=3,
)
→ variants: [
    {palette.accent: "#2563EB", tone: "minimalist technical", ...},
    {palette.accent: "#B45309", tone: "confident enterprise", ...},
    {palette.accent: "#E8612E", tone: "clean editorial", ...},
  ]
```

Empty intent falls back to template order (editorial, enterprise, tech).

## Step 2 — `generate_variants(deck, content_list, briefs, variant_prefix)`

Renders the same content_list N times, once per brief. Slide IDs are
`{variant_prefix}{i}_{suffix}`, so everything is scannable + grouped.

```
generate_variants(
    deck_url=deck,
    content_list=[
        {"archetype": "cover_with_hero",
         "content": {"title": "Atlas Data Platform", "subtitle": "Ship analytics faster"},
         "slide_id": "cover"},
        {"archetype": "3col_pill_cards",
         "content": {"title": "Three capabilities",
                     "columns": [{"pill": "Unified", "body": "..."},
                                 {"pill": "Governed", "body": "..."},
                                 {"pill": "Fast", "body": "..."}]},
         "slide_id": "pillars"},
    ],
    briefs=variants_from_step_1,
    variant_prefix="eval_D_variants_",
)
→ {
    variants: [
      {variant_id: "eval_D_variants_0", brief: {...tech}, slide_ids: ["eval_D_variants_0_cover", "eval_D_variants_0_pillars"]},
      {variant_id: "eval_D_variants_1", brief: {...enterprise}, slide_ids: [...]},
      {variant_id: "eval_D_variants_2", brief: {...editorial}, slide_ids: [...]},
    ],
    total_slides_created: 6,
  }
```

**Internally:** for each brief, the tool calls `set_theme_brief(brief)` then
`create_slide` per item. The deck's meta-slide brief ends the loop holding
`briefs[-1]` (pre-lock state). Every slide carries `brief_applied: True`.

**Content list validation is up-front.** All briefs validate BEFORE any slide
is created, so an invalid brief fails loudly with nothing written.

**Default `variant_prefix` is `"v"`** — fine for experimentation. For Round-4
gates or intern tests, use an `eval_{X}_...` prefix to group + query later.

## Step 3 — render thumbnails to compare

The manifest gives you slide_ids; loop `render_thumbnail` to see the moods
side by side:

```
for v in manifest.variants:
    for sid in v.slide_ids:
        render_thumbnail(deck, sid, size="MEDIUM")
```

Or render ONE representative slide per variant (typically the cover or the
most visually-busy archetype) if you just want the gestalt.

## Step 4 — `lock_variant(deck, variant_id, variants_manifest)`

Picks one variant as winner. Two side effects, in order:

1. `set_theme_brief(deck, winner.brief)` — promotes winner's brief into the
   meta-slide (overwriting the loop-terminal state).
2. `delete_slide` on every slide_id in every LOSING variant.

Winner's slides stay. Losers' slides are gone. Meta-slide now holds the
winner's brief — from here on, any new `create_slide` resolves against it.

```
lock_variant(
    deck_url=deck,
    variant_id="eval_D_variants_1",
    variants_manifest=manifest_from_step_2,
)
→ {
    locked_variant_id: "eval_D_variants_1",
    locked_brief: {...enterprise brief...},
    kept_slide_ids: ["eval_D_variants_1_cover", "eval_D_variants_1_pillars"],
    deleted_slide_count: 4,
    deleted_slide_ids: ["eval_D_variants_0_cover", ...],
    warnings: [],
  }
```

Individual delete failures become warnings — the winner still commits.

## When to use vs NOT

| Use variants | Don't — just pick one |
|-------------|----------------------|
| User says "I'm not sure what vibe" | User named a specific brand palette (go direct) |
| You genuinely can't tell from intent which mood fits | Intent is explicit: "navy, sans, corporate" → just `set_theme_brief` |
| 2-5 moods worth comparing | 1 obvious winner — skip the ceremony |
| Pre-commit exploration with a taste test | Downstream iteration on an already-locked deck |
| Round-X intern eval (measuring distinctness) | Bespoke per-slide styling (use `update_text_style`) |

## Anti-patterns

| Anti-pattern | Why it's bad | Fix |
|--------------|-------------|-----|
| `generate_variants` without rendering → `lock_variant` | You're guessing blind; variant selection is USELESS without the visual | Always render thumbnails between generate and lock |
| Skipping `propose_brief_variants`; hand-rolling briefs | Agent-bias toward familiar palettes; no distinctness guarantee | Use `propose_brief_variants` to seed; tweak the returned briefs if needed |
| Committing the first variant reflexively | Defeats the purpose of the render-and-compare step | If you always pick `v0`, use `set_theme_brief` directly and save the cycle |
| Using `variant_prefix="v"` in a shared deck | Name collisions with previous `v0_*` runs | Pick a session-scoped prefix: `eval_Q2launch_`, `pitch_r3_`, etc. |
| Leaving surviving variant slides after a taste test | Clutters the deck; future agents confused | Either `lock_variant` (deletes losers) OR manually `delete_slide` the survivors |
