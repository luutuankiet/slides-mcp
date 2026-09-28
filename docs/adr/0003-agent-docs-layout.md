# Keep repository knowledge in docs/, loaded on demand

Knowledge about why this repository looks the way it does was spread across
fifteen release-note files, the README and code comments, with nothing an
agent or contributor loads by default. It now lives in a small always-loaded
`AGENTS.md` (imported by `CLAUDE.md`), topic pages under `docs/` indexed by a
generated `docs/README.md`, and two index skills under `.claude/skills/`.

What was promoted: an architecture map for the four areas (read path, write
wedge, auth, packaging), the release checklist, and the two pivots recorded
in `0001` and `0002`. What was left behind: per-release narrative, tool
counts, token-cost estimates and version-specific migration notes, which stay
in `releases/` where they are dated by construction. Contradictions found
between the code and the docs were filed as issues rather than written up as
pages.

## Considered options

- **Grow the README.** Rejected: it is the user manual, and a contributor map
  there costs every reader and every agent session whether or not they need
  it.
