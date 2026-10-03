---
title: Auth and entry points
covers: where token.json is read from, how OAuth refresh and scopes work, what slides-mcp and slides-mcp-auth do on startup, how HTTP mode (serve-http) picks up each caller's Google token
verified: 2026-10-03
---

# Auth and entry points

Line numbers are **a starting point, not an address**. Confirm by what the
code says, and re-date this page if you correct a range.

## Two console scripts

Declared in `pyproject.toml` under `[project.scripts]`:

| command | module | does |
|---|---|---|
| `slides-mcp` | `cli.py:main` (42–60) | starts the stdio server; `slides-mcp auth …` forwards to the consent flow; `slides-mcp serve-http` starts HTTP mode |
| `slides-mcp-auth` | `bootstrap.py:main` (19–50) | one-time OAuth consent in a browser, writes `token.json` |

`cli.py` treats an unrecognised first argument that starts with `-` as "start
the stdio server", so an MCP client can pass opaque flags. An unrecognised
bare word (`slides-mcp atuh`, `slides-mcp serve-htp`) prints
`unknown command: <word>` and exits `2`: on Cloud Run a silent stdio start
would only show up as a failed health check.

The server never runs a consent flow. It only reads a token minted elsewhere.

## Where the token is read from

`auth.token_path()` (`auth.py:68–72`):

```python
if env := os.environ.get("SLIDES_MCP_TOKEN_PATH"):
    return Path(env).expanduser()
return Path.cwd() / "token.json"
```

Without the environment variable, the path depends on the **working directory
the MCP client launches the server in**. `slides-mcp-auth --out` also defaults
to `./token.json` (`bootstrap.py:29`).

## Load and refresh

`load_credentials` (`auth.py:75–104`) calls
`Credentials.from_authorized_user_file(path)` **without** passing `SCOPES`. It
refreshes an expired token and writes the refreshed token back to the same
file.

Not passing `SCOPES` is deliberate. Google refuses a refresh that asks for
scopes different from those granted at consent (`invalid_scope: Bad Request`),
and v2.0.0 hit exactly that after narrowing `SCOPES`. Loading with the saved
scopes keeps tokens minted by any version working. The comment at
`auth.py:93–94` guards this.

`SCOPES` (`auth.py:26–29`) is used only by `bootstrap.py` for fresh consent.
Write tools need the `presentations` scope; a token with only
`presentations.readonly` gets a 403 that `exec_batch_update` rewrites into a
re-consent hint.

## Service object

`slides_api._slides_service()` (`slides_api.py:96–126`) is the one place
credentials enter. In stdio mode it loads token.json once per process
(`_stdio_credentials`, `functools.cache`), so a token replaced on disk is not
picked up until the server restarts. The client built from it is kept per
thread (`_stdio_service`), because its HTTP transport is not thread-safe and
write receipts fetch thumbnails from several threads at once.

`auth_status` returns path, existence, scopes, the last 8 characters of the
client id, whether a refresh token exists, and expiry. It never returns the
token itself.

## HTTP mode

`slides-mcp serve-http` (`http_mode.py:serve`, 169–) checks its four
`SLIDES_MCP_` settings, calls `auth.enable_http_mode()` once, attaches
fastmcp's `GoogleProvider` and runs stateless streamable HTTP. The mode is
never guessed per request.

In HTTP mode `_slides_service()` builds a new client **per call** from
`auth.caller_credentials()` (`auth.py:54–65`): the caller's Google access token
from fastmcp's request context, with no refresh token. Nothing is cached, and
`token.json` is never read; a call with no caller raises `NotSignedInError`.
Refreshing the caller's Google grant is the sign-in proxy's job. Worker threads
(`anyio.to_thread.run_sync`, used by `run_deck_script` and thumbnails) inherit
the request context, so every phase runs as the same caller. Receipt
thumbnails run in a plain thread pool, so `receipts.render` copies the context
into each worker itself. Why this is a
context variable rather than a parameter: `docs/adr/0007-caller-credentials-from-request-context.md`.

Other HTTP-mode differences:

| what | stdio | HTTP |
|---|---|---|
| write-scope pre-check (`writes.write_scope_error`) | reads token.json scopes | always `None` |
| `403` on a write | "re-run `slides-mcp-auth`" | "check edit access, then sign in again" |
| `401` from Google | plain error | tells the agent to have the user re-authenticate this server |
| `auth_status` | token.json state | `mode: "http"`, caller email, scopes, expiry |

The sign-in proxy calls Google's `tokeninfo` and `userinfo` on every request.
`http_mode.ValidationCache` keeps a successful result until that access token
expires; it is installed on the proxy's private `_token_validator`, which is
why `fastmcp` is pinned below 5 and `test_provider_installs_cache_on_proxy_verifier`
exists.
