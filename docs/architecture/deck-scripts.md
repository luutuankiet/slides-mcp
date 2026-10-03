---
title: Deck scripts (run_deck_script)
covers: how an agent's JavaScript runs against a deck, the read model the script sees, the worker process and its timeouts, commit phases, dry run, applying a dry run by plan_id, what is refused before any API call, where errors and warnings come from, the thumbnails a real apply returns
verified: 2026-10-03
---

# Deck scripts

`run_deck_script` (`src/slides_mcp/server.py:986–1153`) runs an agent's
JavaScript against one deck. The script reads the deck, computes, and queues
Slides API requests with `emit()`; the server applies them. The deck never
passes through the agent's context, only the script's return value does.

Line numbers are a starting point, not an address.

```mermaid
sequenceDiagram
  participant T as run_deck_script
  participant R as scripting.run
  participant W as worker process (V8)
  participant G as Google Slides
  T->>R: script, input, limits
  R->>G: GET deck (DECK_FIELDS)
  R->>W: start {script, input, deck snapshot}
  W-->>R: commit {requests}
  R->>G: batchUpdate (one phase, atomic)
  R->>G: GET deck
  R->>W: resume {deck snapshot}
  W-->>R: done {result, requests}
  R->>G: batchUpdate (last phase)
  R-->>T: result, receipt, warnings, logs
```

## The pieces

| file | role |
|---|---|
| `src/slides_mcp/scripting.py` | limits, the `Worker` handle, `run()` (391–), warnings, dry-run summary, notes expansion |
| `src/slides_mcp/sandbox/worker.py` | the child process: one V8 isolate per call, JSON lines on stdin/stdout |
| `src/slides_mcp/sandbox/prelude.js` | everything the script sees besides the deck: `emit`, `commit`, helpers, selectors, `console` capture |
| `src/slides_mcp/deck_model.py` | `build()` turns the API deck into the snapshot the script reads; `id_index()` maps every object id to its slide |
| `src/slides_mcp/writes.py` | `apply_batch`, destructive kinds, `unknown_ids`, `affected_slide_ids` |

## Why a separate process

The isolate is mini-racer (embedded V8). It runs in a child process spawned
per call (`python -m slides_mcp.sandbox.worker`), and the parent owns every
timeout:

- `cpu_timeout_s` is the budget for each wait on the worker, so an infinite
  loop between commits is killed. `timeout_s` is the overall wall clock,
  including API calls and thumbnails.
- When either fires, the parent kills the process. That is the only reliable
  stop: code after an `await` runs as V8 microtasks, which mini-racer's
  per-eval timeout does not cover.
- A V8 crash or out-of-memory (limit 1 GB) ends the child, not the MCP
  server.

The trade-off, recorded in `docs/adr/0004-deck-scripts-in-embedded-v8.md`, is
about 0.1 s of process start per call.

## What the script sees

`deck_model.build` (71–108) produces plain JSON: deck `{id, title,
revisionId, pageSize, slides}`, each slide with `position`, `hidden`,
`layoutId`, resolved `background` with `luminance`, `isDark` (luminance under
0.4), the resolved `theme` colours, `notes {objectId, text, markdown}` and
`elements`. Elements carry page-space EMU geometry (`x, y, w, h`, rotation and
groups already applied) plus the raw local `transform` and `parentMatrix`,
because write requests for a group child are in the parent's space.
Everything comes from `normalize.py`; see `read-path.md` for how fills,
theme colours and UTF-16 offsets are resolved.

The prelude wraps that JSON with selectors (`deck.select`, `deck.slide`,
`slide.find`, `deck.element`, `deck.slideOf`) and helpers that return request
dicts. `styleRuns` keeps each run's weight when the font changes, since
setting `fontFamily` alone resets weight to 400. `textBox` sets `autofit` to
`NONE`, matching the shipped skill's invariant.

## Phases, dry run and refusals

Each `await commit()` ends a *phase*. A phase is one `batchUpdate`, so it
applies atomically. After it, the deck is re-read and sent back so the next
phase sees created ids and new geometry.

Before any phase is sent, `run()` refuses it (error kind in brackets) when:

- a request names an id that is neither in the deck nor created earlier in
  the same phase (`validation`, via `writes.unknown_ids`);
- the total across phases passes `max_requests` (`limit`);
- it holds destructive kinds and `confirm_destructive` is false
  (`destructive`). The one exemption is the `deleteParagraphBullets` a notes
  append emits for its own new lines.

`dry_run=true` (the default) sends nothing and returns a per-slide preview:
request kinds, colours and fonts before and after (`summarise`, 277–330). It
stops at the first `commit()`, since later phases would build on writes that
did not happen, and says so with `stopped_at_commit`.

`setNotes()` in the prelude returns a marker, not a request.
`expand_notes` (332–362) turns it into real requests with the same builder as
`write_speaker_notes`, and tracks each notes shape so two `setNotes` calls on
one slide in one phase compose correctly.

## Applying a dry run by plan_id

A successful dry run saves `{script, input, limits}` through the server state
store (`src/slides_mcp/state_store.py`) and returns the key as
`preview.plan_id`. The plan is bound to the deck id and to the caller
(`auth.caller_id()`: `None` over stdio, the Google account's `sub` claim in
HTTP mode) and expires after 1 hour. Over stdio it lives in process memory;
`serve-http` keeps it in Firestore so another Cloud Run instance can apply
it.

Calling the tool with `plan_id`, `dry_run=false` and no `script` loads the
plan and re-runs the stored script against the live deck, with the stored
limits. It never replays the dry run's requests, pins a revision or compares
with the preview: co-editors' changes since the dry run are picked up, and
the receipt thumbnails are the verification. Per-call arguments come from
the apply call, so `confirm_destructive` must be given again if the re-run
emits destructive kinds; confirming on the dry run does not carry over.

A successful apply expires the plan, since a second apply would repeat the
edits. A refused or failed apply leaves it in place for a retry. Error kind
`plan` covers: `plan_id` with `script`, `plan_id` without `dry_run=false`,
and a plan that is unknown, expired, already applied, for another deck or
made by another caller (one message for all of these, saying to run the dry
run again, so a plan id reveals nothing about other callers' plans).

## Thumbnails after a real apply

A real apply that succeeded returns thumbnails as a receipt; a dry run or an
error never does. The slides are `receipt.affected_slide_ids` across every
phase, in deck order, at most 3, with the rest listed in
`thumbnails.not_shown_slide_ids`. `render_slides` overrides that list with an
explicit selector, still capped at 6. `receipt` sets the size: `medium`
(default), `large`, or `off` for none, even when `render_slides` is given.

The tool then returns a list, the JSON body as text followed by the images,
instead of the plain dict. A failed thumbnail is a warning and never an
error. Rendering is shared with `exec_batch_update`; see the Receipts section
of `write-wedge.md` for concurrency and Google's thumbnail quota.

## Errors, warnings and logs

- Error kinds: `syntax` and `script` (with `line` and `column` in the
  script's own numbering; the wrapper adds one line, `LINE_OFFSET` in
  `worker.py`), `memory`, `timeout`, `validation`, `limit`, `destructive`,
  `api` (with `request_index` and the failing request), `auth`, `plan`.
- A failing API call leaves that phase unapplied, but earlier phases stay
  applied; the receipt says how many.
- `batch_warnings` (189–246) flags font changes that drop an existing weight
  and size or font changes that likely overflow a box with `autofit` `NONE`.
  The overflow check is an estimate from per-font average glyph widths, not a
  layout engine.
- `console.log` output comes back in `logs`. The return value is truncated
  past `max_return_bytes`.

## Tests

`tests/unit/test_deck_script.py` drives the MCP tool against
`tests/fake_api.py`, which serves a scrubbed recording of four real slides
plus hand-built edge cases (transparent box, translucent theme fill, rotated
label, weighted heading, emoji text, rich notes) and applies the request
kinds the tests send. No network. Its `run` helper turns receipts off;
`tests/unit/test_receipts.py` covers them, and
`tests/unit/test_deck_plans.py` covers plans over the in-memory store.
