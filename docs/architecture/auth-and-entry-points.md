---
title: Auth and entry points
covers: where token.json is read from, how OAuth refresh and scopes work, what slides-mcp and slides-mcp-auth do on startup
verified: 2026-09-28
---

# Auth and entry points

Line numbers are **a starting point, not an address**. Confirm by what the
code says, and re-date this page if you correct a range.

## Two console scripts

Declared in `pyproject.toml` under `[project.scripts]`:

| command | module | does |
|---|---|---|
| `slides-mcp` | `cli.py:main` (30–44) | starts the stdio server; `slides-mcp auth …` forwards to the consent flow |
| `slides-mcp-auth` | `bootstrap.py:main` (19–50) | one-time OAuth consent in a browser, writes `token.json` |

`cli.py` treats any unrecognised first argument as "start the server", so an
MCP client can pass opaque flags. A typo such as `slides-mcp atuh` therefore
starts a stdio server that waits silently for input. That trade-off is
commented at `cli.py:41–42`.

The server never runs a consent flow. It only reads a token minted elsewhere.

## Where the token is read from

`auth.token_path()` (`auth.py:28–32`):

```python
if env := os.environ.get("SLIDES_MCP_TOKEN_PATH"):
    return Path(env).expanduser()
return Path.cwd() / "token.json"
```

Without the environment variable, the path depends on the **working directory
the MCP client launches the server in**. `slides-mcp-auth --out` also defaults
to `./token.json` (`bootstrap.py:29`).

## Load and refresh

`load_credentials` (`auth.py:35–64`) calls
`Credentials.from_authorized_user_file(path)` **without** passing `SCOPES`. It
refreshes an expired token and writes the refreshed token back to the same
file.

Not passing `SCOPES` is deliberate. Google refuses a refresh that asks for
scopes different from those granted at consent (`invalid_scope: Bad Request`),
and v2.0.0 hit exactly that after narrowing `SCOPES`. Loading with the saved
scopes keeps tokens minted by any version working. The comment at
`auth.py:53–54` guards this.

`SCOPES` (`auth.py:22–25`) is used only by `bootstrap.py` for fresh consent.
Write tools need the `presentations` scope; a token with only
`presentations.readonly` gets a 403 that `exec_batch_update` rewrites into a
re-consent hint.

## Service object

`slides_api._slides_service()` (`slides_api.py:92–95`) builds the Google API
client once per process (`functools.cache`). A token replaced on disk is not
picked up until the server restarts.

`auth_status` returns path, existence, scopes, the last 8 characters of the
client id, whether a refresh token exists, and expiry. It never returns the
token itself.
