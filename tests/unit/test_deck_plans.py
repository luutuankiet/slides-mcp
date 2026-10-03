"""Dry-run plans: a run_deck_script dry run returns a plan_id, and applying it
re-runs the stored script against the deck as it is now.

Called as an MCP client would, over the fake Slides API and the in-memory
state store. Assertions are about what came back and what reached the fake.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any

import pytest
from fastmcp.server.auth.auth import AccessToken
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser

from slides_mcp import auth, state_store
from slides_mcp.server import run_deck_script

DECK = "deck_fixture"

# Paints every slide whose background is not already #101010, and says which.
REPAINT = """
  const todo = deck.select().filter(s => s.background.hex !== "#101010");
  for (const s of todo) emit(setBackground(s, input.hex));
  return todo.map(s => s.id);
"""


class Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch, clock: Clock) -> state_store.MemoryStateStore:
    fresh = state_store.MemoryStateStore(clock=clock)
    monkeypatch.setattr(state_store, "_current", fresh)
    return fresh


@contextmanager
def signed_in_as(sub: str):
    """HTTP mode, with this Google account as the caller."""
    reset = auth_context_var.set(AuthenticatedUser(AccessToken(
        token=f"token-{sub}", client_id="c", scopes=["openid"], expires_at=2_000_000_000,
        claims={"sub": sub, "email": f"{sub}@example.com"})))
    try:
        yield
    finally:
        auth_context_var.reset(reset)


def body(out: Any) -> dict[str, Any]:
    """The JSON body of a tool reply, with or without attached thumbnails."""
    return json.loads(out[0]) if isinstance(out, list) else out


async def dry_run(script: str = REPAINT, **kw) -> str:
    out = await run_deck_script(deck_url=DECK, script=script, **kw)
    assert out["isError"] is False, out
    return out["preview"]["plan_id"]


async def test_applying_a_plan_runs_the_stored_script_and_returns_a_receipt(fake, store):
    plan_id = await dry_run(input={"hex": "#101010"})
    assert fake.batches == []

    out = body(await run_deck_script(deck_url=DECK, plan_id=plan_id, dry_run=False))

    assert out["isError"] is False, out
    assert out["dry_run"] is False
    assert len(fake.batches) == 1
    assert {next(iter(r)) for r in fake.batches[0]} == {"updatePageProperties"}
    assert out["receipt"]["applied_request_count"] == 6
    assert out["thumbnails"]["slide_ids"]  # the normal apply reply, receipt included


async def test_apply_re_runs_against_the_deck_as_it_is_now(fake, store):
    plan_id = await dry_run(input={"hex": "#101010"})
    # A colleague paints the first slide and adds a slide after the dry run.
    first = fake.deck["slides"][0]
    first.setdefault("pageProperties", {})["pageBackgroundFill"] = {
        "solidFill": {"color": {"rgbColor": {"red": 16 / 255, "green": 16 / 255,
                                             "blue": 16 / 255}}}}
    fake.deck["slides"].append({"objectId": "added_by_colleague", "pageElements": []})

    out = body(await run_deck_script(deck_url=DECK, plan_id=plan_id, dry_run=False))

    assert out["isError"] is False, out
    painted = [r["updatePageProperties"]["objectId"] for r in fake.batches[0]]
    assert first["objectId"] not in painted
    assert "added_by_colleague" in painted
    assert len(painted) == 6
    assert out["result"] == painted


async def test_destructive_plan_needs_confirmation_on_apply(fake, store):
    delete = "emit({deleteObject: {objectId: 'translucent_badge'}})"
    # Confirming on the dry run does not carry over to the apply.
    plan_id = await dry_run(delete, confirm_destructive=True)

    refused = await run_deck_script(deck_url=DECK, plan_id=plan_id, dry_run=False)
    assert refused["isError"] is True
    assert "deleteObject" in json.dumps(refused["error"])
    assert fake.batches == []

    # Refused is not applied: the plan is still there.
    out = body(await run_deck_script(deck_url=DECK, plan_id=plan_id, dry_run=False,
                                     confirm_destructive=True))
    assert out["isError"] is False, out
    assert fake.batches == [[{"deleteObject": {"objectId": "translucent_badge"}}]]


# ---- plans that cannot be applied ------------------------------------------------


def assert_run_dry_run_again(out: dict[str, Any], fake) -> None:
    assert out["isError"] is True
    assert out["error"]["kind"] == "plan"
    assert "dry run again" in out["error"]["message"]
    assert fake.batches == []


async def test_unknown_plan_says_to_run_the_dry_run_again(fake, store):
    out = await run_deck_script(deck_url=DECK, plan_id="no-such-plan", dry_run=False)
    assert_run_dry_run_again(out, fake)


async def test_plan_id_with_a_script_is_an_error(fake, store):
    plan_id = await dry_run(input={"hex": "#101010"})
    out = await run_deck_script(deck_url=DECK, plan_id=plan_id, script=REPAINT,
                                dry_run=False)
    assert out["isError"] is True and out["error"]["kind"] == "plan"
    assert "not both" in out["error"]["message"]
    assert fake.batches == []


async def test_plan_id_without_dry_run_false_is_an_error(fake, store):
    plan_id = await dry_run(input={"hex": "#101010"})
    out = await run_deck_script(deck_url=DECK, plan_id=plan_id)
    assert out["isError"] is True and out["error"]["kind"] == "plan"
    assert "dry_run=false" in out["error"]["message"]
    # The plan is still there to apply.
    applied = body(await run_deck_script(deck_url=DECK, plan_id=plan_id, dry_run=False))
    assert applied["isError"] is False, applied


async def test_plan_expires_after_an_hour(fake, store, clock):
    plan_id = await dry_run(input={"hex": "#101010"})
    clock.now += 3601
    out = await run_deck_script(deck_url=DECK, plan_id=plan_id, dry_run=False)
    assert_run_dry_run_again(out, fake)


async def test_plan_does_not_apply_to_another_deck(fake, store):
    plan_id = await dry_run(input={"hex": "#101010"})
    out = await run_deck_script(deck_url="some_other_deck", plan_id=plan_id, dry_run=False)
    assert_run_dry_run_again(out, fake)


async def test_plan_is_single_use(fake, store):
    plan_id = await dry_run(input={"hex": "#101010"})
    first = body(await run_deck_script(deck_url=DECK, plan_id=plan_id, dry_run=False))
    assert first["isError"] is False, first
    fake.batches.clear()
    again = await run_deck_script(deck_url=DECK, plan_id=plan_id, dry_run=False)
    assert_run_dry_run_again(again, fake)


async def test_on_the_hosted_server_only_the_planner_can_apply(fake, store, monkeypatch):
    monkeypatch.setattr(auth, "_http_mode", True)
    with signed_in_as("alice"):
        plan_id = await dry_run(input={"hex": "#101010"})
    with signed_in_as("bob"):
        out = await run_deck_script(deck_url=DECK, plan_id=plan_id, dry_run=False)
    assert_run_dry_run_again(out, fake)
    with signed_in_as("alice"):
        out = body(await run_deck_script(deck_url=DECK, plan_id=plan_id, dry_run=False,
                                         receipt="off"))
    assert out["isError"] is False, out
