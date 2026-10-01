# The caller's Google credentials come from request context, not a parameter

In HTTP mode every tool call must reach Google as the person who made it.
We read the caller's token in one place, `slides_api._slides_service()`,
from the context variable fastmcp already sets for each request, instead of
passing credentials down through every tool and helper. Explicit parameters
would have changed about 20 signatures, including tool code agents read, to
carry what the framework already carries. Worker threads started with
`anyio.to_thread.run_sync` (deck scripts, thumbnails) copy the context, so
every phase of a script sees the same caller.

## Considered options

- **Pass credentials explicitly through every tool and helper.** Rejected for
  the signature churn.
- **Inject a service factory into each tool.** Same threading cost, plus an
  extra object.
- **Infer the mode per request** ("no token in context, use `token.json`").
  Rejected: an HTTP request without a token could silently act as whoever
  owns a stray `token.json`. The mode is fixed once at startup instead.
- **Cache Slides clients by token in HTTP mode.** Rejected: a wrong key would
  hand one caller's client to another, to save a local object build.

## Consequences

- No function's signature says it needs credentials; `docs/architecture/auth-and-entry-points.md`
  is where a reader finds out.
- Code that reaches Google outside the request's context (a new thread pool,
  a background task started with a fresh context) would raise
  `NotSignedInError` in HTTP mode. `tests/unit/test_http_mode.py` covers the
  `anyio` worker-thread path.
