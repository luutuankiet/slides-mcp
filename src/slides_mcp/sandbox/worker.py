"""Sandbox worker: runs one deck script in an embedded V8 isolate.

Spawned per `run_deck_script` call as `python -m slides_mcp.sandbox.worker`
and spoken to over JSON lines on stdin/stdout. The parent owns every timeout
and kills this process when one fires. That is the only reliable stop: code
after an `await` runs in V8 microtasks outside any eval timeout, so an
in-process guard cannot interrupt it. A V8 crash also stays in here.

The isolate has no filesystem, network, module loader or timers; its only
way out is the messages below.

  parent -> worker   {"op": "start", "script", "input", "deck", "max_requests"}
                     {"op": "resume", "deck"}          after a commit
  worker -> parent   {"op": "commit", "requests", "logs"}
                     {"op": "done", "result", "requests", "logs"}
                     {"op": "error", "error": {kind, name, message, line?, column?}, "logs"}
"""
from __future__ import annotations

import json
import re
import sys
from importlib import resources
from typing import Any

MEMORY_LIMIT_BYTES = 1024 * 1024 * 1024
_LOC_RE = re.compile(r"(?:script\.js|<anonymous>):(\d+)(?::(\d+))?")
# The wrapper puts one line in front of the user's source.
LINE_OFFSET = 1


def _send(obj: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def _recv() -> dict[str, Any]:
    line = sys.stdin.readline()
    if not line:
        raise SystemExit(0)
    return json.loads(line)


def _locate(text: str) -> dict[str, int]:
    m = _LOC_RE.search(text or "")
    if not m:
        return {}
    out = {"line": max(int(m.group(1)) - LINE_OFFSET, 1)}
    if m.group(2):
        out["column"] = int(m.group(2))
    return out


def _clean_stack(stack: str) -> str:
    lines = [ln for ln in (stack or "").splitlines() if "script.js" in ln or not ln.startswith("    at")]
    return "\n".join(lines[:6])


def main() -> None:
    import py_mini_racer as pmr

    start = _recv()
    mr = pmr.MiniRacer()
    mr.set_hard_memory_limit(MEMORY_LIMIT_BYTES)
    prelude = resources.files("slides_mcp.sandbox").joinpath("prelude.js").read_text("utf-8")
    mr.eval(prelude)
    names = mr.eval("Object.keys(helpers).join(',')")
    wrapped = (
        "globalThis.__main = async function (input) { "
        f"const {{{names}}} = globalThis.helpers; "
        "return await (async () => {\n"
        f"{start['script']}\n"
        "})(); };\n//# sourceURL=script.js"
    )
    try:
        mr.eval(wrapped)
    except pmr.JSEvalException as e:
        msg = str(e)
        first = next((ln for ln in msg.splitlines() if "Error" in ln), msg.splitlines()[0] if msg else "")
        first = re.sub(r"^\S*(?:script\.js|<anonymous>):\d+:\s*", "", first)
        _send({"op": "error", "logs": [], "error": {
            "kind": "syntax", "name": "SyntaxError", "message": first.strip(), **_locate(msg)}})
        return

    mr.eval(
        f"__start({json.dumps(json.dumps(start.get('input')))}, "
        f"{json.dumps(json.dumps(start['deck']))}, {int(start['max_requests'])})"
    )
    while True:
        try:
            state = json.loads(mr.eval("__poll()"))
        except pmr.JSOOMException:
            _send({"op": "error", "logs": [], "error": {
                "kind": "memory", "name": "RangeError",
                "message": f"script exceeded the {MEMORY_LIMIT_BYTES // (1024 * 1024)} MB memory limit"}})
            return
        status = state["status"]
        logs = state.get("logs", [])
        if status == "commit":
            _send({"op": "commit", "requests": state.get("requests", []), "logs": logs})
            msg = _recv()
            if msg.get("op") != "resume":
                return
            mr.eval(f"__resume({json.dumps(json.dumps(msg['deck']))})")
            continue
        if status == "done":
            _send({"op": "done", "result": state["result"],
                   "requests": state.get("requests", []), "logs": logs})
            return
        if status == "error":
            err = state["error"]
            _send({"op": "error", "logs": logs, "error": {
                "kind": "script", "name": err.get("name", "Error"), "message": err.get("message", ""),
                "stack": _clean_stack(err.get("stack", "")), **_locate(err.get("stack", ""))}})
            return
        # Still "running" after every microtask drained: the script awaited
        # something other than commit(), which can never resolve here.
        _send({"op": "error", "logs": logs, "error": {
            "kind": "script", "name": "Error",
            "message": "script is awaiting something that never resolves; "
                       "commit() is the only awaitable in this sandbox"}})
        return


if __name__ == "__main__":
    main()
