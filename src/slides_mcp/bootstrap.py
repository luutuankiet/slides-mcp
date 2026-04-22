"""One-time OAuth consent runner.

Run on a host with a browser:
  slides-mcp-auth --client-secret ./client_secret.json --out ./token.json

Then scp token.json to the host running the MCP server.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

from .auth import SCOPES, save_credentials


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="slides-mcp-auth",
        description="One-time OAuth consent to produce token.json for slides-mcp.",
    )
    parser.add_argument(
        "--client-secret", required=True, type=Path,
        help="Path to Google Cloud OAuth client_secret.json (Desktop app type).",
    )
    parser.add_argument(
        "--out", type=Path, default=Path("./token.json"),
        help="Where to write token.json (default: ./token.json).",
    )
    parser.add_argument(
        "--port", type=int, default=0,
        help="Loopback port for the OAuth redirect (0 = ephemeral).",
    )
    args = parser.parse_args(argv)

    if not args.client_secret.exists():
        print(f"error: {args.client_secret} not found", file=sys.stderr)
        return 2

    flow = InstalledAppFlow.from_client_secrets_file(str(args.client_secret), SCOPES)
    print("Opening browser for Google consent flow...", file=sys.stderr)
    creds = flow.run_local_server(port=args.port, open_browser=True)
    target = save_credentials(creds, path=args.out)

    print(f"Wrote credentials to {target}.", file=sys.stderr)
    print("Next: copy this file to the host running the MCP server, e.g.:", file=sys.stderr)
    print(f"  scp {target} user@host:~/slides-mcp/token.json", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
