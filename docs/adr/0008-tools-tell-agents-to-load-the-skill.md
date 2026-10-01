# Every tool tells the agent to load the skill once per session

Supersedes the rejected option "a server `instructions` string or nudges from
other tools" in `0005-skill-delivery-through-mcp-tool.md`. Everything else in
0005 stands: the skill still ships in the wheel and `install_skill` still
returns it.

0005 waited for the human to ask for the skill. In practice nobody asked:
over one day of remote use, claude.ai agents called `install_skill` 0 times in
4 working sessions and a ChatGPT agent called it once, 11 minutes in, after a
failed write (issue #19). Web agents have no skill folder, so the skill only
reaches them if the agent loads it unprompted. Since v2.4.0 every tool
description except `install_skill`'s ends with one `Skill:` sentence: load the
slides-mcp skill once per session if it is not already in context, through the
harness's own skill loader when the skill is installed, otherwise through
`install_skill`. The server's `instructions` carry the same sentence, for
clients that read them.

## Considered options

- **Server `instructions` only.** Rejected as the only channel: not every
  client shows them to the model; tool descriptions always reach it.
- **The hint on write tools only.** Rejected: agents read decks first, and the
  skill also covers reading.
- **Move the skill's key advice into the tool descriptions.** Rejected for
  now: about 22 KB of skill would be paid by every client on every connect.

## Consequences

- The sentence lives once, as `SKILL_HINT` in `server.py`; `_tool()` appends it
  to each docstring, so `tools/list` grows by about 2.5 KB. A test checks every
  tool but `install_skill` carries it.
- `install_skill`'s first instruction now says that an agent loading the skill
  on its own reads the files as reference and installs nothing.
- Agents that already have the skill installed may still call `install_skill`
  once; that costs one 22 KB response and no Google call.
- To judge whether the skill helps, the server needs to log tool calls by name;
  today only writes are logged.
