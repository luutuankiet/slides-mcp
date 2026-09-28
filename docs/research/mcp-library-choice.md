# Which MCP library for stateless HTTP plus a Google-backed OAuth server

verified: 2026-09-28, against the released wheels `mcp` 1.27.0, `mcp` 2.2.0,
`fastmcp` 4.0.10 (which is a meta-package over `fastmcp-slim` 4.0.10) and
`py-key-value-aio` 0.4.6, downloaded from PyPI and read directly, plus the MCP
specification revision `2026-07-28` at modelcontextprotocol.io.

Answers GitHub issue #10. Paths below are paths inside the named wheel.

## Answer

Build HTTP mode on **`fastmcp` 4.x** with its `GoogleProvider` and a shared
`client_storage` backend (Firestore is the GCP-native option). It is the only
candidate that ships a working "MCP authorization server in front of Google"
today; it rides on the official `mcp` 2.x transport, so it serves both
`2026-07-28` stateless clients and legacy handshake clients on one endpoint.
The official `mcp` 2.2.0 has the same transport but leaves the whole
authorization server for us to write. `mcp` 1.27 cannot speak `2026-07-28` at
all.

Separately, and urgently: **the published `slides-mcp` 2.2.0 is broken for
fresh installs right now**, because `mcp` 2.x removed `mcp.server.fastmcp` and
our requirement `mcp[cli]>=1.2.0` has no upper bound (see the last section).

## Comparison

| | `mcp` 1.27 (today) | `mcp` 2.2.0 (`MCPServer`) | `fastmcp` 4.0.10 |
|---|---|---|---|
| Serves `2026-07-28` stateless requests | No. Newest version it knows is `2025-11-25` | Yes | Yes (inherits the `mcp` 2.x session manager) |
| Still serves legacy `initialize` clients | Yes | Yes, same endpoint | Yes, same endpoint |
| Built-in Google provider | No | No | Yes, `GoogleProvider` |
| OAuth proxy for IdPs without DCR | No | No | Yes, `OAuthProxy` |
| Client ID Metadata Documents (CIMD) as server | No | No (client side only) | Yes, on by default (marked beta) |
| DCR endpoint | Yes, via your own provider | Yes, via your own provider | Yes, emulated locally by the proxy |
| Where auth state lives | Wherever your provider puts it | Wherever your provider puts it | `client_storage`, any `py-key-value-aio` store; default is local disk |
| Hands tools the user's Google token | You build it | You build it | Yes, `get_access_token().token` |
| `server.py` change | none | 2 import lines | 2 import lines |
| Install weight in a clean env (this repo's deps included) | n/a | 48 packages | 88 packages |

## 1. Stateless transport

**The spec.** In `2026-07-28` there is no handshake: "Every request carries
its protocol version, and the server accepts or rejects each request
independently." A *dual-era* server "selects its behavior from how the client
opens": a request with modern per-request `_meta` "is served statelessly",
an `initialize` request "selects legacy semantics", and a dual-era server
"MAY serve both eras concurrently on the same endpoint". A legacy-only
server paired with a dual-era client still works (the client falls back); a
legacy client against a modern-only server fails because "Legacy clients have
no fall-forward mechanism."
(`https://modelcontextprotocol.io/specification/2026-07-28/basic/versioning`)

**`mcp` 2.2.0.** `mcp/server/streamable_http_manager.py`,
`_handle_request`: if the `MCP-Protocol-Version` header is present and is not
one of the handshake-era versions, the request goes to
`handle_modern_request` in `mcp/server/_streamable_http_modern.py`, whose
module docstring reads "A 2026-07-28 request is a self-contained POST: no
`initialize` handshake, no `Mcp-Session-Id`, one JSON-RPC request in, one
JSON-RPC response out." This routing happens **before** the `stateless`
flag is consulted, so modern clients are always served statelessly. Legacy
requests then take `_handle_stateless_request` when `stateless=True`: a fresh
transport per request with `mcp_session_id=None`, no event store and "no
standalone channel: the legacy stateless path never opens a GET stream".
So with `stateless_http=True` neither era gets a session id or a standing
`GET` stream, which is the property the Cloud Run cost argument needs.

**`fastmcp` 4.0.10.** `fastmcp/server/http.py` subclasses the SDK's
`StreamableHTTPSessionManager` (`FastMCPStreamableHTTPSessionManager`) and
`fastmcp-slim` requires `mcp>=2.0.0,<3.0.0`, so it inherits exactly the
behaviour above. In stateless mode it also only mounts `POST` and `DELETE`
on the MCP route (`create_streamable_http_app`, `["POST", "DELETE"] if
stateless_http`). Flags: `mcp.run(transport="http", stateless_http=True,
json_response=True)` or env `FASTMCP_STATELESS_HTTP` / `FASTMCP_JSON_RESPONSE`
(`fastmcp/server/mixins/transport.py`, `fastmcp/settings.py`).

**`mcp` 1.27.0.** `mcp/types.py` sets `LATEST_PROTOCOL_VERSION =
"2025-11-25"`; `mcp/shared/version.py` lists nothing newer, and there is no
modern-request handler. Its `stateless_http=True` is the legacy sessionless
mode only. Dual-era clients would fall back to it and work, but a
modern-only client would not.

## 2. OAuth authorization server in front of Google

**The spec.** Authorization servers and clients "SHOULD support OAuth Client
ID Metadata Documents"; Dynamic Client Registration "is deprecated and
retained for backwards compatibility". Clients obtain a client id through
CIMD, pre-registration or DCR. And: "MCP servers MUST NOT accept or transit
any other tokens" than ones issued for them by their authorization server.
(`https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization`)
That last rule is why the server must be its own authorization server and
keep Google's token server-side, rather than let clients present Google
tokens directly.

**`mcp` 2.2.0.** `mcp/server/auth/provider.py` defines
`OAuthAuthorizationServerProvider`, a `Protocol` with `get_client`,
`register_client`, `authorize`, `load_authorization_code`,
`exchange_authorization_code`, `load_refresh_token`,
`exchange_refresh_token`, `load_access_token`, `revoke_token`. The SDK
supplies routes and handlers (`mcp/server/auth/routes.py`: metadata,
`/authorize`, `/token`, optional `/register`, `/revoke`) and nothing behind
them: no Google provider, no proxy, no storage. `build_metadata` never sets
`client_id_metadata_document_supported`; the only CIMD references in the
package are on the client side (`mcp/client/auth/oauth2.py`). Getting to
parity means writing the upstream redirect, callback, PKCE on both legs, code
store, token mapping, encryption at rest and refresh yourself.

**`fastmcp` 4.0.10.** `fastmcp/server/auth/providers/google.py`:
`GoogleProvider(OAuthProxy)` fixes Google's authorize and token endpoints,
verifies tokens with `GoogleTokenVerifier` (Google `tokeninfo`, `aud` pinned
to our client id, then `userinfo` for profile claims), and defaults
`extra_authorize_params` to `{"access_type": "offline", "prompt": "consent"}`
so Google returns a refresh token. `OAuthProxy`
(`fastmcp/server/auth/oauth_proxy/proxy.py`) is the generic piece:

- **DCR, emulated.** `register_client` stores a `ProxyDCRClient` locally for
  whatever the MCP client registered; every MCP client shares our one
  pre-registered Google OAuth client and its single fixed redirect URI.
- **CIMD.** `enable_cimd=True` by default; URL-shaped client ids are fetched
  with SSRF protection (`fastmcp/server/auth/cimd.py`, which calls itself
  "Beta Feature") and the metadata advertises
  `client_id_metadata_document_supported = True`.
- **Pre-registered.** `get_client` synthesizes a client when a caller sends
  the upstream Google client id directly without registering.
- **Token swap.** The client gets a proxy-signed JWT, never the Google token.
  `load_access_token` verifies the JWT, looks up the upstream token by `jti`,
  validates it with Google, refreshes it transparently if expired, and
  returns an `AccessToken` whose `.token` is the user's Google access token.
- **Consent page.** `require_authorization_consent=True` by default shows a
  proxy consent page before Google's.

What it does **not** do: restrict sign-in to one Workspace domain. Nothing in
`GoogleProvider` or `GoogleTokenVerifier` checks a hosted-domain value.
Passing `hd` through `extra_authorize_params` only pre-filters Google's
account chooser; enforcement needs a check in a custom verifier, which is
left to the auth architecture ticket (#13).

## 3. State across instances and restarts

**`mcp` 1.27 / 2.2.0.** Whatever your provider implementation stores; the SDK
keeps nothing of its own for auth. The stateful transport keeps sessions in
an in-process dict (`_server_instances`), which is why stateless mode is
required for more than one instance.

**`fastmcp` 4.0.10.** All proxy state goes through one `AsyncKeyValue`
(`client_storage`), split into collections (`proxy.py`, `__init__`):

| Collection | Holds | Lifetime in code |
|---|---|---|
| `mcp-oauth-proxy-clients` | DCR client records | no TTL |
| `mcp-oauth-transactions` | in-flight authorize requests | 15 minutes |
| `mcp-consent-csrf-tokens` | consent page CSRF tokens | short |
| `mcp-authorization-codes` | our auth codes plus PKCE challenge | 5 minutes |
| `mcp-upstream-tokens` | Google access and refresh tokens | refresh token lifetime |
| `mcp-jti-mappings` | our token id to upstream token id | our token lifetime |
| `mcp-refresh-tokens` | our refresh token metadata, by hash | refresh lifetime |

The **default** when `client_storage` is omitted is a `FileTreeStore` under
the FastMCP home directory wrapped in `FernetEncryptionWrapper`. On Cloud Run
that is per-instance and lost on every restart or scale-to-zero, so every user
would re-consent after each cold start and a callback landing on a different
instance than its `/authorize` would fail. A custom `client_storage` is **not**
encrypted automatically; wrap it in `FernetEncryptionWrapper` yourself.

Pluggable stores (`py-key-value-aio` 0.4.6 `key_value/aio/stores/`): `firestore`,
`postgresql`, `redis`, `valkey`, `dynamodb`, `mongodb`, `s3`, `disk`,
`filetree`, `memory` and others. **Firestore** fits scale-to-zero with no
standing cost; a Redis-compatible store would need an always-on instance.

Other cross-instance points read in the code:

- The JWT signing key must be the same on every instance. If
  `jwt_signing_key` is not passed it is derived from the Google client
  secret, which is stable as long as every instance gets the same secret.
- The refresh lock is in-process (`_get_refresh_lock`); on a refresh failure
  it re-reads the store in case another instance already refreshed.
- The CIMD document cache is in-process but re-fetchable, so losing it costs
  one fetch, not a sign-in.

## 4. Migration cost

`server.py` uses four things from the SDK: `FastMCP(...)`, the
`@mcp.tool()` decorator on 9 tools, `Image`, and `mcp.run()`. I swapped only
the two import lines in a scratch copy and listed tools in-process under each
library; all 9 tools loaded in both.

| Target | Import change |
|---|---|
| `mcp` 2.2.0 | `from mcp.server.mcpserver import MCPServer as FastMCP`; `from mcp.server.mcpserver.utilities.types import Image` |
| `fastmcp` 4.0.10 | `from fastmcp import FastMCP`; `from fastmcp.utilities.types import Image` |

One behaviour difference agents will see: `fastmcp` parses each docstring and
moves the `Args:` section into per-parameter `description` fields in the
input schema, so the tool description gets much shorter (for `read_slides`,
187 characters under `fastmcp` against 2143 under `mcp` 2.2.0) while the
per-parameter text appears in the schema. Nothing is lost, but because tool
docstrings are what agents read, the migration should snapshot the
`tools/list` output and review it.

**stdio.** `mcp.run()` with no argument uses `fastmcp.settings.transport`,
stdio by default, so the stdio path and `token.json` flow stay as they are.
`fastmcp` prints a startup banner to stderr on stdio; it can be turned off
with `show_banner=False`. The cost to stdio users is install weight: a clean
environment with this repo's dependencies resolved 88 packages with `fastmcp`
against 48 with `mcp` 2.2.0. `fastmcp-slim[server]` is the same server
without the client extras.

**HTTP mode sketch** (for #13 and #15, not a design):

```python
auth = GoogleProvider(
    client_id=..., client_secret=..., base_url="<SERVICE_URL>",
    required_scopes=["openid", "email", "https://www.googleapis.com/auth/presentations", ...],
    client_storage=FernetEncryptionWrapper(key_value=FirestoreStore(...), fernet=...),
)
mcp = FastMCP("slides-mcp", auth=auth)   # auth only when HTTP mode is on
mcp.run(transport="http", stateless_http=True, json_response=True)
# inside a tool: google_token = get_access_token().token
```

## Rejected alternatives

- **Stay on `mcp` 1.27.** Cannot serve `2026-07-28` requests; modern-only
  clients would fail and dual-era clients would fall back to the legacy
  transport. It also has no Google or proxy support, and the 1.x line gets
  only critical fixes per the `mcp` 2.x README. The auth work would be the same
  as the next option, on a line that is winding down.
- **Official `mcp` 2.2.0 with a hand-written provider.** The transport is
  exactly right and the dependency is lighter, but the SDK gives only the
  provider interface. Writing a correct Google-proxying authorization server
  (DCR emulation, CIMD, consent, PKCE on both legs, encrypted token storage,
  transparent refresh, cross-instance safety) reproduces what `OAuthProxy`
  already is: about 2,900 lines in `proxy.py` alone. Worth reconsidering only if
  the `fastmcp` dependency itself becomes the problem.
- **Point clients straight at Google as the authorization server.** Google
  has neither DCR nor CIMD, and the spec says MCP servers "MUST NOT accept or
  transit any other tokens" than their own authorization server's; a Google
  token presented directly would also be sent on to the Slides API. The proxy
  pattern exists for this reason.

## Found on the way: published release broken for fresh installs

`pyproject.toml` requires `mcp[cli]>=1.2.0`. The `mcp` 2.x README says
"`pip install mcp` now installs 2.x, keep a `<2` upper bound". Running the
published package in a clean environment:

```text
$ uvx --from "slides-mcp==2.2.0" python -c "from mcp.server.fastmcp import FastMCP"
ModuleNotFoundError: No module named 'mcp.server.fastmcp'. This is mcp 2.x,
where FastMCP was renamed to MCPServer ...
```

Any `uvx slides-mcp` that resolves fresh today gets `mcp` 2.2.0 and fails at
import. A `mcp[cli]>=1.2.0,<2` patch release fixes stdio users now,
independent of the HTTP-mode choice.
