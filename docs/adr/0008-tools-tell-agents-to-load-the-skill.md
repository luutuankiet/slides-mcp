# Point agents at the skill from the raw-requests tool, not from every tool

Supersedes the rejected option "a server `instructions` string or nudges from
other tools" in `0005-skill-delivery-through-mcp-tool.md`. Everything else in
0005 stands: the skill still ships in the wheel and `install_skill` still
returns it.

0005 waited for the human to ask for the skill. In practice nobody asked:
over one day of remote use, claude.ai agents called `install_skill` 0 times in
4 working sessions and a ChatGPT agent called it once, 11 minutes in, after a
failed write (issue #19). Web agents have no skill folder, so the skill only
reaches them if the agent loads it unprompted.

Most of the skill does not need loading, though. The `run_deck_script`,
`write_speaker_notes` and `add_section_footers` descriptions already carry
what an agent needs, and read-only work needs none of it (the skill says so
itself). Only raw `exec_batch_update` requests need more: where ids come from,
EMU sizes, the `autofit: NONE` rule, and that deck-dependent edits belong in
`run_deck_script`. So those facts moved into the `exec_batch_update`
description, which also points at the skill for worked examples. The server
`instructions` became a one-line routing map with the same pointer, because
an agent using tool search may load only one tool, and some clients show
instructions before any tool is loaded.

## Considered options

- **A "load the skill once per session" line on every tool.** Built, then
  dropped before release: it costs about 22 KB per session on reads, notes
  and scripts that gain nothing from it.
- **Server `instructions` only.** Rejected as the only channel: not every
  client shows them to the model.
- **Move the whole skill into descriptions.** Rejected: every client would pay
  for the worked examples on every connect.

## Consequences

- The `exec_batch_update` description is the copy of the raw-request rules
  agents are sure to see; the skill repeats them with examples. A test checks
  the description keeps them.
- Each tool call now logs one `slides-mcp call` line (tool name, duration,
  outcome) to stderr, so whether agents load the skill, and whether it helps,
  can be measured instead of inferred from response sizes.
