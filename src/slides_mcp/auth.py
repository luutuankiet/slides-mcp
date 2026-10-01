"""Google OAuth credential loader.

stdio mode (the default):
  1. bootstrap_auth.py runs on a machine with a browser; produces token.json
  2. Operators scp token.json to the headless host running the MCP
  3. This module loads and refreshes token.json silently

HTTP mode (`slides-mcp serve-http`): each request carries the caller's own
Google access token, which fastmcp's sign-in proxy puts in request context.
The mode is fixed once at startup and HTTP mode never reads token.json.

We never run the consent flow from the MCP server itself.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

# v2 is read-only — `presentations.readonly` is enough for `presentations.get`,
# `presentations.pages.get`, and `presentations.pages.getThumbnail`. The v1
# `drive.file` scope (used by clone_deck) is dropped: v2 has no Drive surface.
SCOPES = [
    "https://www.googleapis.com/auth/presentations",
    "https://www.googleapis.com/auth/drive.readonly",
]

_http_mode = False


def enable_http_mode() -> None:
    """Called once by `serve-http` before the port opens; never undone."""
    global _http_mode
    _http_mode = True


def http_mode() -> bool:
    return _http_mode


class NotSignedInError(RuntimeError):
    """HTTP mode, but no caller access token in the request context."""


def _caller_access_token():
    from fastmcp.server.dependencies import get_access_token

    return get_access_token()


def caller_credentials() -> Credentials:
    """The current HTTP caller's Google credentials: access token only, no refresh.

    Refreshing the caller's Google grant is the sign-in proxy's job, not ours.
    """
    token = _caller_access_token()
    if token is None or not token.token:
        raise NotSignedInError(
            "No signed-in caller on this request. Ask the user to re-authenticate "
            "this MCP server in their MCP client, then retry."
        )
    return Credentials(token=token.token)


def token_path() -> Path:
    """Resolve where token.json lives. Env var wins; fallback to ./token.json."""
    if env := os.environ.get("SLIDES_MCP_TOKEN_PATH"):
        return Path(env).expanduser()
    return Path.cwd() / "token.json"


def load_credentials() -> Credentials:
    """Load + refresh credentials from token.json. Raises if missing or unrefreshable.

    Loads with the scopes saved in the token file rather than forcing v2's
    SCOPES list — Google's refresh endpoint rejects with `invalid_scope` if
    we ask to refresh under a scope set that differs from what was granted
    at consent. v2's SCOPES (`presentations.readonly`) is for FRESH consent
    only via `slides-mcp-auth`; existing v0.x tokens (`presentations` +
    `drive.file`) keep working untouched. v2 tools never make write calls,
    so a broader granted scope is harmless — capability ≠ usage.
    """
    path = token_path()
    if not path.exists():
        raise FileNotFoundError(
            f"token.json not found at {path}. "
            "Run `slides-mcp-auth` on a host with a browser, then copy the file here. "
            "See README 'Auth setup'."
        )
    # scopes=None → use whatever is saved in the JSON. Prevents the
    # `invalid_scope` refresh error when SCOPES tightens between versions.
    creds = Credentials.from_authorized_user_file(str(path))
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        path.write_text(creds.to_json())
    if not creds.valid:
        raise RuntimeError(
            f"Credentials at {path} are invalid and cannot be refreshed. "
            "Re-run the bootstrap flow."
        )
    return creds


def save_credentials(creds: Credentials, path: Path | None = None) -> Path:
    """Used by bootstrap_auth.py after the consent flow."""
    target = path or token_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(creds.to_json())
    return target


def credentials_info() -> dict[str, object]:
    """Diagnostic: report token state without exposing secrets."""
    if _http_mode:
        return _caller_info()
    path = token_path()
    if not path.exists():
        return {"path": str(path), "exists": False}
    raw = json.loads(path.read_text())
    return {
        "path": str(path),
        "exists": True,
        "scopes": raw.get("scopes") or [],
        "client_id_suffix": (raw.get("client_id") or "")[-8:],
        "has_refresh_token": bool(raw.get("refresh_token")),
        "token_expiry": raw.get("expiry"),
    }


def _caller_info() -> dict[str, object]:
    token = _caller_access_token()
    if token is None:
        return {"mode": "http", "signed_in": False}
    info: dict[str, object] = {"mode": "http", "signed_in": True}
    if email := (token.claims or {}).get("email"):
        info["email"] = email
    info["scopes"] = list(token.scopes or [])
    info["token_expiry"] = token.expires_at
    return info
