---
title: Google's Workspace Slides MCP server compared with slides-mcp
summary: what Google's hosted Slides MCP server (Developer Preview) does, how it differs from slides-mcp, and whether it changes the plan to self-host slides-mcp for a small team
verified: 2026-09-28
---

# Google's Workspace Slides MCP server compared with slides-mcp

Research for issue #16. The question: does Google's hosted Slides MCP server
(Developer Preview) make self-hosting slides-mcp on Cloud Run unnecessary for a
team of 5 to 10 people in one Workspace domain, or does it complement it?

## Verdict

**(c) Google's server does not change the plan.** Keep the destination: a
self-hosted slides-mcp with per-user OAuth. Google's server is a thin, faithful
wrapper over four Slides REST methods. It returns raw API JSON, has no deck
outline, no search, no inline image, no write guard and no post-write state.
Those are the reasons slides-mcp exists. For the Slides jobs this team does, it
offers nothing slides-mcp does not already do better, so running both would add
a second connector without giving either one a distinct job.

**It does change one ticket's option set.** Google's server is a working example
of the "client holds a Google token directly" auth design: its protected
resource metadata names `https://accounts.google.com/` as the authorization
server, the client does the OAuth dance with Google using a pre-registered
OAuth client, and the server keeps no state. That is a real candidate for the
auth architecture ticket (#13), and if chosen it would shrink the state storage
ticket (#14) to almost nothing. It has a cost: see
[What this means for the open tickets](#what-this-means-for-the-open-tickets).

**What would flip it:**

| change at GA (or before) | flips to |
|---|---|
| `read_presentation` gains a compact mode (outline, Markdown or text projection) or a slide-range selector, so a whole-deck read costs tens of tokens per slide instead of thousands | (b) run both: Google's server for reads and raw writes, slides-mcp only for `run_deck_script`, section footers and Markdown notes |
| the team finds that field-masked raw JSON is good enough in practice (measure it: check 2 below) | (b) |
| building HTTP mode plus auth for slides-mcp turns out to cost far more than planned, and the team only needs raw `batchUpdate` | (a) for the stopgap period, accepting the read cost |
| Google adds a server-side dry run, destructive-request confirmation or post-write read-back to `update_presentation` | strengthens (b); on its own not enough, the read gap remains |

## Sources and how they disagree

Every claim below cites one of these. "Live" means the server's own answer to
an unauthenticated MCP request made on 2026-09-28; no credentials were used and
nothing was enabled in any project.

| id | source |
|---|---|
| S1 | Tool reference, `update_presentation`: `https://developers.google.com/workspace/slides/api/reference/mcp/tools_list/update_presentation` |
| S2 | Tool reference, `read_presentation`: `https://developers.google.com/workspace/slides/api/reference/mcp/tools_list/read_presentation` |
| S3 | MCP reference overview: `https://developers.google.com/workspace/slides/api/reference/mcp` |
| S4 | Configure the Slides MCP server (last updated 2026-09-18): `https://developers.google.com/workspace/slides/api/guides/configure-mcp-server` |
| S5 | Configure the Google Workspace MCP servers (last updated 2026-09-18): `https://developers.google.com/workspace/guides/configure-mcp-servers` |
| S6 | Google Workspace Developer Preview Program (last updated 2026-09-18): `https://developers.google.com/workspace/preview` |
| S7 | Slides API usage limits (last updated 2026-09-03): `https://developers.google.com/workspace/slides/api/limits` |
| L1 | Live `tools/list` from `https://slidesmcp.googleapis.com/mcp/v1` |
| L2 | Live protected resource metadata: `https://slidesmcp.googleapis.com/.well-known/oauth-protected-resource/mcp/v1` and the per-tool variants |

> **The reference pages are behind the live server.** S1, S2 and S3 document
> two tools, and `read_presentation` with only `presentationId`. The live
> `tools/list` (L1) has **four** tools, and `read_presentation` also takes
> `fields` (a field mask) and `commentsIncluded`. The page for
> `read_slide_page` returns 404. S3 gives the endpoint as
> `https://slidesmcp.googleapis.com/mcp`; that path answers `400`, and S4's
> `https://slidesmcp.googleapis.com/mcp/v1` is the one that works. This page
> trusts L1 where they differ.

## 1. Tool surface

Google's four tools (L1):

| tool | wraps | inputs | returns |
|---|---|---|---|
| `read_presentation` | `presentations.get` | `presentationId`, optional `fields` mask, optional `commentsIncluded` | `content`: the presentation JSON |
| `read_slide_page` | `presentations.pages.get` | `presentationId`, `pageId`, optional `fields`, optional `commentsIncluded` | `content`: one page's JSON |
| `read_slide_page_thumbnail` | `presentations.pages.getThumbnail` | `presentationId`, `pageId`, optional `thumbnailProperties` (`mimeType` PNG; `thumbnailSize` SMALL 200px, MEDIUM 800px, LARGE 1600px, WIDTH2000_PX) | `contentUrl`, `widthPx`, `heightPx`: a URL, not an image |
| `update_presentation` | `presentations.batchUpdate` | `presentationId`, `requests[]`, optional `writeControl.requiredRevisionId` | `replies[]` |

Side by side with slides-mcp's ten tools (as of v2.3.0):

| job | slides-mcp | Google's server | gap |
|---|---|---|---|
| auth check | `auth_status` | none | Google: none needed, the client owns the token |
| ship the agent skill | `install_skill` | none | Google: the ~29,000-character `update_presentation` description plays the same role, sent on every connect |
| whole-deck index | `get_deck_outline`: title, archetype label, flags per slide | `read_presentation` with a hand-written `fields` mask | Google has no outline or archetype; the agent must compose a mask |
| read slides at a chosen detail | `read_slides` with `outline` / `summary` / `full` / `raw`, slide selectors (ranges, `hidden:false`, `with_notes`) | `read_slide_page` one page at a time, or `read_presentation` for all | Google has no detail levels and no multi-slide selector |
| speaker notes as text or Markdown | `read_slides(notes_format="markdown")`, `write_speaker_notes` | raw `notesPage` JSON; writes via raw `insertText` / `deleteText` requests | Google has no notes convenience either way |
| search | `search_deck` (substring or regex, notes included) | none | agent must read everything and search in context |
| see a slide | `render_thumbnail`: inline MCP image | `read_slide_page_thumbnail`: a `contentUrl` string | Google's agent sees a URL, not pixels, unless the client fetches it (check 3) |
| raw write | `exec_batch_update` | `update_presentation` | see part 3 |
| optimistic concurrency | not exposed | `writeControl.requiredRevisionId` | **slides-mcp lacks this** |
| comments | none | `commentsIncluded` on reads; `insertComment`, `addCommentReply`, `updateCommentPost`, `deleteComment`, `deleteCommentReply` listed as request kinds | **slides-mcp lacks a comment read.** The five comment kinds are in the REST request reference but marked Developer Preview, so `exec_batch_update` can already forward them from an enrolled project. `deleteComment` and `deleteCommentReply` are not in slides-mcp's destructive list (`writes.py` `DESTRUCTIVE_KINDS`), so they would pass the guard unconfirmed |
| section footers | `add_section_footers`, idempotent | none | |
| read, compute and edit in one call | `run_deck_script` (sandboxed V8) | none | |
| other Workspace products | none | sibling servers for Gmail, Drive, Docs, Sheets, Calendar, Chat, People (S5, S6) | out of scope for this repo |

**Coverage only Google has:** reading comments, and a revision guard on
writes. Both are small and could be added to slides-mcp (a `commentsIncluded`
read option and a `writeControl` passthrough). **Coverage only slides-mcp has:** outline,
detail levels, slide selectors, search, inline thumbnails, Markdown notes,
section footers, deck scripts, the destructive guard, dry run and post-write
state.

## 2. Read quality and token cost

Google's reads return the Slides REST resource as JSON, unchanged (L1: "Read a
JSON representation of a Google Slides presentation. Corresponds to
`presentations.get`"). No summarising, no archetype, no text flattening.

**Measured on the same deck.** slides-mcp's unit tests serve a scrubbed
recording of four real slides (`tests/fixtures/sandbox_deck.json`, served by
`tests/fake_api.py`). Serialising that recording is what `read_presentation`
would return for those slides with no mask; running slides-mcp's tools over the
fake API gives the other rows. Tokens are estimated at about 4 characters per
token, so treat them as order-of-magnitude.

| read | chars per slide | ~tokens per slide |
|---|---|---|
| Google `read_presentation`, no `fields` | 31,197 | ~7,800 |
| Google `read_presentation`, ideal text-only mask (element ids, text runs, notes text) | 3,281 | ~820 |
| slides-mcp `get_deck_outline` | 234 | ~60 |
| slides-mcp `read_slides(detail="summary")` (notes included) | 774 | ~190 |
| slides-mcp `read_slides(detail="full")` | 978 | ~245 |
| slides-mcp `read_slides(detail="raw")` | 8,592 | ~2,150 |

Why these are lower bounds for Google's side:

- The recording's masters and layouts are scrubbed to about 1,500 characters;
  a real deck's are far larger and come back on every unmasked
  `read_presentation`.
- The text-only mask above does not walk groups or tables, and a real agent
  rarely writes the ideal mask first time. Google's own tool description tells
  the agent to "Always use targeted field masks", which puts the burden of
  knowing the REST schema on the agent.
- Google's workflow note inside the `update_presentation` description says to
  read again after writing to verify. That is a second full read per write;
  slides-mcp returns the post-write state in the same response.

**Connect-time cost.** Google's `tools/list` is about 38,000 characters, of
which about 29,000 is the `update_presentation` description (an inline guide to
52 request kinds). slides-mcp's ten tools total about 22,000 characters.

> A 30-slide deck read unmasked through Google's server is roughly 230,000
> tokens by this measure, larger than most clients accept in one tool result.
> The same deck's slides-mcp outline is about 1,800 tokens.

## 3. Writes

`update_presentation` is a **raw `batchUpdate` passthrough**: `presentationId`
plus `requests[]`, "using the schema and semantics documented at"
the REST request reference (L1, S1). The description inlines schemas for 52
request kinds, destructive ones included (`deleteObject`, `deleteText`,
`deleteTableRow`, `deleteTableColumn`, `replaceAllText`, `deleteComment`,
`deleteCommentReply`, among others).

| guard | slides-mcp `exec_batch_update` | Google `update_presentation` |
|---|---|---|
| destructive requests | refused unless `confirm_destructive=True` | none server-side; only the annotation `destructiveHint: true` on the whole tool (L1, S1), which a client may or may not turn into a confirmation prompt |
| dry run | `dry_run=True` returns request kinds and a preview | none |
| post-write state | `post_state` envelope: deck outline plus touched slides, same response | `replies[]` only; the description recommends a separate read to verify |
| stale-deck protection | none | `writeControl.requiredRevisionId`: mismatched revision returns 400 |
| audit trail | one audit line per applied batch | whatever Google logs for the API call |

## 4. Hosting, auth and access

| aspect | Google's server |
|---|---|
| endpoint | `https://slidesmcp.googleapis.com/mcp/v1` (S4, L1). Streamable HTTP (S4). `initialize` reports `serverInfo.name` `StatelessServer` and protocol `2025-06-18` (live) |
| auth | OAuth 2.0 (S4, S5). Protected resource metadata names `https://accounts.google.com/` as the only authorization server (L2). Unauthenticated `tools/call` returns 401 with a per-tool `resource_metadata` URL; `tools/list` and `initialize` answer without a token (live) |
| scopes | read tools accept `presentations.readonly`, `drive.readonly`, `presentations`, `drive`, `drive.file`; `update_presentation` needs `presentations`, `drive` or `drive.file` (S1, S2, L2) |
| identity | per user: each person consents and the tool acts as them, inheriting their Drive permissions (S5) |
| client registration | Google's authorization server has no dynamic client registration (its OpenID configuration has no `registration_endpoint`). So the operator creates a Web application OAuth client in their own GCP project and pastes the client ID and secret into each MCP client (S4) |
| supported clients | Google Antigravity, and Claude on Enterprise, Pro, Max or Team plans via Settings, Connectors, "Add custom connector" with the OAuth credentials under Advanced settings; redirect URI `https://claude.ai/api/mcp/auth_callback` (S4, S5). "Other AI applications supporting Streamable HTTP" are mentioned without detail |
| enabling it | in `<GCP_PROJECT>`: `gcloud services enable slides.googleapis.com` and `gcloud services enable slidesmcp.googleapis.com`, configure the OAuth consent screen, add the four scopes, create the OAuth client (S4). S4 names no Workspace Admin console step |
| domain restriction | consent screen audience: S4 says "select Internal. If you can't select Internal, select External." Internal limits consent to the organisation's users |
| preview status | "Available as part of the Google Workspace Developer Preview Program" (S1 to S4). S4 lists "Membership in the Google Workspace Developer Preview Program" as a prerequisite |
| preview terms | S6: program features "may not be included in public applications prior to the General Availability (GA) announcement"; you "may not grant end users access, outside my domain or company" to apps built on pre-GA APIs without Google's permission; Google "may use any data submitted, stored, sent, or received via any Pre-GA APIs to provide, test, analyze, develop, and improve those APIs"; pre-GA APIs are provided "AS IS" |
| quotas | Slides API limits apply per project: reads 3,000 per minute per project and 600 per minute per user per project; `getThumbnail` 300 and 60; writes 600 and 60 (S7). No MCP-specific quota is documented |
| pricing | "All standard use of the Google Slides API is available at no additional cost. Exceeding the quota request limits is planned to incur charges to your Google Cloud billing account later in 2026." (S7). No MCP-specific price is documented. Nothing to host |

**What the preview terms mean for this team.** Use inside one domain is
allowed. The data-use clause is the one to take to whoever owns data policy:
deck contents read through the preview server may be used by Google to improve
the API. Self-hosted slides-mcp calls the GA Slides REST API and does not fall
under that clause.

## What this means for the open tickets

| ticket | effect |
|---|---|
| map (#9) destination | unchanged: self-host slides-mcp. Replace the "under question" note with this verdict |
| #13 auth architecture | **new evidence for the "client holds a Google token" option.** Google runs exactly that: the MCP client authorises against `accounts.google.com` with a pre-registered client, then sends a Google access token as the bearer. slides-mcp's HTTP mode could do the same: publish protected resource metadata naming Google, accept the bearer, and call Slides with it. The cost to weigh: the MCP authorization spec forbids a server from passing a client's token through to an upstream API and requires tokens to be audience-bound to the MCP server. Google can do this because it is the resource server; slides-mcp would be forwarding. It also only works with clients that accept a pre-registered client ID and secret, since Google has no dynamic registration |
| #14 state storage | shrinks to nearly nothing **if** #13 picks the Google-token design: the client holds and refreshes the Google token, and the server stores nothing. Otherwise unchanged |
| #15 per-request credentials | unchanged in shape: the seam is still needed. With the Google-token design the per-request credential is simply the bearer token |

## Checks the docs leave open

None of these were run. Each needs an enrolled project or a signed-in client.

1. **Does every teammate need to be enrolled in the Developer Preview Program,
   or only the project?** S4 says membership is a prerequisite and S6 does not
   say per person. Check: with the project enrolled and `slidesmcp.googleapis.com`
   enabled, have a teammate in `<WORKSPACE_DOMAIN>` who is not enrolled add the
   connector and call `read_presentation` on a deck they own. Expected if only
   the project matters: the deck JSON. Expected otherwise: an error naming the
   preview or a 403.
2. **Real token cost on a team deck.** Call `read_presentation` with no `fields`
   on a typical 20 to 40 slide deck and record the length of `content`; then
   call slides-mcp `get_deck_outline` and `read_slides(detail="summary")` on the
   same deck. Expected: the unmasked read is two orders of magnitude larger, as
   in part 2.
3. **Can the agent see Google's thumbnail?** Call `read_slide_page_thumbnail`
   from Claude and ask the agent to describe the slide. Expected: the agent
   receives only `contentUrl` and cannot describe it unless the client fetches
   the URL; slides-mcp's `render_thumbnail` returns the image inline.
4. **Does the client confirm destructive writes?** From each client the team
   uses, call `update_presentation` with one `deleteObject` on a scratch deck.
   Expected: either a client-side confirmation prompt (driven by
   `destructiveHint`) or the delete runs immediately. Google's server itself
   will not refuse it.
5. **Admin controls.** If `<WORKSPACE_DOMAIN>` restricts third-party app access
   under the Admin console's API controls, check whether an Internal OAuth
   client in `<GCP_PROJECT>` needs to be marked trusted before teammates can
   consent. This applies equally to a self-hosted slides-mcp.
6. **Pre-registered client support in each harness.** For the Google-token
   design in #13, confirm each client the team uses (Claude web and desktop,
   Claude Code, others) accepts a pre-registered OAuth client ID and secret for
   a remote MCP server. Expected from S4: Claude connectors do; others unknown.

## How the live facts were gathered

Unauthenticated requests only, so anyone can repeat them:

```sh
curl -sS -X POST https://slidesmcp.googleapis.com/mcp/v1 \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'

curl -sS https://slidesmcp.googleapis.com/.well-known/oauth-protected-resource/mcp/v1

curl -sS https://accounts.google.com/.well-known/openid-configuration
```

The token table in part 2 comes from running slides-mcp's tools against
`tests/fake_api.py` and serialising `tests/fixtures/sandbox_deck.json` as
compact JSON.
