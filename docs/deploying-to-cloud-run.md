---
title: Deploying the shared server to Cloud Run
summary: the Google Cloud setup a deployer does once before running slides-mcp as a shared remote server (Firestore database, service account, client secret, settings, Google OAuth client), how a release tag is built and deployed with gcloud run deploy --source, how to check it is up, and the shared thumbnail quota
verified: 2026-10-03
---

# Deploying the shared server to Cloud Run

Every command on this page was run in a brand-new project on 2026-10-01, up
to a deployed service answering sign-in requests. Two parts are still open:
the Google OAuth console steps were written from Google's docs and not yet
clicked through, and instance sizing waits for measurements. Steps 9 and 10
of the one-time setup were added later: the TTL command was checked against
`gcloud`'s help, and with step 10's grants in place an image was uploaded,
fetched anonymously through a URL signed as the service account, and deleted,
on 2026-10-03.

Placeholders used below:

| placeholder | what it is |
|---|---|
| `<GCP_PROJECT>` | the Google Cloud project the service runs in |
| `<REGION>` | the Cloud Run region, e.g. `us-central1` |
| `<PROJECT_NUMBER>` | the project's number, from `gcloud projects describe <GCP_PROJECT> --format='value(projectNumber)'` |
| `<SERVICE_ACCOUNT>` | the Cloud Run service's runtime identity, `slides-mcp-run@<GCP_PROJECT>.iam.gserviceaccount.com` |
| `<SERVICE_URL>` | the service's URL, `https://slides-mcp-<PROJECT_NUMBER>.<REGION>.run.app` |
| `<CLIENT_ID>` | the Google OAuth Web client's ID |
| `<CLIENT_SECRET>` | that client's secret; it goes into Secret Manager and nowhere else |
| `<DEPLOYER_EMAIL>` | the Google account of the person who runs `gcloud run deploy` |
| `<N>` | the Secret Manager version number of the client secret |
| `<IMAGE_BUCKET>` | an optional GCS bucket for temporary images, used only by slides-mcp |

## Why a separate Firestore database

The remote server keeps its **auth store** (registered clients, in-flight
sign-ins and every caller's Google access and refresh token) in Firestore, so
that sign-ins survive scale-to-zero, redeploys and several instances.

It gets **its own named database**, never the project's `(default)` one:

- Firestore permissions for a service account apply to a whole database, not
  to a collection, and Firestore security rules do not apply to server code. A
  separate database is the only way to stop slides-mcp from reading or writing
  another app's collections, and to stop it being read by that app.
- The stored values are **not encrypted by slides-mcp**, only by Google's
  default encryption at rest. Anyone who can read the database can read every
  teammate's Google refresh token. IAM on this one database is the only
  protection.

Inside it, every collection name starts with `slides-mcp__`, for example
`slides-mcp__mcp-upstream-tokens`. There are seven of them, created on first
write; you never create collections by hand.

An eighth, `slides-mcp__plans`, holds deck-script dry runs for an hour so a
plan made on one instance can be applied on another. Each plan is tied to
one deck and one signed-in caller.

## One-time setup

1. **Enable the APIs.**
   ```
   gcloud services enable \
     firestore.googleapis.com \
     secretmanager.googleapis.com \
     run.googleapis.com \
     cloudbuild.googleapis.com \
     artifactregistry.googleapis.com \
     iam.googleapis.com \
     slides.googleapis.com \
     drive.googleapis.com \
     --project=<GCP_PROJECT>
   ```
   Cloud Build and Artifact Registry are what `gcloud run deploy --source`
   builds and stores the image with; IAM is needed to create the service
   account. **Slides and Drive must be on in the project that owns the OAuth
   client**: every caller's Slides call is charged to that project, and
   without them every tool call fails with "API has not been used in
   project".

2. **Create the auth database** in the same region as the service, with
   delete protection on. Deleting it signs every teammate out.
   ```
   gcloud firestore databases create \
     --project=<GCP_PROJECT> \
     --database=slides-mcp-auth \
     --location=<REGION> \
     --edition=standard \
     --type=firestore-native \
     --delete-protection
   ```
   Database IDs are lowercase letters, digits and hyphens, 4 to 63 characters,
   starting with a letter. A deleted ID cannot be reused for about 5 minutes.

3. **Create a dedicated service account** for the service to run as. Never use
   the default compute service account: it holds Editor on the project, which
   reaches every database.
   ```
   gcloud iam service-accounts create slides-mcp-run \
     --project=<GCP_PROJECT> \
     --display-name='slides-mcp Cloud Run service'
   ```

4. **Grant the service account access to that database only.** The condition
   is what keeps it out of every other database in the project; a grant without
   it reaches `(default)` too.
   ```
   gcloud projects add-iam-policy-binding <GCP_PROJECT> \
     --member='serviceAccount:<SERVICE_ACCOUNT>' \
     --role='roles/datastore.user' \
     --condition='expression=resource.name=="projects/<GCP_PROJECT>/databases/slides-mcp-auth",title=slides-mcp-auth-only'
   ```

5. **Review who else can read the database.** Project-wide Owner and Editor,
   and any project-level `roles/datastore.*` grant without a condition, can read
   the stored refresh tokens. Keep that list short.
   ```
   gcloud projects get-iam-policy <GCP_PROJECT> \
     --flatten='bindings[].members' \
     --filter='bindings.role:roles/owner OR bindings.role:roles/editor OR bindings.role~datastore' \
     --format='table(bindings.role,bindings.members,bindings.condition.title)'
   ```
   Enabling Cloud Build and Cloud Run in a new project creates
   `<PROJECT_NUMBER>-compute@developer.gserviceaccount.com` **with Editor**, so
   it can read the auth database too. `--source` deploys build as that account
   in new projects, so removing its Editor role breaks the build unless you
   first give Cloud Build a narrower account.

6. **Set the database name on the service** as
   `SLIDES_MCP_FIRESTORE_DATABASE=slides-mcp-auth`. HTTP mode refuses to start
   without it, on purpose: the storage library would otherwise fall back to
   `(default)` and write its seven collections next to whatever else lives
   there. `(default)` itself is rejected too.

7. **Store the Google OAuth client secret** in Secret Manager, and let the
   service account read that one secret, not every secret in the project.
   ```
   printf '%s' '<CLIENT_SECRET>' | gcloud secrets create slides-mcp-google-client-secret \
     --project=<GCP_PROJECT> \
     --replication-policy=automatic \
     --data-file=-

   gcloud secrets add-iam-policy-binding slides-mcp-google-client-secret \
     --project=<GCP_PROJECT> \
     --member='serviceAccount:<SERVICE_ACCOUNT>' \
     --role='roles/secretmanager.secretAccessor'
   ```
   The first version is `1`; `gcloud secrets versions list` shows the rest.

8. **Let the person deploying act as the service account.** Deploying a
   service that runs as `<SERVICE_ACCOUNT>` needs `roles/iam.serviceAccountUser`
   on it, on top of permission to deploy Cloud Run and run Cloud Build. A
   project Owner already has it; skip this step if that is you.
   ```
   gcloud iam service-accounts add-iam-policy-binding <SERVICE_ACCOUNT> \
     --project=<GCP_PROJECT> \
     --member='user:<DEPLOYER_EMAIL>' \
     --role='roles/iam.serviceAccountUser'
   ```

9. **Let dry-run plans expire on their own.** A deck-script dry run is
   stored for an hour in the auth database, collection `slides-mcp__plans`,
   so another instance can apply it. slides-mcp ignores a plan once its
   `expires_at` time has passed; this TTL policy makes Firestore delete it too.
   ```
   gcloud firestore fields ttls update expires_at \
     --project=<GCP_PROJECT> \
     --database=slides-mcp-auth \
     --collection-group=slides-mcp__plans \
     --enable-ttl
   ```
   Firestore deletes expired documents within about a day, not at once; that
   is fine, because reads already treat them as gone.

10. **Optional: an image bucket**, for tools that hand Google an image the
    server made (Google fetches inserted images from a URL). Without it the
    server still starts and plans still work; only those image calls fail,
    with an error naming `SLIDES_MCP_IMAGE_BUCKET`. Each image is uploaded
    under a random name, given to Google as a signed URL valid for 5 minutes,
    and deleted straight after the write. A lifecycle rule deleting objects
    older than 1 day (the smallest age GCS allows) catches anything missed.
    ```
    gcloud storage buckets create gs://<IMAGE_BUCKET> \
      --project=<GCP_PROJECT> \
      --location=<REGION> \
      --uniform-bucket-level-access \
      --public-access-prevention

    printf '%s' '{"rule":[{"action":{"type":"Delete"},"condition":{"age":1}}]}' \
      > lifecycle.json
    gcloud storage buckets update gs://<IMAGE_BUCKET> --lifecycle-file=lifecycle.json
    ```
    Then grant the service account object access on that bucket only, and
    permission to sign URLs as itself. Cloud Run's credentials hold no private
    key, so signing goes through the IAM Credentials API's `signBlob` call,
    which needs that API on and the Token Creator role on the account itself.
    ```
    gcloud services enable iamcredentials.googleapis.com --project=<GCP_PROJECT>

    gcloud storage buckets add-iam-policy-binding gs://<IMAGE_BUCKET> \
      --member='serviceAccount:<SERVICE_ACCOUNT>' \
      --role='roles/storage.objectAdmin'

    gcloud iam service-accounts add-iam-policy-binding <SERVICE_ACCOUNT> \
      --project=<GCP_PROJECT> \
      --member='serviceAccount:<SERVICE_ACCOUNT>' \
      --role='roles/iam.serviceAccountTokenCreator'
    ```
    Set `SLIDES_MCP_IMAGE_BUCKET=<IMAGE_BUCKET>` on the service, without the
    `gs://`. The bucket stays private: a signed URL is the only way to read an
    object, and it is unguessable and short-lived.

## The Google OAuth consent screen and Web client

These exist only in the Google Cloud console, under **Google Auth Platform**,
in the project above. Not yet clicked through for this guide; correct it
if a screen differs.

1. **Branding**: app name (for example `slides-mcp`), support email,
   developer contact email.
2. **Audience**: **Internal**. Only accounts in your Google Workspace
   organization can sign in. This is the only sign-in restriction there is;
   slides-mcp does not check domains.
3. **Data access**: add `openid`, `.../auth/userinfo.email`,
   `.../auth/presentations` and `.../auth/drive.readonly`.
4. **Clients → Create client**: type **Web application**. Under **Authorized
   redirect URIs** add exactly `<SERVICE_URL>/auth/callback`. Add no
   JavaScript origins.
5. Copy the client ID into `SLIDES_MCP_GOOGLE_CLIENT_ID`, and put the secret
   into Secret Manager (step 7 above) without writing it anywhere else.

## Settings

HTTP mode starts with `slides-mcp serve-http` and reads only environment
variables. It checks all of them before opening the port; if any is missing or
invalid it prints one line per problem and exits with code `2`.

| setting | required | default | on Cloud Run |
|---|---|---|---|
| `SLIDES_MCP_GOOGLE_CLIENT_ID` | yes | none | `--set-env-vars` |
| `SLIDES_MCP_GOOGLE_CLIENT_SECRET` | yes | none | Secret Manager via `--set-secrets` |
| `SLIDES_MCP_BASE_URL` | yes | none | `--set-env-vars`, the service's `https://` URL |
| `SLIDES_MCP_FIRESTORE_DATABASE` | yes | none | `--set-env-vars` |
| `SLIDES_MCP_IMAGE_BUCKET` | no | none | `--set-env-vars`, the bucket name from step 10 |
| `PORT` | no | `8080` | set by Cloud Run |

To run HTTP mode locally, point it at the Firestore emulator with
`FIRESTORE_EMULATOR_HOST`; there is no in-memory store. A plain
`pip install slides-mcp` lacks the Firestore library; HTTP mode needs
`slides-mcp[http]`, which the `Dockerfile` installs.

## Building and deploying a release

There is no published image and no deploy job. Each deployer builds a release
tag into their own project with one command; Cloud Build reads the repo's
`Dockerfile` and stores the image in the project's Artifact Registry.

**Deploy from a fresh clone of the tag, never a working directory.**
`--source .` uploads the directory. `.gcloudignore` and `.dockerignore` keep
`token.json`, `creds.json`, `tmp/`, `.venv/` and `.git/` out of the upload,
but a clean clone is the only thing that guarantees none of your own files
end up in the image.

```
git clone --depth 1 --branch vX.Y.Z https://github.com/luutuankiet/slides-mcp.git
cd slides-mcp
```

The image installs the dependencies pinned in `uv.lock`, not the PyPI
release, which would ignore the lockfile. It runs `slides-mcp serve-http` on
`python:3.12-slim-bookworm` as a non-root user.

**Work out `<SERVICE_URL>` before the first deploy.** Cloud Run's URL is
`https://<SERVICE>-<PROJECT_NUMBER>.<REGION>.run.app`, so with the service
named `slides-mcp` it is known in advance. Use it for both
`SLIDES_MCP_BASE_URL` and the Google Web client's authorized redirect URI,
`<SERVICE_URL>/auth/callback`, and the first deploy works without a second
pass.

```
gcloud run deploy slides-mcp \
  --source . \
  --project=<GCP_PROJECT> \
  --region=<REGION> \
  --service-account=<SERVICE_ACCOUNT> \
  --allow-unauthenticated \
  --min-instances=0 \
  --set-env-vars=SLIDES_MCP_GOOGLE_CLIENT_ID=<CLIENT_ID>,SLIDES_MCP_BASE_URL=<SERVICE_URL>,SLIDES_MCP_FIRESTORE_DATABASE=slides-mcp-auth \
  --set-secrets=SLIDES_MCP_GOOGLE_CLIENT_SECRET=slides-mcp-google-client-secret:<N>
```

- `--allow-unauthenticated` is intended. MCP clients present a slides-mcp
  server token, not a Google identity token, so Cloud Run's own caller check
  has to be off; who may sign in is set on the Google OAuth client. If an
  organization policy refuses the `allUsers` grant, use
  `--no-invoker-iam-check` instead.
- **Pin the secret to a version number, never `latest`.** The server-token
  signing key is derived from the secret, and `latest` is read when each
  instance starts, so instances started on either side of a rotation would
  reject each other's tokens. To rotate, add a version and redeploy with the
  new number.
- Memory, CPU, concurrency, request timeout and execution environment are not
  set yet; they wait for measurements of the running service.

## Checking it is up

No Google sign-in is needed for these. Each checks one layer:

```
curl -s -o /dev/null -w '%{http_code}\n' -X POST <SERVICE_URL>/mcp \
  -H 'content-type: application/json' -d '{}'
curl -s <SERVICE_URL>/.well-known/oauth-protected-resource/mcp
curl -s -o /dev/null -w '%{http_code}\n' -X POST <SERVICE_URL>/register \
  -H 'content-type: application/json' \
  -d '{"client_name":"smoke","redirect_uris":["http://localhost:33418/callback"],"token_endpoint_auth_method":"none"}'
```

| check | expect | proves |
|---|---|---|
| `POST /mcp` with no token | `401` | the service started, so every setting passed the startup check |
| protected resource metadata | JSON naming `<SERVICE_URL>/mcp` | `SLIDES_MCP_BASE_URL` is right |
| `POST /register` | `201` | the service account can write the auth database |

The startup log carries one `UserWarning` that a configured store "is
unstable and may change"; that is the storage library labelling its Firestore
backend, and a reason the image installs from `uv.lock`.

When those pass but signing in fails, Google's error page names the setting:

| Google says | fix |
|---|---|
| `Error 401: invalid_client` ("The OAuth client was not found") | `SLIDES_MCP_GOOGLE_CLIENT_ID` is not a client in this project |
| `Error 400: redirect_uri_mismatch` | the Web client has no authorized redirect URI `<SERVICE_URL>/auth/callback`; the console form leaves that list empty unless you add it |

## What signs everyone out

| event | result |
|---|---|
| redeploy, new revision, scale to zero, more instances | everyone stays signed in |
| rotating the Google OAuth client secret | everyone signs in again: the server-token signing key is derived from it |
| deleting the auth database | everyone signs in again (blocked by delete protection) |
| Google refusing a refresh (revoked, suspended, unused for 6 months) | that one person signs in again |

## Thumbnail quota is shared by the whole team

Google allows **300 slide-thumbnail requests a minute per project** and 60 a
minute per user. Every caller's thumbnails count against the project that owns
the OAuth client, and they come from two places: `render_thumbnail`, and the
receipt every write tool attaches (up to 3 thumbnails per write by default).

A thumbnail refused for quota (`429`) does not fail the write. The reply
carries a warning, the write stays applied, and slides-mcp does not retry. If
a busy team hits the project limit, have agents pass `receipt="off"` on bulk
edits.

## Cost

Firestore's free tier "applies to only one Firestore database per project",
and in most projects `(default)` already holds it, so this database is billed
from its first read. At 10 people making about 300 tool calls a day, each call
reading the store twice, that is roughly 180k reads a month plus a few thousand
writes: cents a month.

Expired records are never deleted: the storage library saves expiry as text,
which Firestore's automatic deletion cannot use. They are ignored when read,
and at this size they add up to a few thousand small documents a year.
Dry-run plans are the exception: their expiry is a real timestamp, so the
TTL policy from step 9 deletes them.

## Still to write

- Instance sizing flags for `gcloud run deploy`, once they can be measured.
