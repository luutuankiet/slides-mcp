"""The server state store: dry-run plans and temporary images.

These behaviour tests run against the in-memory store here. The Firestore store
runs the same tests against the emulator in tests/integration/, which swaps the
`make_store` fixture and imports everything else from this module.
"""
from __future__ import annotations

import pytest

from slides_mcp import state_store


class Clock:
    def __init__(self, now: float = 1_800_000_000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def make_store(clock):
    return lambda: state_store.MemoryStateStore(clock=clock)


@pytest.fixture
def store(make_store):
    return make_store()


PLAN = {"script": "deck.slides[0].title = 'x'", "input": {"rows": [[1, 2], [3]]},
        "options": {"slides": "all"}}


def test_saved_plan_loads_back_for_same_deck_and_caller(store):
    plan_id = store.save_plan(PLAN, deck_id="deck-a", caller="a@example.com")
    assert store.load_plan(plan_id, deck_id="deck-a", caller="a@example.com") == PLAN


def test_plan_expires_after_its_time_to_live(store, clock):
    plan_id = store.save_plan(PLAN, deck_id="deck-a", caller="a@example.com", ttl_seconds=60)
    clock.now += 59
    assert store.load_plan(plan_id, deck_id="deck-a", caller="a@example.com") == PLAN
    clock.now += 2
    assert store.load_plan(plan_id, deck_id="deck-a", caller="a@example.com") is None


def test_plans_live_one_hour_by_default(store, clock):
    plan_id = store.save_plan(PLAN, deck_id="deck-a", caller=None)
    clock.now += 3599
    assert store.load_plan(plan_id, deck_id="deck-a", caller=None) == PLAN
    clock.now += 2
    assert store.load_plan(plan_id, deck_id="deck-a", caller=None) is None


def test_plan_for_another_deck_is_not_found(store):
    plan_id = store.save_plan(PLAN, deck_id="deck-a", caller="a@example.com")
    assert store.load_plan(plan_id, deck_id="deck-b", caller="a@example.com") is None


def test_plan_made_by_another_caller_is_not_found(store):
    plan_id = store.save_plan(PLAN, deck_id="deck-a", caller="a@example.com")
    assert store.load_plan(plan_id, deck_id="deck-a", caller="b@example.com") is None
    assert store.load_plan(plan_id, deck_id="deck-a", caller=None) is None


def test_unknown_plan_id_is_not_found(store):
    assert store.load_plan("no-such-plan", deck_id="deck-a", caller=None) is None


def test_expired_plan_is_gone_and_expiring_twice_is_harmless(store):
    plan_id = store.save_plan(PLAN, deck_id="deck-a", caller=None)
    store.expire_plan(plan_id)
    assert store.load_plan(plan_id, deck_id="deck-a", caller=None) is None
    store.expire_plan(plan_id)


def test_each_plan_gets_its_own_id(store):
    first = store.save_plan(PLAN, deck_id="deck-a", caller=None)
    second = store.save_plan({**PLAN, "script": "other"}, deck_id="deck-a", caller=None)
    assert first != second
    assert store.load_plan(second, deck_id="deck-a", caller=None)["script"] == "other"


# ---- images: the in-memory store refuses ------------------------------


def test_memory_store_image_calls_say_the_hosted_server_is_needed(clock):
    store = state_store.MemoryStateStore(clock=clock)
    with pytest.raises(state_store.ImageHostingUnavailable, match="slides-mcp serve-http"):
        store.put_image(b"\x89PNG", content_type="image/png")
    with pytest.raises(state_store.ImageHostingUnavailable, match="slides-mcp serve-http"):
        store.delete_image("any-handle")


class _FakeBlob:
    def __init__(self, bucket, name):
        self.bucket, self.name = bucket, name

    def upload_from_string(self, data, content_type):
        self.bucket.objects[self.name] = data

    def generate_signed_url(self, **kw):
        raise PermissionError("iam.serviceAccounts.signBlob denied")

    def delete(self):
        del self.bucket.objects[self.name]


class _FakeBucket:
    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def blob(self, name):
        return _FakeBlob(self, name)


class _FakeStorage:
    """GCS at the network boundary: holds objects; signing is refused."""

    class _credentials:  # same name as the real client's attribute signing reads
        valid = True
        service_account_email = "runner@example.iam.gserviceaccount.com"
        token = "t"

    def __init__(self):
        self.the_bucket = _FakeBucket()

    def bucket(self, name):
        return self.the_bucket


def test_image_is_not_left_behind_when_signing_fails(clock):
    storage = _FakeStorage()
    store = state_store.HostedStateStore(database="slides-mcp-auth", image_bucket="images",
                                         clock=clock, storage_client=storage)
    with pytest.raises(PermissionError, match="signBlob"):
        store.put_image(b"\x89PNG", content_type="image/png")
    assert storage.the_bucket.objects == {}


def test_hosted_store_without_a_bucket_names_the_missing_setting(clock):
    store = state_store.HostedStateStore(database="slides-mcp-auth", image_bucket=None,
                                         clock=clock)
    with pytest.raises(state_store.ImageHostingUnavailable, match="SLIDES_MCP_IMAGE_BUCKET"):
        store.put_image(b"\x89PNG", content_type="image/png")
    with pytest.raises(state_store.ImageHostingUnavailable, match="SLIDES_MCP_IMAGE_BUCKET"):
        store.delete_image("any-handle")
