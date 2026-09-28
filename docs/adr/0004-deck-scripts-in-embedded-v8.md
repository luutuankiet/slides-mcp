# Run agent scripts in embedded V8, in a worker process per call

Edits that depend on deck content (swap a palette, re-theme dark slides,
restyle every title) made the agent read the deck, compute requests in its
context and paste them back: slow, token-heavy and error-prone. v2.2.0 adds
`run_deck_script`, which runs agent-written JavaScript against the deck inside
the server. The script runs in mini-racer, a maintained embedded V8 that
agents already write good code for, with no filesystem, network, imports or
timers. Each call gets its own child process,
and the parent kills it on timeout.

The threat model is **accidents, not adversaries**: an agent writing an
infinite loop, a huge allocation, or a batch that would wreck a deck. The
script only runs with the local user's own token, against a deck the user
named. So the sandbox has to be cheap and unbreakable by mistake, not proven
against a determined attacker.

## Considered options

- **pydantic-monty** (a Python-subset interpreter). Rejected: its first 1.0
  release was days old when this was decided.
- **quickjs** (Python bindings for QuickJS). Rejected: unmaintained since
  2023.
- **RestrictedPython.** Rejected: it is not a security boundary; a mistake
  runs inside the server's own interpreter.
- **mini-racer in the server process.** Rejected after probing: its eval
  timeout does not cover code that runs after an `await` (V8 microtasks), so
  a loop there hangs the server; and a V8 crash takes the server down.
- **V8's `--allow-natives-syntax`** to get more control. Rejected: it lets
  script code reach V8 internals and crash the process.

## Consequences

- About 0.1 s of process start per call, and one extra dependency
  (`mini-racer`, which ships V8 wheels for every platform, about 20 MB).
- The parent owns the timeouts: a per-wait CPU budget and an overall wall
  clock. Kill is the only stop mechanism, so there is no graceful cancel.
- All Google API calls stay in the parent. The worker only sees JSON, so no
  token or network handle ever enters the isolate.
- Writes still go through the one write path (`writes.apply_batch`) with the
  same destructive guard, which keeps the `0002` rule that every write is a
  plain `batchUpdate`.
