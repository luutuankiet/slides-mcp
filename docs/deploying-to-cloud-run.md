---
title: Deploying the shared server to Cloud Run (draft)
summary: the Google Cloud setup a deployer does once before running slides-mcp as a shared remote server, with the Firestore database, service account access and settings it needs; draft until HTTP mode ships
verified: 2026-09-28
---

# Deploying the shared server to Cloud Run (draft)

> **Draft.** HTTP mode is not built yet. What is written here was decided in
> [Where does per-user auth state live on a scale-to-zero, multi-instance service?](https://github.com/luutuankiet/slides-mcp/issues/14)
> and the two `gcloud` commands were checked against Google's Firestore docs,
> but the container build, the image location and the name of the database
> setting are still open. Finish this page when HTTP mode lands.

Placeholders used below:

| placeholder | what it is |
|---|---|
| `<GCP_PROJECT>` | the Google Cloud project the service runs in |
| `<REGION>` | the Cloud Run region, e.g. `us-central1` |
| `<SERVICE_ACCOUNT>` | the Cloud Run service's runtime identity, `name@<GCP_PROJECT>.iam.gserviceaccount.com` |

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
     --project=<GCP_PROJECT>
   ```

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

3. **Grant the service account access to that database only.** The condition
   is what keeps it out of every other database in the project; a grant without
   it reaches `(default)` too.
   ```
   gcloud projects add-iam-policy-binding <GCP_PROJECT> \
     --member='serviceAccount:<SERVICE_ACCOUNT>' \
     --role='roles/datastore.user' \
     --condition='expression=resource.name=="projects/<GCP_PROJECT>/databases/slides-mcp-auth",title=slides-mcp-auth-only'
   ```

4. **Review who else can read the database.** Project-wide Owner and Editor,
   and any project-level `roles/datastore.*` grant without a condition, can read
   the stored refresh tokens. Keep that list short.

5. **Set the database name on the service.** HTTP mode refuses to start without
   it, on purpose: the storage library would otherwise fall back to `(default)`
   and write its seven collections next to whatever else lives there. The
   setting's name is not decided yet.

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

- Building the container and where the image lives.
- How the Google OAuth client ID and secret reach the service (Secret Manager,
  plus `roles/secretmanager.secretAccessor` for `<SERVICE_ACCOUNT>`).
- The `gcloud run deploy` command, its flags and instance sizing.
- The Google OAuth consent screen and Web client, which are console-only.
