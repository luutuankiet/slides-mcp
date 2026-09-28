---
title: Google's Slides MCP server compared with slides-mcp
summary: what Google's hosted Slides MCP server (Developer Preview) offers, measured token cost and write guards side by side with slides-mcp, and how to repeat the measurements
verified: 2026-09-28
---

# Google's Slides MCP server compared with slides-mcp

Google runs a hosted MCP server for Google Slides, available through the Google
Workspace Developer Preview Program. It is a thin, faithful wrapper over four
Slides REST methods: it returns the API's JSON unchanged and forwards writes
unchanged. slides-mcp does the opposite: it shapes the deck for the agent
before the result reaches it, and guards writes. This page records what each
one does, what was measured, and where Google's documentation disagrees with
its own live server.

Everything here was checked on 2026-09-28 against a preview product. Re-run the
commands at the bottom before relying on it.

## Sources, and where they disagree

"Live" means the server's own answer to an unauthenticated MCP request. No
credentials were used and nothing was enabled in any project.

| id | source |
|---|---|
| S1 | Tool reference, `update_presentation`: `https://developers.google.com/workspace/slides/api/reference/mcp/tools_list/update_presentation` |
| S2 | Tool reference, `read_presentation`: `https://developers.google.com/workspace/slides/api/reference/mcp/tools_list/read_presentation` |
| S3 | MCP reference overview: `https://developers.google.com/workspace/slides/api/reference/mcp` |
| S4 | Configure the Slides MCP server (updated 2026-09-18): `https://developers.google.com/workspace/slides/api/guides/configure-mcp-server` |
| S5 | Configure the Google Workspace MCP servers (updated 2026-09-18): `https://developers.google.com/workspace/guides/configure-mcp-servers` |
| S6 | Google Workspace Developer Preview Program (updated 2026-09-18): `https://developers.google.com/workspace/preview` |
| S7 | Slides API usage limits (updated 2026-09-03): `https://developers.google.com/workspace/slides/api/limits` |
| L1 | Live `tools/list` from `https://slidesmcp.googleapis.com/mcp/v1` |
| L2 | Live protected resource metadata: `https://slidesmcp.googleapis.com/.well-known/oauth-protected-resource/mcp/v1` |

> **The reference pages are behind the live server.** S1 to S3 document two
> tools, and `read_presentation` with only `presentationId`. The live
> `tools/list` (L1) has **four** tools, and `read_presentation` also takes
> `fields` (a field mask) and `commentsIncluded`. The page for
> `read_slide_page` returns 404. S3 gives the endpoint as
> `https://slidesmcp.googleapis.com/mcp`, which answers `400`; S4's
> `https://slidesmcp.googleapis.com/mcp/v1` is the one that works. This page
> trusts L1 where they differ.

## Tool surface

Google's four tools (L1):

| tool | wraps | inputs | returns |
|---|---|---|---|
| `read_presentation` | `presentations.get` | `presentationId`, optional `fields` mask, optional `commentsIncluded` | the presentation JSON |
| `read_slide_page` | `presentations.pages.get` | `presentationId`, `pageId`, optional `fields`, optional `commentsIncluded` | one page's JSON |
| `read_slide_page_thumbnail` | `presentations.pages.getThumbnail` | `presentationId`, `pageId`, optional `thumbnailProperties` | `contentUrl`, `widthPx`, `heightPx`: a URL, not an image |
| `update_presentation` | `presentations.batchUpdate` | `presentationId`, `requests[]`, optional `writeControl.requiredRevisionId` | `replies[]` |

Side by side with slides-mcp's ten tools (v2.3.0):

| job | slides-mcp | Google's server |
|---|---|---|
| whole-deck index | `get_deck_outline`: title, archetype label and flags per slide | `read_presentation` with a hand-written `fields` mask |
| read slides at a chosen detail | `read_slides` with `outline` / `summary` / `full` / `raw` and slide selectors (ranges, `hidden:false`, `with_notes`) | `read_slide_page` one page at a time, or `read_presentation` for everything |
| speaker notes | `read_slides(notes_format="markdown")`, `write_speaker_notes` | raw `notesPage` JSON; writes are raw `insertText` / `deleteText` requests |
| search | `search_deck`, substring or regex, notes included | none |
| see a slide | `render_thumbnail`: an inline MCP image | `read_slide_page_thumbnail`: a URL the client may or may not fetch |
| raw write | `exec_batch_update` | `update_presentation` |
| legwork beyond raw writes | `add_section_footers`, `run_deck_script` (sandboxed V8) | none |
| optimistic concurrency | not exposed | `writeControl.requiredRevisionId` |
| comments | none | `commentsIncluded` on reads |
| auth check, skill delivery | `auth_status`, `install_skill` | none; the ~29,000-character `update_presentation` description plays the skill's role |
| other Workspace products | none | sibling servers for Gmail, Drive, Docs, Sheets, Calendar, Chat, People (S5) |

**Only Google has** comment reads and a revision check on writes. Both are small
and could be added here: a comments option on reads and a `writeControl`
passthrough on `exec_batch_update`.

## Read cost

Google's reads return the Slides REST resource as JSON with no summarising and
no text flattening (L1: "Read a JSON representation of a Google Slides
presentation. Corresponds to `presentations.get`").

**Measured on the same four slides**, the scrubbed recording in
`tests/fixtures/sandbox_deck.json`. Serialising that recording is what
`read_presentation` returns for those slides with no mask; running slides-mcp's
tools against `tests/fake_api.py` gives the other rows. Tokens are estimated at
about 4 characters per token, so treat them as order of magnitude.

| read | characters per slide | ~tokens per slide |
|---|---|---|
| Google `read_presentation`, no `fields` | 31,197 | ~7,800 |
| Google `read_presentation`, ideal text-only mask (element ids, text runs, notes text) | 3,281 | ~820 |
| slides-mcp `get_deck_outline` | 234 | ~60 |
| slides-mcp `read_slides(detail="summary")`, notes included | 774 | ~190 |
| slides-mcp `read_slides(detail="full")` | 978 | ~245 |
| slides-mcp `read_slides(detail="raw")` | 8,592 | ~2,150 |

Google's rows are **lower bounds**:

- The recording's masters and layouts are scrubbed to about 1,500 characters. A
  real deck's are far larger and come back on every unmasked read.
- The text-only mask does not walk groups or tables, and an agent rarely writes
  the ideal mask first time. Google's tool description tells the agent to
  "Always use targeted field masks", which leaves knowing the REST schema to
  the agent.
- The `update_presentation` description tells the agent to read again after
  writing to verify: a second full read per write. slides-mcp returns the
  post-write state in the same response.

**Connect-time cost:** Google's `tools/list` is about 38,000 characters, 29,000
of them the `update_presentation` description (an inline guide to 52 request
kinds). slides-mcp's ten tools total about 22,000 characters.

> A 30-slide deck read unmasked through Google's server is roughly 230,000
> tokens by this measure, more than most clients accept in one tool result. The
> same deck's slides-mcp outline is about 1,800 tokens.

## Writes

`update_presentation` is a raw `batchUpdate` passthrough, documented "using the
schema and semantics" of the REST request reference (L1, S1). Its description
inlines schemas for 52 request kinds, destructive ones included.

| guard | slides-mcp `exec_batch_update` | Google `update_presentation` |
|---|---|---|
| destructive requests | refused unless `confirm_destructive=True` | none on the server; only a `destructiveHint: true` annotation on the whole tool, which a client may or may not turn into a prompt |
| dry run | `dry_run=True` returns request kinds and a preview | none |
| post-write state | `post_state`: deck outline plus touched slides, same response | `replies[]` only |
| stale-deck protection | none | `writeControl.requiredRevisionId`; a mismatched revision returns 400 |
| audit trail | one audit line per applied batch | whatever Google logs for the API call |

## Hosting, auth and terms

| aspect | Google's server |
|---|---|
| endpoint | `https://slidesmcp.googleapis.com/mcp/v1`, streamable HTTP (S4, L1). `initialize` reports `serverInfo.name` `StatelessServer` and protocol `2025-06-18` |
| auth | OAuth 2.0. Protected resource metadata names `https://accounts.google.com/` as the only authorization server (L2). `tools/list` and `initialize` answer without a token; `tools/call` returns 401 |
| scopes | reads accept `presentations.readonly`, `drive.readonly`, `presentations`, `drive` or `drive.file`; `update_presentation` needs `presentations`, `drive` or `drive.file` (S1, S2, L2) |
| identity | per user: each person consents and acts as themselves (S5) |
| client registration | Google's authorization server has no dynamic client registration (no `registration_endpoint` in its OpenID configuration). The operator creates a Web OAuth client in their own Cloud project and pastes the client ID and secret into each MCP client (S4) |
| supported clients | Google Antigravity, and Claude (Enterprise, Pro, Max or Team) through a custom connector with the OAuth credentials under Advanced settings (S4, S5) |
| enabling it | enable `slides.googleapis.com` and `slidesmcp.googleapis.com`, configure the consent screen and scopes, create the OAuth client (S4). Developer Preview Program membership is a prerequisite |
| preview terms | S6: pre-GA features "may not be included in public applications"; access may not be granted "outside my domain or company" without Google's permission; Google "may use any data submitted, stored, sent, or received via any Pre-GA APIs to provide, test, analyze, develop, and improve those APIs"; provided "AS IS" |
| quotas and price | Slides API limits apply per project (S7). "All standard use of the Google Slides API is available at no additional cost"; charges past quota are planned for later in 2026. Nothing to host |

slides-mcp calls the generally available Slides REST API, so the preview
data-use clause does not apply to it.

## What would change this comparison

- `read_presentation` gains a compact mode (outline, Markdown or text) or a
  slide-range selector, so a whole-deck read costs tens of tokens per slide
  rather than thousands. That is the main gap.
- `update_presentation` gains a server-side dry run, a destructive-request
  confirmation or a post-write read-back.

## Not yet measured

Each of these needs an enrolled project or a signed-in client:

1. Whether every user must join the Developer Preview Program, or only the
   project.
2. The unmasked `read_presentation` size on a real 20 to 40 slide deck, against
   `get_deck_outline` and `read_slides(detail="summary")` on the same deck.
3. Whether a client fetches `contentUrl`, so the agent can actually see
   Google's thumbnail.
4. Whether each client turns `destructiveHint` into a confirmation prompt
   before a `deleteObject`.

## Repeat the live checks

Unauthenticated requests, so anyone can run them:

```sh
curl -sS -X POST https://slidesmcp.googleapis.com/mcp/v1 \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'

curl -sS https://slidesmcp.googleapis.com/.well-known/oauth-protected-resource/mcp/v1

curl -sS https://accounts.google.com/.well-known/openid-configuration
```

## The general rule

A hosted MCP server that mirrors a REST API is cheap to adopt and expensive to
read through: the agent pays for the API's full JSON on every call. Compare
servers by measuring the same read on the same deck, not by counting tools.
