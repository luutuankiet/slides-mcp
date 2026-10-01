"""Thin CLI dispatcher for slides-mcp.

Subcommands:
  auth        Run one-time OAuth consent (produces token.json)
  serve-http  Run as a remote MCP server over HTTP; `serve-http --help` lists its settings
  (none)      Start the MCP stdio server (default)

v1 had an `install` subcommand that dropped Claude Code skill docs into the
user's project. That is now the `install_skill` MCP tool.
"""
from __future__ import annotations

import sys


def _dispatch_auth(remaining_args: list[str]) -> int:
    from slides_mcp.bootstrap import main as auth_main

    return auth_main(remaining_args)


def _dispatch_http(remaining_args: list[str]) -> int:
    from slides_mcp import http_mode

    if remaining_args in (["-h"], ["--help"]):
        print(http_mode.SETTINGS_HELP, end="")
        return 0
    if remaining_args:
        print(f"slides-mcp serve-http: takes no arguments, got {remaining_args[0]!r}; "
              "settings come from environment variables (see --help)", file=sys.stderr)
        return 2
    return http_mode.serve()


def _dispatch_server() -> int:
    from slides_mcp.server import main as server_main

    server_main()  # blocks until stdio closed
    return 0


def main() -> int:
    args = sys.argv[1:]

    if args == ["-h"] or args == ["--help"]:
        print(__doc__ or "")
        return 0

    if args:
        sub, *rest = args
        if sub == "auth":
            return _dispatch_auth(rest)
        if sub == "serve-http":
            return _dispatch_http(rest)
        # Leading flags still start stdio, so MCP clients can pass opaque ones.
        # A bare word is a mistyped command: on Cloud Run a silent stdio start
        # would only show up as a failed health check.
        if not sub.startswith("-"):
            print(f"unknown command: {sub}", file=sys.stderr)
            return 2

    return _dispatch_server()


if __name__ == "__main__":
    raise SystemExit(main())
