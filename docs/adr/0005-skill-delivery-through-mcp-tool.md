# Deliver the agent skill through an MCP tool

The server is being shared with a team whose members only get an MCP endpoint
URL behind OAuth. They never clone the repo or run a CLI, so they had no way
to get the `slides-mcp` skill, and their agents could reach the write tools
without knowing how to compose requests well. The published wheel did not
even contain the skill. v2.3.0 adds `install_skill`, a tool that returns every
skill file with its path, the version, and steps the agent follows to install
it where the user chooses.

An `install` CLI existed before v2.0.0 and was removed with the read-only
refactor (`0001`). It came back as a tool, not a CLI, because a CLI only runs
on the machine that has the package; a remote teammate has only the endpoint.

## Considered options

- **Return only a link to the skill on GitHub.** Rejected: the agent needs web
  access, a push to the repo would silently change what gets installed, and
  the skill could drift from the server version the user is connected to.
- **A server `instructions` string or nudges from other tools** telling the
  agent to install the skill. Rejected: the human asks for it.

## Consequences

- The skill is part of the wheel (hatch `force-include`) and is versioned with
  the server; a wheel test guards the bundling.
- The response grows with the skill. If it passes about 50K, revisit returning
  reference pages by link.
