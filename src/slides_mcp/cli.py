"""Thin CLI dispatcher for slides-mcp (v2 — read-only).

Subcommands:
  auth      Run one-time OAuth consent (produces token.json)
  (none)    Start the MCP stdio server (default)

v1 had an `install` subcommand that dropped Claude Code skill docs into the
user's project. v2 is barebone — the skill is shipped by the published
package but the install dance is gone. Read `skills/slides-mcp/SKILL.md`
inline if you want the agent guidance.
"""
from __future__ import annotations

import sys


def _dispatch_auth(remaining_args: list[str]) -> int:
    from slides_mcp.bootstrap import main as auth_main

    return auth_main(remaining_args)


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
        # Unknown leading arg → start the server (lets MCP clients pass
        # opaque flags). Trade-off: typo silently starts stdio; users notice fast.

    return _dispatch_server()


if __name__ == "__main__":
    raise SystemExit(main())
