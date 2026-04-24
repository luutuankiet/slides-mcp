# Theme Coherence — Cross-Slide Visual DNA

**Read this BEFORE `generate-from-intent.md` / `visual-presentation.md`.** This
rule governs the one decision that distinguishes a *deck* from a *pile of
slides*: a shared visual language across every slide you create.

## The problem this solves

Phase 1 (content-driven per-slide identity) works. Each `create_slide` call
picks its own palette, gets its own accent, its own pills. But 10 slides with
10 independent palettes reads as *10 different decks*. A viewer can't tell
they belong together.

Phase 2 fixes this by persisting a **theme brief** inside the deck itself
(hidden meta-slide, Decision R). Every subsequent `create_slide` call reads
the brief and fills unspecified color fields from it. One commitment, coherent
output.

## The brief — what it carries

```yaml
version: 1
palette:
  surface: "#0F1A4A"    # header bars, backgrounds
  accent:  "#E8612E"    # titles, dividers, highlights
  text:    "#000000"    # body text
  category_set:         # 3-5 hex for N-slot archetypes (pill cards, columns)
    - "#E8612E"
    - "#0F1A4A"
    - "#5A6B9A"
shape_language: "sharp" | "rounded" | "mixed"
numbering_style: "bold" | "outlined" | "dot" | "hidden"
tone: "clean editorial"      # free-text — informs image prompts + copy register
image_prompt_style: "..."    # free-text — informs [IMAGE: …] placeholder style
```

**What's NOT in the brief and why:**
- Fonts — live in the theme YAML layer (`promote_to_theme`). Iterate on palette
  fast, iterate on typography slow.
- Archetype preferences — structural pacing stays an agent decision. Brief is
  *look*, archetype is *rhythm*.

## Resolution order (locked)

Every builder applies this priority for any color/palette field:

```
per_slide_content  >  brief.palette.*  >  theme YAML  >  safety default
```

You can still pass `pill_hex`, `title_color_hex`, `accent_color_hex`, etc.
per-call — those WIN over the brief. The brief is a **default**, not a
gatekeeper. Pass overrides when the slide genuinely needs a different accent
(a "danger" column in red, a dark-mode fullbleed cover, etc.). Omit them
everywhere else and the brief keeps the deck coherent.

## The brief tools (v0.9+ surface)

| Tool | Purpose |
|---|---|
| `get_theme_brief(deck_url)` | Read the active brief. Returns `{brief, slide_id, status}`. `status: "absent"` when the deck has no meta-slide yet. |
| `write_theme_brief(deck_url, mode="replace", brief=...)` | Create (first time) or replace the brief on the deck. Appends a hidden (`isSkipped`) slide titled `__SLIDES_MCP_THEME_BRIEF__ — DO NOT DELETE`. Returns the meta-slide `slide_id`. v0.9.0+ also populates speaker notes with rebuild instructions. |
| `write_theme_brief(deck_url, mode="merge", delta=...)` | **Forward-only** deep-merge patch. Existing slides untouched — future `create_slide` calls see the amended brief. |
| `extract_theme_brief(deck_url)` | **Brownfield.** Audit an existing deck (Joon-style deck without a brief), return a proposed brief with evidence histograms. Does NOT commit — agent reviews with user, tweaks, then calls `write_theme_brief(mode="replace", brief=...)`. |
| `write_theme_brief(deck_url, mode="scaffold", auto_commit_if_high_confidence=?)` | **v0.9.0 brownfield-first entry.** One-shot: detects existing / absent / corrupted meta, extracts a proposal, optionally auto-commits when `confidence == "high"`. Collapses the `get → extract → review → set` dance for the dominant brownfield entry mode. Prefer over the legacy 3-call path when onboarding a deck. |
| `write_theme_brief(deck_url, mode="import", yaml_source=..., is_path=?)` | Parse a YAML brief (string or file path) and commit it to the meta-slide. |

## Workflow — greenfield (new deck, user gives intent)

```
1. get_theme_brief(deck_url)
     → status: "absent"

2. Translate user intent → brief values
     - User: "Q2 QBR, clean editorial tone, navy + orange"
     - Brief: {palette: {surface: #0F1A4A, accent: #E8612E, text: #000000,
                         category_set: [#E8612E, #0F1A4A, #5A6B9A]},
               shape_language: "sharp", tone: "clean editorial", ...}

3. write_theme_brief(deck_url, mode="replace", brief=brief)
     → returns slide_id; brief now lives in the deck

4. create_slide(..., archetype=X, content={title, body, ...})
     → brief auto-resolves every unset color
     → response includes `brief_applied: true`

5. Continue creating slides — no palette repetition per call

6. If user pivots ("make the accent warmer"):
     write_theme_brief(deck_url, mode="merge", delta={palette: {accent: "#D64518"}})
     → subsequent creates use new accent; existing slides unchanged
```

## Workflow — brownfield (existing deck inherited / imported)

**v0.9.0 fast path — `write_theme_brief(mode="scaffold")`** (preferred for most decks):

```
1. write_theme_brief(deck_url, mode="scaffold",
                      auto_commit_if_high_confidence=False)
     → status: "exists" | "proposed"   (auto-skips to greenfield step 4+
                                         when "exists")

2. If "proposed":
     a. Present proposed_brief + evidence + confidence to user
     b. User tweaks OR accepts
     c. If accepted as-is + confidence was "high": re-run scaffold with
        auto_commit_if_high_confidence=True to commit in one call
     d. Else: call write_theme_brief(mode="replace", brief=...) to persist

3. From here on: same as greenfield step 4+
```

**Legacy 3-call path** (explicit per-step control; still supported):

```
1. get_theme_brief(deck_url)                                 → status: "absent"
2. extract_theme_brief(deck_url)                             → proposed_brief + evidence
3. (user review)
4. write_theme_brief(deck_url, mode="replace", brief=brief)  → commits; populates notes
5. From here on: same as greenfield step 4+
```

## When to pass per-slide overrides (the other 20%)

Use per-slide content fields (NOT brief) when the slide is visually distinct
by design:

- **Danger / warning column**: `pill_hex: "#DB4437"` on one column of a 3col
- **Hero cover with bright image**: `title_color_hex: "#FFFFFF"` so text reads
  over the fullbleed raster
- **Section opener** where you genuinely want a different accent to signal a
  topic shift
- **User asked for a one-off variation** ("make this slide red to emphasize
  risk")

If you find yourself passing the same `accent_color_hex` on every call —
STOP. That belongs in the brief. Commit it once via `write_theme_brief(mode="replace", ...)`, then
let the default flow.

## The `brief_applied` response flag

Every `create_slide` response now carries `brief_applied: bool`. If it's
false when you expected the brief to drive the palette, check:

1. Was `theme_brief=True` (the default)? You only set `False` when
   deliberately bypassing for regression testing.
2. Did `get_theme_brief` return `status: "absent"`? No meta-slide = no
   brief. Call `write_theme_brief(mode="replace", ...)` first.
3. Is the brief body corrupted? `get_theme_brief` returns
   `status: "unparseable"` in that case. Use `write_theme_brief(mode="merge", ...)` to
   repair (or delete the meta-slide and `write_theme_brief(mode="replace", ...)` fresh).

## Deletion safety — what if someone removes the meta-slide?

**Durability layers (v0.9.0):**

- **Title marker** — the meta-slide title carries a literal `DO NOT DELETE`
  warning, rendered in 20pt bold red.
- **Body preamble** — visible warning + `write_theme_brief(mode="scaffold")` rebuild command.
- **Speaker notes** — v0.9.0+ populates the Notes pane with a longer-form
  explanation + rebuild steps + the full MCP tool list. Humans who open
  the Notes pane get full context without needing external docs.
- **`isSkipped=True`** — hidden from presentation mode; devs editing the
  deck see it but it doesn't appear in the actual show.

**If it's deleted anyway:**

1. **Google Slides version history** restores it — fastest path if deletion
   was recent (mentioned in both body preamble and speaker notes).
2. **Rebuild via MCP**:
   `write_theme_brief(deck_url, mode="scaffold", auto_commit_if_high_confidence=True)`
   proposes a brief from the deck's existing palette and commits when
   confidence is high. Low-confidence decks get a proposal-only response
   for user review before committing.
3. **Graceful degradation** — `create_slide` without a brief falls back to
   theme YAML (pre-Phase-2 behavior). No hard failure: deck creation keeps
   working while you scaffold a new brief.

## Anti-patterns

1. **Carrying the brief in your conversation state instead of in the deck.**
   You'll forget after compaction / fork. The deck IS the source of truth.
2. **Extracting a brief and immediately committing without showing the user.**
   The extraction is a PROPOSAL. Discussion + tweaks are the point — the
   agent owns the legwork, the user owns the aesthetic call.
3. **Passing `pill_palette` on every `create_slide` call.** That's a brief,
   not a per-slide choice. Commit it once.
4. **Using `write_theme_brief(mode="merge")` to retroactively repaint existing slides.**
   It doesn't — it's forward-only. Retroactive repaint needs `restyle_slides`
   or `apply_brief_and_restyle` (one-call commit + repaint). If you need to repaint now, use
   `apply_brief_and_restyle(deck_url, brief=..., confirm_destructive=True)`.
