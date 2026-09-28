---
title: Google OAuth for a single-Workspace-domain app calling Slides
summary: what Google enforces (and does not) for an Internal OAuth app that signs in one Workspace domain and calls Slides as each user
verified: 2026-09-28
---

# Google OAuth for a single-Workspace-domain app calling Slides

Research for the remote Cloud Run deployment: a web OAuth client, owned by a GCP
project inside the same Google Workspace organization as its users, that signs
in only `<WORKSPACE_DOMAIN>` accounts and calls the Slides API as each user.
Every claim cites Google's own documentation, read on 2026-09-28. Placeholders:
`<WORKSPACE_DOMAIN>`, `<GCP_PROJECT>`, `<SERVICE_URL>`.

## 1. Domain restriction: three layers, two worth relying on

| layer | what it enforces | trust it? |
|---|---|---|
| Consent screen **Audience = Internal** | Google refuses authorization for any account outside the project's parent organization, with an `org_internal` error | Yes: enforced by Google, cannot be bypassed by the client |
| `hd=<WORKSPACE_DOMAIN>` authorization parameter | Only optimizes the account chooser UI | No: a hint, the request can be edited |
| Server-side check of the ID token `hd` claim | The signed ID token names the user's hosted domain | Yes: this is the check Google tells you to make |

- **Internal.** "Projects associated with a Google Cloud Organization can
  configure Internal users to limit authorization requests to members of the
  organization." "An `org_internal` authorization error is displayed when
  authorization is requested from users outside the Google Cloud project's
  parent." Source: Manage App Audience,
  `https://support.google.com/cloud/answer/15549945`.
- **The boundary is the organization, not a domain.** Internal admits every
  account in the Workspace / Cloud Identity organization, which includes any
  secondary domains the organization owns. If the org holds more than one
  domain and only `<WORKSPACE_DOMAIN>` should get in, only the `hd` claim check
  narrows it.
- **`hd` parameter vs claim.** "Don't rely on this UI optimization to control who
  can access your app, as client-side requests can be modified. Be sure to
  validate that the returned ID token has an `hd` claim value that matches what
  you expect." The claim is "Provided only if the user belongs to a Google Cloud
  organization"; "The absence of this claim indicates that the account does not
  belong to a Google hosted domain." Source: OpenID Connect,
  `https://developers.google.com/identity/openid-connect/openid-connect`.
  Getting an ID token requires the `openid` scope (add `email` to read the
  address for logs).

> Rely on **Internal** (Google-enforced org boundary) plus a **server-side
> `hd == <WORKSPACE_DOMAIN>` check on a verified ID token** (defence in depth,
> and the only thing that narrows to one domain inside a multi-domain org). Send
> the `hd` parameter for UX only.

## 2. Scopes and verification

### Classification

From the Slides scope table (`https://developers.google.com/workspace/slides/api/scopes`)
and the Drive scope guide
(`https://developers.google.com/workspace/drive/api/guides/api-specific-auth`):

| scope | class |
|---|---|
| `presentations` | Sensitive |
| `presentations.readonly` | Sensitive |
| `drive.file` | Non-sensitive |
| `drive.readonly` | **Restricted** |
| `drive` | **Restricted** |
| `openid`, `email`, `profile` | Non-sensitive (sign-in) |

For a Restricted scope Google says "If you store restricted scope data on
servers (or transmit), then you must go through a security assessment" (Drive
guide), unless the app qualifies for an exception.

### Internal apps are exempt from verification

Restricted scope verification lists exceptions; one is **Internal use only**:
"the app is used only by people in your Google Workspace or Cloud Identity
organization. The project must be owned by the organization, and its OAuth
consent screen needs to be configured for an Internal user type. In this case,
your app might need approval from an organization administrator." Source:
`https://developers.google.com/identity/protocols/oauth2/production-readiness/restricted-scope-verification`.
The companion page adds: "Your app will not be subject to the unverified app
screen or the 100-user cap if it's designated as internal-only." Source: When is
verification not needed, `https://support.google.com/cloud/answer/13464323`.

So neither `presentations` (sensitive) nor `drive.readonly` (restricted) forces
verification or a security assessment for an Internal app. What restricted still
costs is **admin exposure**: the Audience page warns that "User authorization of
scopes associated with restricted Google Workspace services, including
high-risk Gmail and Drive scopes, might require additional configuration by your
organization's administrators" (section 5).

### What the server actually needs

Every Google API call in `src/slides_mcp/slides_api.py` is a Slides v1 call:
`presentations.get`, `presentations.pages.get`,
`presentations.pages.getThumbnail`, `presentations.batchUpdate`. No code path
calls the Drive API.

- **`getThumbnail`** accepts any of `drive`, `drive.file`, `drive.readonly`,
  `presentations`, `presentations.readonly`
  (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/getThumbnail`).
- **The thumbnail `contentUrl` fetch needs no scope at all.** "This URL is tagged
  with the account of the requester. Anyone with the URL effectively accesses
  the image as the original requester." Default lifetime 30 minutes (same page).
  `get_thumbnail_bytes` already fetches it with a plain unauthenticated
  `urllib` GET. Treat the URL as a bearer credential: never log it or return it
  to the client.
- **Where Drive scope would matter**: a few `batchUpdate` request kinds that the
  raw passthrough could carry. `createVideo` from Drive needs `drive`,
  `drive.readonly` or `drive.file`; `createSheetsChart` needs a spreadsheets or
  drive scope; `refreshSheetsChart` and `replaceAllShapesWithSheetsChart` need
  `spreadsheets.readonly`, `spreadsheets`, `drive.readonly` or `drive`
  (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations/request`).
  Without those scopes these requests fail with a permission error; nothing
  else breaks.

> **Minimal scope set: `openid email https://www.googleapis.com/auth/presentations`.**
> `drive.readonly` is unused by the code, is Restricted, and is the one scope
> most likely to be blocked by a Workspace admin's API controls. If Sheets-chart
> requests matter, add `spreadsheets.readonly` (Sensitive), not a Drive scope.

## 3. Refresh tokens

The general rules from `https://developers.google.com/identity/protocols/oauth2`
("Refresh token expiration"). A refresh token stops working when:

1. the user revokes the app's access;
2. it has not been used for six months;
3. the user changed passwords **and the token contains Gmail scopes** (not ours);
4. the account exceeds the live-token cap: "a limit of 100 refresh tokens per
   Google Account per OAuth 2.0 client ID", and minting another "automatically
   invalidates the oldest refresh token without warning";
5. the user granted time-based access and it expired;
6. an admin set any of the requested services to Restricted (and has not
   trusted the app);
7. for Google Cloud scopes only, the admin's Cloud session length was exceeded.

Notes for an Internal app in production:

- **No 7-day expiry.** The 7-day expiry of refresh tokens applies to the
  *Testing* publishing status, whose users are an explicit test-user list
  (Manage App Audience page). An Internal app is not on that path, so its
  tokens follow only the list above.
- **Password change does not revoke Slides tokens.** The Workspace admin page
  scopes automatic revocation to "applications that use mail scopes"
  (`https://knowledge.workspace.google.com/admin/apps/automatic-oauth-20-token-revocation-upon-password-change`).
- **Cloud session length does not apply.** It covers the Cloud console, `gcloud`
  and "applications ... that require user authorization for Google Cloud
  scopes" (`https://knowledge.workspace.google.com/admin/security/set-session-length-for-google-cloud-services`).
  `presentations` is not a Google Cloud scope.
- **User leaves the domain.** "Suspending a user resets the user's sign-in
  cookies and OAuth tokens"
  (`https://knowledge.workspace.google.com/admin/support/troubleshooting/identify-and-secure-compromised-accounts`).
  Deleting the account removes it outright. An admin can also revoke one app's
  tokens per user from the user's security settings, or revoke/block via API
  controls, which "renders associated refresh tokens invalid"
  (`https://developers.google.com/identity/protocols/oauth2/production-readiness/google-workspace`).
- **A refresh token is issued once.** Only with `access_type=offline`, and "only
  returned on the first authorization"; `prompt=consent` forces a new one
  (`https://developers.google.com/identity/protocols/oauth2/web-server`). Each
  forced re-consent mints a new token toward the 100 cap, so a server that
  forces consent on every login quietly churns its oldest tokens.
- **Granular consent.** Users can deselect individual scopes on the consent
  screen; the app "must verify which scopes were actually granted" (same page).

## 4. Redirect URIs for a Cloud Run callback

Rules for a web-application client
(`https://developers.google.com/identity/protocols/oauth2/web-server`, and
Manage OAuth Clients `https://support.google.com/cloud/answer/15549257`):

- HTTPS only (localhost exempt); no raw IP host; the host's TLD must be on the
  public suffix list; not `googleusercontent.com`; no URL shorteners; no
  userinfo, no `/..` path traversal, no fragment, no wildcards, no open
  redirects.
- The `redirect_uri` sent must exactly match a registered one, else
  `redirect_uri_mismatch`. "It may take 5 minutes to a few hours for changes
  made to these settings to take effect."
- **`run.app` is accepted.** `.app` is a public-suffix TLD, and the public
  suffix list carries `*.run.app` in its private section, so a Cloud Run host
  like `<service>-<hash>.<REGION>.run.app` is its own top private domain.
- **Authorized domains.** "All domains used in your project, whether in the
  branding page or client configuration pages must be pre-registered" as
  authorized domains, added before the redirect URIs
  (`https://support.google.com/cloud/answer/15549049`). Because of the
  `*.run.app` entry, the authorized domain to register is the full service host
  of `<SERVICE_URL>`. Domain ownership verification in Search Console is part
  of app verification, which an Internal app skips; confirm in the console that
  the host is accepted on first setup.
- A Cloud Run service can answer on more than one `run.app` URL. Register the
  exact URL the server builds its callback from (`<SERVICE_URL>/<callback path>`),
  and configure the server with that URL rather than deriving it from the
  request `Host` header.

## 5. Workspace admin controls

From "Control which apps access Google Workspace data"
(`https://knowledge.workspace.google.com/admin/apps/control-which-apps-access-google-workspace-data`)
and "Control which third-party & internal apps access Google Workspace data"
(`https://knowledge.workspace.google.com/admin/apps/control-which-third-party-and-internal-apps-access-google-workspace-data`):

- **Defaults need no admin action.** Services are Unrestricted unless an admin
  changes them, and "Allow users to access any third-party apps (default)" is
  the default for unconfigured apps. With defaults, users can consent to the
  Internal app themselves.
- **Restricted services block untrusted apps.** "Restricted: Only internal and
  third-party apps configured with a Trusted or Specific Google data access
  setting can access data." Switching a service to Restricted means "any
  previously installed apps that you haven't trusted stop working, and tokens
  are revoked." For Drive, admins can separately restrict high-risk scopes
  (that is where `drive.readonly` is exposed).
- **Trusting internal apps.** Admin console, Security, Access and data control,
  API controls, Settings, Internal apps: "Trust internal apps" lets "internal
  apps built by your organization ... access restricted Google Workspace APIs."
  Alternatively trust just this OAuth client ID under Manage Third-Party App
  Access.
- The docs do not say whether the "Unconfigured third-party apps" block setting
  also catches internal apps. Trusting the client ID explicitly removes the
  question.

## 6. Creating the client and consent screen: console only

- The IAP OAuth Admin API "was shut down on March 19, 2026. You can no longer
  create or manage OAuth brands or clients programmatically using this API"
  (`https://docs.cloud.google.com/iap/docs/programmatic-oauth-clients`). Its
  clients were locked to IAP redirect URIs anyway, so it never fit this use.
- The migration guide says new "OAuth brand and client configurations" are
  created and managed "using the Google Cloud console"
  (`https://docs.cloud.google.com/iap/docs/deprecations/migrate-oauth-client`).
  The Terraform it mentions (`google_iap_settings`) only wires an existing
  client ID into IAP; it does not create one.
- Google Auth Platform (console) has the pages: Branding, **Audience** (set
  Internal), **Clients** (Create client, type Web application), **Data Access**
  (declare scopes). Source: `https://support.google.com/cloud/answer/15544987`.
- The client secret is shown in full only at creation; afterwards the console
  shows the last four characters. Google recommends storing it in Secret
  Manager, which is scriptable. Clients unused for six months are deleted
  automatically (`https://support.google.com/cloud/answer/15549257`).

> Creating the consent screen and web client is a one-time **manual console
> step**. Everything after it (Secret Manager secret, Cloud Run env wiring) can
> be scripted.

## Recommendations

1. **Domain restriction**: Audience = Internal, plus verify the ID token
   signature, `aud`, `iss`, and `hd == <WORKSPACE_DOMAIN>` server-side. Send `hd`
   in the auth request for account-chooser UX only.
2. **Scopes**: `openid`, `email`, `https://www.googleapis.com/auth/presentations`.
   Drop `drive.readonly` for the remote deployment. Add `spreadsheets.readonly`
   only if Sheets-chart `batchUpdate` requests are wanted. Check granted scopes
   after the callback.
3. **Refresh tokens**: request `access_type=offline`; send `prompt=consent` only
   when no stored refresh token exists for the user. Expect tokens to live until
   revoked, unused for six months, the user is suspended or deleted, or an admin
   restricts or blocks the app. Handle `invalid_grant` on refresh by sending the
   user back through sign-in. Never mint tokens per request (100-token cap).
4. **Redirect URI**: one fixed `https://<SERVICE_URL>/<callback path>` registered
   on a Web application client; the `run.app` service host registered as an
   authorized domain first; allow minutes to hours for changes to apply.
5. **Thumbnails**: keep fetching `contentUrl` server-side and returning bytes;
   never expose or log the URL.

### Setup checklist

- [ ] Confirm `<GCP_PROJECT>` sits under the Workspace organization (Internal is
      only offered to org-owned projects).
- [ ] Google Auth Platform, Branding: app name, support email, add the
      `run.app` service host of `<SERVICE_URL>` under authorized domains.
- [ ] Audience: set **Internal**.
- [ ] Data Access: declare `openid`, `email`, `presentations`.
- [ ] Clients: create a **Web application** client with redirect URI
      `<SERVICE_URL>/<callback path>`; copy the secret immediately into Secret
      Manager.
- [ ] Workspace admin: in API controls, check whether Drive/Docs (Slides) is set
      to Restricted or unconfigured third-party apps are blocked. If either is,
      trust the OAuth client ID (or tick "Trust internal apps"). With defaults,
      no action is needed, but trusting the client ID up front is cheap
      insurance.
- [ ] Offboarding: suspending or deleting a user revokes their Google tokens;
      the server should also delete its stored copy.
