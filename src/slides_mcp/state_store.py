"""The server's short-lived state: dry-run plans and temporary images.

One interface, two implementations. stdio mode and unit tests use
`MemoryStateStore`; `serve-http` uses `HostedStateStore`, which keeps plans in
the Firestore database HTTP mode already has and images in a GCS bucket, so a
plan made on one Cloud Run instance can be applied on another.
"""
from __future__ import annotations

import json
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

PLAN_TTL_SECONDS = 3600
PLANS_COLLECTION = "slides-mcp__plans"
IMAGE_PREFIX = "slides-mcp/"
IMAGE_URL_TTL_SECONDS = 300
_EXTENSIONS = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif"}

_STDIO_IMAGE_MESSAGE = (
    "Hosting an image for Google to fetch needs the hosted server "
    "(slides-mcp serve-http with SLIDES_MCP_IMAGE_BUCKET set); this server runs over "
    "stdio. Pass a public image URL instead, or use the hosted server.")

_NO_BUCKET_MESSAGE = (
    "SLIDES_MCP_IMAGE_BUCKET is not set on this server, so it has nowhere to "
    "host an image for Google to fetch. Ask the deployer to set it (see "
    "docs/deploying-to-cloud-run.md), or pass a public image URL instead.")


class ImageHostingUnavailable(RuntimeError):
    """No bucket to host a temporary image in: stdio mode, or the bucket is unset."""


class ImageSigningUnavailable(ImageHostingUnavailable):
    """The server's credentials can neither sign a URL nor name a service account to sign as."""


@dataclass(frozen=True)
class HostedImage:
    url: str     # short-lived URL Google can fetch the image from
    handle: str  # what delete_image takes


def _new_plan_id() -> str:
    return secrets.token_urlsafe(18)


def _plan_record(payload, deck_id, caller, expires_at) -> dict[str, Any]:
    # Payload as JSON text: Firestore rejects arrays nested in arrays, and a
    # deck script's input can hold table rows.
    return {"payload": json.dumps(payload), "deck_id": deck_id, "caller": caller or "",
            "expires_at": expires_at}


def _plan_payload(record, deck_id, caller, now: float) -> dict | None:
    """The plan, or None when it is expired or belongs to another deck or caller."""
    if (record is None or record["expires_at"] <= now or record["deck_id"] != deck_id
            or record["caller"] != (caller or "")):
        return None
    return json.loads(record["payload"])


class MemoryStateStore:
    """Process memory. Plans only; images need the hosted server."""

    def __init__(self, clock=time.time):
        self._clock = clock
        self._plans: dict[str, dict[str, Any]] = {}

    def save_plan(self, payload: dict[str, Any], *, deck_id: str, caller: str | None,
                  ttl_seconds: float = PLAN_TTL_SECONDS) -> str:
        plan_id = _new_plan_id()
        self._plans[plan_id] = _plan_record(payload, deck_id, caller,
                                            self._clock() + ttl_seconds)
        return plan_id

    def load_plan(self, plan_id: str, *, deck_id: str, caller: str | None) -> dict | None:
        return _plan_payload(self._plans.get(plan_id), deck_id, caller, self._clock())

    def expire_plan(self, plan_id: str) -> None:
        self._plans.pop(plan_id, None)

    def check_image_hosting(self) -> None:
        raise ImageHostingUnavailable(_STDIO_IMAGE_MESSAGE)

    def put_image(self, data: bytes, *, content_type: str) -> HostedImage:
        raise ImageHostingUnavailable(_STDIO_IMAGE_MESSAGE)

    def delete_image(self, handle: str) -> None:
        raise ImageHostingUnavailable(_STDIO_IMAGE_MESSAGE)


class HostedStateStore:
    """`serve-http`: plans in Firestore, temporary images in a GCS bucket.

    Plans go in the named database the auth store uses, collection
    `slides-mcp__plans`. `expires_at` is a Firestore timestamp so a TTL policy
    can delete old plans; until it does, load treats them as not found.
    Clients are built on first use, so plans work without a bucket and images
    without Firestore.
    """

    def __init__(self, *, database: str, image_bucket: str | None, clock=time.time,
                 firestore_client=None, storage_client=None):
        self.database = database
        self.image_bucket = image_bucket
        self._clock = clock
        self._firestore = firestore_client
        self._storage = storage_client

    # ---- plans

    def _plans(self):
        if self._firestore is None:
            from google.cloud import firestore

            self._firestore = firestore.Client(database=self.database)
        return self._firestore.collection(PLANS_COLLECTION)

    def save_plan(self, payload: dict[str, Any], *, deck_id: str, caller: str | None,
                  ttl_seconds: float = PLAN_TTL_SECONDS) -> str:
        plan_id = _new_plan_id()
        record = _plan_record(payload, deck_id, caller, self._clock() + ttl_seconds)
        record["expires_at"] = datetime.fromtimestamp(record["expires_at"], tz=UTC)
        self._plans().document(plan_id).set(record)
        return plan_id

    def load_plan(self, plan_id: str, *, deck_id: str, caller: str | None) -> dict | None:
        snapshot = self._plans().document(plan_id).get()
        if not snapshot.exists:
            return None
        record = snapshot.to_dict()
        record["expires_at"] = record["expires_at"].timestamp()
        return _plan_payload(record, deck_id, caller, self._clock())

    def expire_plan(self, plan_id: str) -> None:
        self._plans().document(plan_id).delete()

    # ---- images

    def check_image_hosting(self) -> None:
        """Raise ImageHostingUnavailable now, before any work, when there is no bucket."""
        if not self.image_bucket:
            raise ImageHostingUnavailable(_NO_BUCKET_MESSAGE)

    def _bucket(self):
        self.check_image_hosting()
        if self._storage is None:
            from google.cloud import storage

            self._storage = storage.Client()
        return self._storage.bucket(self.image_bucket)

    def put_image(self, data: bytes, *, content_type: str) -> HostedImage:
        """Upload under a random name; return a V4 signed GET URL valid a few minutes."""
        bucket = self._bucket()
        handle = f"{IMAGE_PREFIX}{secrets.token_urlsafe(24)}{_EXTENSIONS.get(content_type, '')}"
        blob = bucket.blob(handle)
        blob.upload_from_string(data, content_type=content_type)
        try:
            url = blob.generate_signed_url(
                version="v4", method="GET",
                expiration=timedelta(seconds=IMAGE_URL_TTL_SECONDS),
                **_signing_kwargs(self._storage))
        except Exception:
            # The caller never gets a handle, so nothing else would delete it.
            blob.delete()
            raise
        return HostedImage(url=url, handle=handle)

    def delete_image(self, handle: str) -> None:
        from google.api_core.exceptions import NotFound

        try:
            self._bucket().blob(handle).delete()
        except NotFound:
            pass


def _signing_kwargs(storage_client) -> dict[str, str]:
    """How to sign a URL with whatever credentials the storage client holds.

    A key file or impersonated credentials sign locally. Cloud Run's
    metadata-server credentials hold no key, so signing goes through the IAM
    signBlob API as the service account itself, which needs
    roles/iam.serviceAccountTokenCreator on that account.
    """
    from google.auth.credentials import Signing
    from google.auth.transport.requests import Request

    creds = storage_client._credentials
    if isinstance(creds, Signing):
        return {}
    if not hasattr(creds, "service_account_email"):
        raise ImageSigningUnavailable(
            "This server cannot sign a URL for the image: it runs with credentials that "
            "hold no private key and belong to no service account (for example a user "
            "account). Run it as a service account that can sign as itself; see "
            "docs/deploying-to-cloud-run.md, one-time setup step 10.")
    if not creds.valid:
        creds.refresh(Request())
    return {"service_account_email": creds.service_account_email,
            "access_token": creds.token}


_current: MemoryStateStore | HostedStateStore = MemoryStateStore()


def current() -> MemoryStateStore | HostedStateStore:
    """The store tools use: in memory unless `serve-http` installed a hosted one."""
    return _current


def install(store: MemoryStateStore | HostedStateStore) -> None:
    """Called once by `serve-http` before the port opens."""
    global _current
    _current = store
