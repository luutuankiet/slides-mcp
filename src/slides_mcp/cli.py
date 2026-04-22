"""Thin CLI dispatcher for slides-mcp.

Subcommands:
  install   Install Claude Code skill docs into .claude/skills/slides-mcp/
  auth      Run one-time OAuth consent (produces token.json)
  (none)    Start the MCP stdio server (default)

Run `slides-mcp <subcommand> --help` for subcommand-specific help.
"""
from __future__ import annotations

import sys


def _dispatch_install(remaining_args: list[str]) -> int:
    from slides_mcp.install_skill import main as install_main

    return install_main(remaining_args)


def _dispatch_auth(remaining_args: list[str]) -> int:
    from slides_mcp.bootstrap import main as auth_main

    return auth_main(remaining_args)


def _dispatch_server() -> int:
    from slides_mcp.server import main as server_main

    server_main()  # returns None; mcp.run() blocks until stdio closed
    return 0


def main() -> int:
    args = sys.argv[1:]

    if args == ["-h"] or args == ["--help"]:
        print(__doc__ or "")
        return 0

    if args:
        sub, *rest = args
        if sub == "install":
            return _dispatch_install(rest)
        if sub == "auth":
            return _dispatch_auth(rest)
        # Unknown leading arg: fall through to the server so MCP clients that
        # pass opaque flags keep working. Accepted trade-off: `slides-mcp xyz`
        # with a typo silently starts the stdio server. Users notice fast.

    return _dispatch_server()


if __name__ == "__main__":
    raise SystemExit(main())
