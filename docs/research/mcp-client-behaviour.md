---
title: How MCP clients connect, register and log in against a remote OAuth server
summary: Per-client protocol era, connect timing, client registration and login UX for Claude Code, hosted Claude, VS Code, Cursor and Gemini CLI, and what that means for a scale-to-zero server
verified: 2026-09-28
---

# How MCP clients connect, register and log in against a remote OAuth server

Research for issue #11. Question: how do Claude Code, the hosted Claude apps
(claude.ai, Claude Desktop, mobile), Cursor, VS Code and Gemini CLI behave
against a remote Streamable HTTP MCP server protected by OAuth, and which of
them can start the login flow on their own?

Everything below was read from primary sources on 2026-09-28: the MCP
specification repository, the MCP TypeScript SDK source, the VS Code and Gemini
CLI source, and each vendor's own documentation and changelog. Where a
behaviour is not written down anywhere and can only be observed on the wire,
the table says **empirical** instead of guessing.

## Gist

1. **Every client can start the login itself**, as long as the authorization
   server offers a registration path it speaks. The one registration path all
   five speak today is **Dynamic Client Registration (DCR)**. CIMD alone locks
   out Cursor and Gemini CLI.
2. **Only Claude Code speaks the stateless `2026-07-28` transport.** VS Code,
   Gemini CLI and (as far as their docs say) Cursor and the hosted Claude apps
   still open with the legacy `initialize` handshake. A server that only speaks
   `2026-07-28` fails them all, so the server has to be **dual-era**.
3. **The standing-stream cost is under the server's control.** Legacy clients
   open a standalone `GET` stream after `initialize`, but both the TypeScript
   SDK and VS Code treat `405 Method Not Allowed` as "no stream" and stop.
   Claude Code on `2026-07-28` opens a `subscriptions/listen` stream only if
   the server advertises `listChanged` capabilities; do not advertise them.
4. **Most clients connect at session start**, so expect one cold start per
   session per user, whether or not a tool is used. Claude Code (with its
   discovery cache on) and VS Code (after the first run) can defer the connect
   until a tool is actually used.
5. **Google is not a usable authorization server for these clients on its
   own**: it offers neither DCR nor CIMD, so the server needs its own
   authorization server in front of Google (feeds issue #13).

## Compatibility table

| Client | Protocol era | Standing stream | Connects when | Registration | Starts login itself? | Tokens stored / refreshed |
|---|---|---|---|---|---|---|
| **Claude Code** (CLI) | `2026-07-28` by default on direct HTTP servers, falls back to legacy | `subscriptions/listen` POST stream if server advertises `listChanged`; legacy fallback opens `GET` (405 stops it) | Session start; with discovery cache on, first tool call | Pre-registered id (`--client-id`), CIMD, DCR | ✅ yes, user runs `/mcp` or `claude mcp login`; startup notice flags it | OS keychain (macOS) or credentials file; refreshes on 401 and retries once |
| **Hosted Claude** (claude.ai, Desktop, mobile) | Not documented; **empirical** | Not documented; **empirical** (405 on `GET` is safe either way) | Not documented; **empirical** | CIMD, DCR, or client id in the connector's advanced settings | ✅ yes, user clicks **Connect** (Team/Enterprise: an Owner adds the connector first) | Anthropic-side; refresh on 401 and up to 5 min before expiry |
| **VS Code** | Legacy only (`2025-11-25`) | `GET` after first successful POST; any `4xx` (e.g. 405) disables it | On chat submit for new or changed servers; otherwise cached tool list, **empirical** when it reconnects | Stored id, CIMD (since 1.106), DCR, then prompts user to paste a client id | ✅ yes, auth dialog on 401 then browser | VS Code secret storage; refreshes with refresh token |
| **Cursor** | Not documented; **empirical** | Not documented; **empirical** | Not documented; **empirical** | DCR, or static `auth.CLIENT_ID` in `mcp.json`; **no CIMD** (staff, 2026-09-22) | ✅ yes, login from MCP settings (desktop) or `/mcp` (CLI) | Not documented |
| **Gemini CLI** | Legacy only (SDK `1.23.0`) | `GET` after `initialized`; 405 stops it | Session start (tool discovery runs over every configured server) | Configured `oauth.clientId`, else DCR; **no CIMD** | ⚠️ automatic only with `"oauth": {"enabled": true}` in config; otherwise user must run `/mcp auth <name>` | OS keychain, encrypted-file fallback; refreshes silently |

> No client in the list is unable to start the login flow. The two that need a
> nudge are **Gemini CLI** (needs `oauth.enabled: true` in the teammate's
> config, or a manual `/mcp auth <name>`) and **Claude Code** (the user opens
> `/mcp`; it does not pop the browser mid-session on its own).

### Redirect URIs each client uses

An authorization server we run must accept all of these. Taken from each
client's docs or source.

| Client | Redirect URI |
|---|---|
| Hosted Claude | `https://claude.ai/api/mcp/auth_callback` |
| Claude Code | `http://localhost:<any port>/callback` and `http://127.0.0.1:<any port>/callback` (match without the port) |
| VS Code | `http://127.0.0.1:33418` and `https://vscode.dev/redirect` |
| Cursor | `http://localhost:8787/callback` (desktop), `https://www.cursor.com/agents/mcp/oauth/callback` (web and cloud agents) |
| Gemini CLI | `http://localhost:<random port>/oauth/callback` unless `redirectUri` is configured |

---

## What the spec changed in `2026-07-28`

From `docs/specification/2026-07-28/changelog.mdx` in
`modelcontextprotocol/modelcontextprotocol`:

- **No sessions, no handshake.** `Mcp-Session-Id` and `initialize` are gone;
  each request carries protocol version and client capabilities in `_meta`.
  `server/discover` is new and servers MUST implement it.
- **No standalone `GET`.** The `GET` endpoint is replaced by
  `subscriptions/listen`, "a single long-lived POST-response stream for
  opted-in server-to-client change notifications". So the stateless era still
  has a long-lived stream; it is just opt-in per notification type.
- **DCR is deprecated** in favour of Client ID Metadata Documents (CIMD).
  Earliest removal is the first revision released on or after 2027-07-28.
  The client-registration page sets this priority for clients: pre-registered
  client, then CIMD if the authorization server advertises
  `client_id_metadata_document_supported`, then DCR if it advertises a
  `registration_endpoint`, then prompt the user.

Two backward-compatibility rules decide the server design
(`basic/versioning.mdx`, `basic/transports/streamable-http.mdx`):

- A legacy client against a modern-only server **fails**. A legacy client
  against a dual-era server works.
- A `2026-07-28`-only server that receives legacy traffic SHOULD answer `GET`
  and `DELETE` with `405`, ignore `Mcp-Session-Id`, and ignore
  `Last-Event-ID`.

## Why a `405` is enough to kill the legacy standing stream

- **MCP TypeScript SDK** (used by Gemini CLI at `1.23.0`, and by Claude Code):
  `packages/client/src/client/streamableHttp.ts` starts the `GET` stream only
  when the server accepts `notifications/initialized` with `202`, and
  `_startOrAuthSse` returns quietly on `405` ("A 405 on the standalone-GET path
  is benign"). The same two code paths exist at tag `1.23.0`
  (`src/client/streamableHttp.ts`, lines 233 and 540).
- **VS Code** has its own transport in
  `src/vs/workbench/api/common/extHostMcp.ts`. `_attachStreamableBackchannel`
  opens the `GET` after the first successful POST; on any status `>= 400` it
  logs "they will be disabled" and returns. If the server does serve the
  stream, VS Code reconnects with backoff for as long as the server is
  configured, which on Cloud Run would be a request billed indefinitely.
- **Modern-era TypeScript SDK clients** only auto-open `subscriptions/listen`
  for the intersection of the client's configured `listChanged` handlers and
  the server's advertised `listChanged` capabilities
  (`packages/client/src/client/client.ts`, around line 1193). An empty
  intersection skips the stream. Claude Code does open this stream in practice:
  its 2.1.233 changelog fixes "MCP v2 connections endlessly reopening the
  subscriptions/listen stream against servers that terminate long-held streams
  on a fixed timeout (e.g. serverless hosts)".

For the hosted Claude apps and Cursor the transport code is closed and
undocumented, so whether they try a stream at all is **empirical**; the `405`
answer is safe for them either way because the spec makes the `GET` stream
optional for servers.

## Per client

### Claude Code

Sources: `https://code.claude.com/docs/en/mcp`, the `CHANGELOG.md` in
`anthropics/claude-code`, and the connector authentication page below.

- **Era.** 2.1.274: "use the v2 MCP client and MCP 2026-07-28 negotiation with
  direct HTTP servers by default, as other installs already do (opt out:
  `MCP_SDK_GENERATION=v1` or `MCP_PROTOCOL_NEGOTIATION=legacy`)". The TypeScript
  SDK's `auto` mode probes with `server/discover` and falls back to
  `initialize` on a legacy server.
- **Connect timing.** "Claude Code connects MCP servers automatically at
  session startup." With the discovery cache on, a previously used remote
  server shows `connects on first use` and is contacted only when Claude calls
  one of its tools. The cache is off by default since 2.1.238 unless a gradual
  rollout turned it on; `MCP_DISCOVERY_CACHE=1` forces it on.
- **Registration.** DCR by default; CIMD "discovers these automatically" (added
  2.1.81); pre-registered `--client-id` / `--client-secret` with
  `--callback-port` (added 2.1.30). Its CIMD document is hosted by Anthropic
  and declares loopback redirects.
- **Login UX.** A `401` or `403` marks the server `! Needs authentication`, and
  a startup notice lists such servers. The user runs `/mcp` or
  `claude mcp login <name>`, which opens the browser; `--no-browser` prints the
  URL for SSH sessions. In `claude -p` runs Claude is told the tools are
  unavailable until the user authorizes.
- **Tokens.** Stored in the system keychain (macOS) or a credentials file. On a
  `401` it refreshes, reconnects and retries once; a rejected refresh token
  produces a notice pointing at `/mcp`.

### Hosted Claude (claude.ai, Claude Desktop, mobile)

Sources: `https://claude.com/docs/connectors/building/authentication.md`,
`https://claude.com/docs/connectors/building/index.md`,
`https://claude.com/docs/connectors/custom/add-unlisted.md`.

- **Where requests come from.** Anthropic's servers, not the user's machine
  (egress range `160.79.104.0/21`). A Cloud Run URL is reachable, so this is
  fine; it does mean the authorization server's discovery and token endpoints
  must be reachable from that range too.
- **Era, stream, connect timing.** The docs name only the `2025-03-26`,
  `2025-06-18` and `2025-11-25` *authorization* specs and say resource
  subscriptions are unsupported. Nothing says which transport era is spoken,
  whether a `GET` stream is opened, or when the server is contacted relative to
  a conversation. **Empirical.**
- **Registration.** CIMD ("Use Claude's published identity", recommended), DCR
  ("Register automatically"), or "Use your own OAuth client" with a client id
  and optional secret. CIMD is used only when the authorization server
  advertises both `client_id_metadata_document_supported: true` and `none` in
  `token_endpoint_auth_methods_supported`; otherwise it falls back to DCR. With
  DCR, "Claude ... register[s] a new client on every fresh connection".
- **Login UX.** The user clicks **Connect** in Customize > Connectors and the
  browser flow runs. On Team and Enterprise plans an Owner must add the custom
  connector for the organization first; members then connect individually. A
  `401` with a `resource_metadata` pointer is required to start sign-in; a
  `WWW-Authenticate` header on a `200` is ignored, and only the first entry of
  `authorization_servers` is used.
- **Tokens.** Held by Anthropic. Refreshed reactively on `401` and proactively
  up to five minutes before expiry. Discovery, registration and token
  endpoints get 10 seconds, refresh gets 30 seconds, which matters if the
  authorization server itself scales to zero.

### VS Code

Sources: `microsoft/vscode` at `main`, plus
`https://code.visualstudio.com/docs/copilot/customization/mcp-servers`
(updated 2026-09-16) and the 1.106 release notes.

- **Era.** `src/vs/platform/mcp/common/modelContextProtocol.ts` pins
  `LATEST_PROTOCOL_VERSION = "2025-11-25"`, and
  `mcpServerRequestHandler.ts` always sends `initialize` then
  `notifications/initialized`. No `2026-07-28` support in source.
- **Stream.** See the `405` section above: a `4xx` on the `GET` disables it.
  Also note: a non-auth `4xx` on the *first* POST makes VS Code fall back to
  the legacy HTTP+SSE transport, which opens a `GET`, so the server must
  answer the legacy `initialize` POST successfully.
- **Connect timing.** `chat.mcp.autostart` (default `newAndOutdated`) starts
  "servers that have never run and servers whose configuration has changed"
  when a chat message is submitted. Other servers are served from a cached
  tool list; exactly when VS Code reconnects them is not documented.
  **Empirical.**
- **Registration.** `mainThreadAuthentication.ts`: a stored client id wins;
  otherwise, if the authorization server advertises
  `client_id_metadata_document_supported`, VS Code uses the product's CIMD URL
  (`authClientIdMetadataUrl`, set in Microsoft's builds, not in the open-source
  product file); otherwise DCR; if the server has no DCR it asks the user to
  paste a client id and optional secret registered for
  `http://127.0.0.1:33418` and `https://vscode.dev/redirect`. The MCP config
  also accepts an `oauth` block with a `clientId`.
- **Login UX.** A login dialog appears when the server requires
  authentication, then the browser opens. Tokens live in VS Code secret
  storage (`dynamicAuthenticationProviderStorageService.ts`) and are refreshed
  with the refresh token when `expires_in` elapses
  (`extHostAuthentication.ts`).

### Cursor

Sources: `https://cursor.com/docs/mcp`, `https://cursor.com/docs/cli/changelog`,
Cursor forum thread "MCP OAuth: CIMD Support Plans and Timelines".

- **Era, stream, connect timing.** Closed source and not documented.
  **Empirical.**
- **Registration.** DCR by default; a static `auth` object in `mcp.json` with
  `CLIENT_ID` (and optional `CLIENT_SECRET`, `scopes`) for providers that give
  a fixed client id. **No CIMD**: Cursor staff on 2026-09-22, "CIMD support for
  MCP OAuth is still on our radar ... I don't have a specific timeline".
- **Login UX.** Login is started from the MCP settings (desktop) or the `/mcp`
  pager (CLI), and the client runs the browser flow. Callback URLs are fixed:
  `http://localhost:8787/callback` for desktop and
  `https://www.cursor.com/agents/mcp/oauth/callback` for web and cloud agents.
- **Tokens.** Storage and refresh are not documented.

### Gemini CLI

Sources: `google-gemini/gemini-cli` at `main` (`2fe7c2d`) and its
`docs/tools/mcp-server.md`.

- **Era.** Depends on `@modelcontextprotocol/sdk` `1.23.0`, which only speaks
  the legacy handshake.
- **Stream.** The SDK `1.23.0` transport opens `GET` after `initialized` and
  stops on `405`.
- **Connect timing.** `discoverMcpTools()` connects to every configured server
  to fetch tool definitions, so a cold start happens at CLI startup.
- **Registration.** Uses its own OAuth provider
  (`packages/core/src/mcp/oauth-provider.ts`), not the SDK's: a configured
  `oauth.clientId`, otherwise DCR against the discovered
  `registration_endpoint`, otherwise the error "No client ID provided and
  dynamic registration not supported". No CIMD in source.
- **Login UX.** In `packages/core/src/tools/mcp-client.ts`, a `401` without
  `oauth.enabled` in the server's config calls `showAuthRequiredMessage`, which
  throws with "requires authentication using: /mcp auth <name>". With
  `"oauth": {"enabled": true}` it reads the `WWW-Authenticate` header, runs
  discovery, opens the browser and retries. Needs a local browser and a
  loopback callback; the docs say it does not work in headless or plain SSH
  sessions. It also requires the authorization server to return `iss` on the
  callback (RFC 9207) when an issuer is known.
- **Tokens.** `HybridTokenStorage`: OS keychain, falling back to an encrypted
  file (the docs still mention `~/.gemini/mcp-oauth-tokens.json`). Refreshed
  silently in `getValidToken` when a refresh token exists.

---

## What this means for the design

- **Registration: offer DCR, add CIMD.** DCR is the only mechanism all five
  clients speak today. CIMD is what the spec now prefers and what hosted
  Claude, Claude Code and VS Code pick first when it is advertised, and it
  needs no stored registration state. Offering both costs a registration
  endpoint whose client records must survive scale-to-zero.
- **Google cannot be the authorization server directly.** Google OAuth does
  not advertise DCR or CIMD, so the MCP server needs its own authorization
  server that clients register with and that delegates the user sign-in to
  Google. Its redirect-URI allowlist is the table above.
- **Dual-era, stateless in both.** Answer legacy `initialize` without minting
  a session id, answer `GET` and `DELETE` with `405`, implement
  `server/discover` for Claude Code, and advertise no `listChanged`
  capabilities so no client opens `subscriptions/listen`. slides-mcp's tool
  list is static, so nothing is lost.
- **Cold starts.** Budget one cold start per client session per user. The
  hosted Claude token endpoint timeout of 10 seconds applies to the
  authorization server's cold start too.

## Open, needs a wire trace

These can only be settled by pointing each client at a deployed server and
logging requests:

1. Hosted Claude: which era it negotiates, whether it tries `GET` or
   `subscriptions/listen`, and whether it contacts the server when a
   conversation opens or only on tool use.
2. Cursor: the same three questions.
3. VS Code: when a cached server is reconnected (on chat submit, or on first
   tool call).
4. Claude Code: whether anything other than `listChanged` makes it open a
   `subscriptions/listen` stream.
