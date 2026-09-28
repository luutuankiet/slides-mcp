# Keep self-hosting slides-mcp rather than adopt Google's Slides MCP server

While planning to share slides-mcp with a team as a remote server, we
considered Google's hosted Slides MCP server (Developer Preview, September
2026), which would remove all hosting and OAuth work. We keep building
slides-mcp's own remote mode. Google's server returns raw Slides API JSON,
about 4 to 14 times the tokens per slide of slides-mcp's reads even with a
tight field mask (about 130 times unmasked), and its write tool has no
destructive guard, dry run or post-write state. Those are the reasons this
repo exists. The measurements are in `docs/reference/google-slides-mcp.md`.

## Considered options

- **Adopt Google's server.** Rejected for the read cost, the unguarded writes,
  and the preview terms, which let Google use data sent through it to improve
  the API.
- **Run both.** Rejected for now: Google's server has no job slides-mcp does
  not already do better, so a second connector adds setup and no capability.

## Consequences

- We carry HTTP mode, OAuth and Cloud Run hosting ourselves.
- Revisit when Google's server leaves preview, if `read_presentation` gains a
  compact read or a slide-range selector.
- Google has two small things we lack, comment reads and a revision check on
  writes. Both are cheap to add.
