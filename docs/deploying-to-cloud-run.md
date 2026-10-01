---
title: Deploying the shared server to Cloud Run (draft)
summary: the Google Cloud setup a deployer does once before running slides-mcp as a shared remote server (Firestore database, service account, client secret, settings), and how a release tag is built and deployed with gcloud run deploy --source; draft until HTTP mode ships
verified: 2026-09-28
---

# Deploying the shared server to Cloud Run (draft)

> **Draft.** HTTP mode is not built yet. What is written here was decided in
> [Where does per-user auth state live on a scale-to-zero, multi-instance service?](https://github.com/luutuankiet/slides-mcp/issues/14)
> and the two `gcloud` commands were checked against Google's Firestore docs,
> and the settings were named in
> [How is HTTP mode switched on, and which settings must be present before it starts?](https://github.com/luutuankiet/slides-mcp/issues/17),
> and the build and deploy were decided in
> [How does a release get built and deployed to Cloud Run?](https://github.com/luutuankiet/slides-mcp/issues/18).
> The service account, secret and Cloud Build steps come from Cloud Run's docs
> and have not been run yet, and instance sizing is still open. Finish this
> page when HTTP mode lands.

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

## One-time setup

1. **Enable the APIs.**
   ```
   gcloud services enable \
     firestore.googleapis.com \
     secretmanager.googleapis.com \
     run.googleapis.com \
     cloudbuild.googleapis.com \
     artifactregistry.googleapis.com \
     --project=<GCP_PROJECT>
   ```
   Cloud Build and Artifact Registry are what `gcloud run deploy --source`
   builds and stores the image with.

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
   on it, on top of permission to deploy Cloud Run and run Cloud Build.
   ```
   gcloud iam service-accounts add-iam-policy-binding <SERVICE_ACCOUNT> \
     --project=<GCP_PROJECT> \
     --member='user:<DEPLOYER_EMAIL>' \
     --role='roles/iam.serviceAccountUser'
   ```

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
| `PORT` | no | `8080` | set by Cloud Run |

To run HTTP mode locally, point it at the Firestore emulator with
`FIRESTORE_EMULATOR_HOST`; there is no in-memory store.

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

## What signs everyone out

| event | result |
|---|---|
| redeploy, new revision, scale to zero, more instances | everyone stays signed in |
| rotating the Google OAuth client secret | everyone signs in again: the server-token signing key is derived from it |
| deleting the auth database | everyone signs in again (blocked by delete protection) |
| Google refusing a refresh (revoked, suspended, unused for 6 months) | that one person signs in again |

## Cost

Firestore's free tier "applies to only one Firestore database per project",
and in most projects `(default)` already holds it, so this database is billed
from its first read. At 10 people making about 300 tool calls a day, each call
reading the store twice, that is roughly 180k reads a month plus a few thousand
writes: cents a month.

Expired records are never deleted: the storage library saves expiry as text,
which Firestore's automatic deletion cannot use. They are ignored when read,
and at this size they add up to a few thousand small documents a year.

## Still to write

- Instance sizing flags for `gcloud run deploy`, once they can be measured.
- The Google OAuth consent screen and Web client, which are console-only.
