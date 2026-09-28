# Cut the slide-authoring surface and make v2 a reader

Up to v0.11 this server tried to make an agent a full author of Google Slides
decks: archetype builders, theme briefs, restyling, deck planning, previews,
catalogues, audits — 48 tools. On real brownfield decks the agent loop never
closed reliably: autofit regressions, colour drift, archetype mismatches and
opaque API rejects cost a large share of every session. v2.0.0 deleted all of
it (about 5,000 lines and most of the tests) and kept five read tools. The
premise: agents are good at reading, quoting, summarising and searching
slides, and humans edit slides best in the Slides UI.

## Considered options

- **Keep hardening the authoring tools.** Rejected: each round of fixes
  exposed new API edge cases, and the cost per session did not go down.
- **Keep them but hide them behind a flag.** Rejected: the code would still
  need maintaining against an API it could not tame.

## Consequences

- Anyone who needs the old tools pins `uvx slides-mcp@0.11.0`; that tag keeps
  the full v0.11 surface.
- Proposals to add creative-layout tools (build a slide, apply a theme,
  restyle) are re-proposals of what this record rejected.
- Writes came back later only in a narrow form; see `0002`.
