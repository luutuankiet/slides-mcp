---
title: Claude for Google Workspace add-on compared with slides-mcp
summary: what Anthropic's Slides sidebar add-on is, how it differs from the Claude Google Slides connector, and the add-on's own verdict on slides-mcp
verified: 2026-10-07
---

# Claude for Google Workspace add-on compared with slides-mcp

In 2026 Anthropic shipped Claude for Google Workspace, which has two separate
parts. Only one of them is new machinery. This page records what each part is,
what the add-on said about slides-mcp when asked, and what is still unverified.

Checked on 2026-10-07 against a beta product. Re-read the sources before relying
on it.

## Sources

| id | source |
|---|---|
| S1 | Announcement: `https://claude.com/resources/articles/claude-now-works-in-google-docs-sheets-and-slides` |
| S2 | Help page, Use Claude in Google Docs, Sheets and Slides: `https://support.claude.com/en/articles/16951679-use-claude-in-google-docs-sheets-and-slides` |
| S3 | Marketplace listing: `https://workspace.google.com/marketplace/app/claude/12459801340` |
| A1 | The add-on's own answer when given this repo's URL and asked for a verdict (2026-10-07). It describes itself; nothing it says was tested |

## Two parts, two mechanisms

| part | what it is | Slides access |
|---|---|---|
| **Connector** (beta) | start in a Claude chat and create or edit Google files from there (S1) | Google's hosted Slides MCP server: `read_presentation`, `read_slide_page`, `read_slide_page_thumbnail`, `update_presentation`. Compared in `google-slides-mcp.md` |
| **Add-on** | a sidebar opened from `Extensions > Claude > Open Claude` inside the open file (S2) | runs inside the editor against the open deck only |

> The connector is not new machinery for Slides: it is the raw Google server
> plus a client-side skill. The add-on is the new part, and it does not call
> Google's MCP server.

## The add-on, from its documentation

- **One file only.** It "operates only on the file it's opened in"; other files
  are reachable only through a separately connected Google Drive connector (S2).
- **Partial reads.** "Claude reads the relevant parts of the open file, plus
  whatever you have selected" (S2). Which parts is decided by the product, not
  the caller.
- **Permissions.** It asks to "View and manage documents, spreadsheets, or
  presentations that this application has been installed in" and to display
  sidebar content. It does not request Drive, Gmail, Calendar, or "the ability
  to make network requests" (S2).
- **Limits.** Single operations are capped at six minutes. Firefox loads the
  sidebar but actions do not complete. It cannot copy files externally or export
  PDF (S2).
- **History.** "Chat history is stored locally in your browser, per file and is
  not synced across devices" (S2).
- **Edit modes.** "Ask before edits" shows an approval card per change; "Accept
  all edits" applies without stopping (S1).
- **Availability.** Beta on all paid plans; install from the Marketplace or by
  admin deployment (S1, S3).

The current-file scope, the six-minute cap (the Apps Script execution limit)
and the absence of a network-request permission suggest the edits run as Apps
Script inside the editor. A1 confirms this from the inside: it names its own
tool `execute_apps_script`.

## The add-on's verdict on slides-mcp (A1)

Summary of what it said, in its words where quoted. It is a self-report from
one session and was not checked.

> "Yours adds real value. My tools don't cover it, and the two are closer to
> complements than substitutes."

**Where it said slides-mcp is ahead**

| area | slides-mcp | add-on, as it described itself |
|---|---|---|
| reach | any MCP client, headless hosts, any deck by URL, HTTP mode with per-user sign-in, `create_deck` | the open deck only; cannot open other decks or create one |
| reading a large deck | `get_deck_outline`, `search_deck`, detail levels | one slide at a time through `list_slide_shapes` and `read_slide_text` |
| seeing a slide | `render_thumbnail` and post-write receipts | no way to render a slide image; it called this its biggest gap |
| write safety | dry run, `plan_id` apply, `confirm_destructive`, audit line, size guard, post-write state | `execute_apps_script` runs directly with no dry run or preview |
| request coverage | raw `batchUpdate` and `run_deck_script` reach request kinds `SlidesApp` lacks, such as `replaceAllShapesWithImage`, field-masked updates and table borders | limited to what `SlidesApp` exposes |
| images | `place_image`: SVG as native shapes or raster | insert from URL or its icon library |

**Where it said the add-on is ahead**

- No setup: it runs with the user's own session, with no OAuth client, token or
  deploy.
- Design checks: `verify_slides` checks overlap, bounds and WCAG contrast.
  slides-mcp's overflow warning is a glyph-width estimate and it has no
  contrast check.
- Helpers for icons, native charts through the Charts service, and theme and
  master editing.
- Web search, other connectors and skills in the same session.

**Where it said slides-mcp is weak**

- The archetype classifier is a label nothing depends on.
- The README and AGENTS.md version notes lagged the released package at the
  time it read them.
- No design layer: contrast and text-fit checks were the one thing it suggested
  porting.

## What would change this comparison

- The add-on gains a way to be called by an agent outside the editor, or access
  to decks other than the open one.
- The add-on syncs history across devices, or exposes how it chooses the
  "relevant parts" it reads.

## Not yet verified

1. A1's tool names and capabilities are the add-on describing itself.
2. The token cost of the add-on's reads on a real deck, against
   `get_deck_outline` on the same deck.
3. Whether the Apps Script inference holds beyond the tool name A1 reported.
