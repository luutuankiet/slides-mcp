# Bring writes back as a raw passthrough for legwork, with post-state in the reply

The read-only v2.0.0 over-corrected for one kind of work: repetitive bulk
edits where about a centimetre of layout slop is fine (section footers,
timestamps, global font swaps, find-and-replace). v2.1.0 added exactly two
write tools. `exec_batch_update` forwards Slides API Request dicts unchanged,
guarded by a dry run and an explicit `confirm_destructive` for delete and
replace kinds. `add_section_footers` is the only convenience tool, and it
delegates to the passthrough. Every successful write returns the re-read deck
outline plus the touched slides in the same response, so the agent can verify
its change without a second call.

## Considered options

- **Typed convenience tools per edit** (as in v0.x). Rejected: that was the
  surface `0001` removed. New convenience tools are added only after a real
  task keeps needing the same batch.
- **Return only Google's raw `batchUpdate` reply.** Rejected: that reply
  carries created ids, not content, so the agent must re-read the deck anyway.

## Consequences

- Each write costs one extra deck `GET` unless `post_state="none"`.
- Pixel-accurate placement is explicitly out of scope; fine layout stays a
  human job in the Slides UI.
- Agents need to know the Slides Request schema; `skills/slides-mcp/SKILL.md`
  ships that knowledge.
