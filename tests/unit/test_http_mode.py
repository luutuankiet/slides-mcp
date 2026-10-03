"""HTTP mode: the per-caller credential seam, the settings check, the CLI and
the fastmcp 4 behaviour we depend on. No network.
"""
from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor

import anyio
import httpx2 as httpx
import pytest
from fastmcp.server.auth.auth import AccessToken
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser

from slides_mcp import auth, cli, http_mode, slides_api

GOOD_ENV = {
    "SLIDES_MCP_GOOGLE_CLIENT_ID": "id.apps.googleusercontent.com",
    "SLIDES_MCP_GOOGLE_CLIENT_SECRET": "secret",
    "SLIDES_MCP_BASE_URL": "https://slides-mcp.example.run.app/",
    "SLIDES_MCP_FIRESTORE_DATABASE": "slides-mcp-auth",
}


@pytest.fixture
def built(monkeypatch: pytest.MonkeyPatch) -> list:
    """Record the credentials every Slides client is built with."""
    seen: list = []
    monkeypatch.setattr(slides_api, "build",
                        lambda *a, credentials, **k: seen.append(credentials) or object())
    slides_api._stdio_reset()
    yield seen
    slides_api._stdio_reset()


@pytest.fixture
def http(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth, "_http_mode", True)


def _caller(token: str, **claims) -> AuthenticatedUser:
    return AuthenticatedUser(AccessToken(token=token, client_id="c", scopes=["openid"],
                                         expires_at=2_000_000_000, claims=claims))


# ---- the credential seam --------------------------------------------


def test_stdio_resolves_from_token_json_and_caches(built, monkeypatch):
    calls = []
    monkeypatch.setattr(auth, "load_credentials", lambda: calls.append(1) or "file-creds")
    first, second = slides_api._slides_service(), slides_api._slides_service()
    assert first is second
    assert calls == [1] and built == ["file-creds"]


def test_stdio_gives_each_thread_its_own_client(built, monkeypatch):
    calls = []
    monkeypatch.setattr(auth, "load_credentials", lambda: calls.append(1) or "file-creds")
    here = slides_api._slides_service()
    with ThreadPoolExecutor(max_workers=1) as pool:
        there = pool.submit(slides_api._slides_service).result()
    assert here is not there
    assert calls == [1] and built == ["file-creds", "file-creds"]


def test_http_builds_from_caller_token_and_caches_nothing(built, http):
    reset = auth_context_var.set(_caller("caller-token"))
    try:
        slides_api._slides_service()
        slides_api._slides_service()
    finally:
        auth_context_var.reset(reset)
    assert [c.token for c in built] == ["caller-token", "caller-token"]
    assert built[0] is not built[1]


def test_http_without_caller_raises_and_never_opens_token_json(built, http, monkeypatch):
    monkeypatch.setattr(auth, "load_credentials",
                        lambda: pytest.fail("HTTP mode must never read token.json"))
    with pytest.raises(auth.NotSignedInError, match="re-authenticate"):
        slides_api._slides_service()
    assert built == []


async def test_caller_survives_the_hop_into_a_worker_thread(built, http):
    # run_deck_script and thumbnails reach Google from anyio worker threads.
    reset = auth_context_var.set(_caller("thread-caller"))
    try:
        await anyio.to_thread.run_sync(slides_api._slides_service)
    finally:
        auth_context_var.reset(reset)
    assert [c.token for c in built] == ["thread-caller"]


def test_http_auth_status_reports_caller_not_token(http):
    reset = auth_context_var.set(_caller("secret-token", email="a@example.com"))
    try:
        info = auth.credentials_info()
    finally:
        auth_context_var.reset(reset)
    assert info["mode"] == "http" and info["email"] == "a@example.com"
    assert "secret-token" not in json.dumps(info)


def test_http_skips_write_scope_precheck(http, monkeypatch):
    from slides_mcp import writes

    monkeypatch.setattr(auth, "credentials_info", lambda: {"scopes": ["readonly"]})
    assert writes.write_scope_error() is None


# ---- settings -------------------------------------------------------


def test_good_settings_pass_and_default_port():
    settings, problems = http_mode.check_settings(GOOD_ENV)
    assert problems == []
    assert settings.port == 8080
    assert settings.base_url == "https://slides-mcp.example.run.app"


def test_every_problem_reported_at_once_without_secret_values():
    env = {**GOOD_ENV, "SLIDES_MCP_GOOGLE_CLIENT_SECRET": "",
           "SLIDES_MCP_BASE_URL": "http://example.com",
           "SLIDES_MCP_FIRESTORE_DATABASE": "(default)", "PORT": "nope"}
    settings, problems = http_mode.check_settings(env)
    assert settings is None
    assert [name for name, _ in problems] == [
        "SLIDES_MCP_GOOGLE_CLIENT_SECRET", "SLIDES_MCP_BASE_URL",
        "SLIDES_MCP_FIRESTORE_DATABASE", "PORT"]
    text = http_mode.format_problems(problems)
    assert text.startswith("slides-mcp serve-http: cannot start, 4 settings need fixing:")
    assert "http://example.com" not in text


@pytest.mark.parametrize("db", ["ab", "Slides", "1abc", "has_underscore"])
def test_invalid_database_ids_rejected(db):
    _, problems = http_mode.check_settings({**GOOD_ENV, "SLIDES_MCP_FIRESTORE_DATABASE": db})
    assert [name for name, _ in problems] == ["SLIDES_MCP_FIRESTORE_DATABASE"]


def test_localhost_http_allowed_for_local_testing():
    _, problems = http_mode.check_settings({**GOOD_ENV,
                                            "SLIDES_MCP_BASE_URL": "http://localhost:8080"})
    assert problems == []


def test_image_bucket_is_optional():
    settings, problems = http_mode.check_settings(GOOD_ENV)
    assert problems == [] and settings.image_bucket is None
    settings, problems = http_mode.check_settings(
        {**GOOD_ENV, "SLIDES_MCP_IMAGE_BUCKET": " my-images "})
    assert problems == [] and settings.image_bucket == "my-images"


def test_state_store_uses_the_auth_database_and_the_image_bucket():
    from slides_mcp import state_store

    settings, _ = http_mode.check_settings({**GOOD_ENV, "SLIDES_MCP_IMAGE_BUCKET": "my-images"})
    store = http_mode.build_state_store(settings)
    assert isinstance(store, state_store.HostedStateStore)
    assert (store.database, store.image_bucket) == ("slides-mcp-auth", "my-images")


def test_stdio_state_store_is_in_memory():
    from slides_mcp import state_store

    assert isinstance(state_store.current(), state_store.MemoryStateStore)


def test_serve_exits_2_before_opening_a_port(capsys):
    assert http_mode.serve(env={}) == 2
    err = capsys.readouterr().err
    assert "4 settings need fixing" in err and "SLIDES_MCP_FIRESTORE_DATABASE" in err


# ---- CLI ------------------------------------------------------------


def test_unknown_bare_word_exits_2(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["slides-mcp", "serve-htp"])
    assert cli.main() == 2
    assert "unknown command: serve-htp" in capsys.readouterr().err


def test_serve_http_help_lists_settings(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["slides-mcp", "serve-http", "--help"])
    assert cli.main() == 0
    out = capsys.readouterr().out
    for name in (*GOOD_ENV, "PORT"):
        assert name in out


def test_leading_flag_still_starts_stdio(monkeypatch):
    started = []
    monkeypatch.setattr(cli, "_dispatch_server", lambda: started.append(1) or 0)
    monkeypatch.setattr(sys, "argv", ["slides-mcp", "--some-client-flag"])
    assert cli.main() == 0 and started == [1]


# ---- sign-in proxy --------------------------------------------------


async def test_validation_cached_until_access_token_expires():
    now = [1000.0]
    calls = []

    async def verify(token):
        calls.append(token)
        return AccessToken(token=token, client_id="c", scopes=[], expires_at=1060)

    cache = http_mode.ValidationCache(verify, clock=lambda: now[0])
    await cache.verify_token("t")
    await cache.verify_token("t")
    assert calls == ["t"]
    now[0] = 1061
    await cache.verify_token("t")
    assert calls == ["t", "t"]


async def test_failed_validation_is_not_cached():
    calls = []

    async def verify(token):
        calls.append(token)

    cache = http_mode.ValidationCache(verify)
    assert await cache.verify_token("t") is None
    assert await cache.verify_token("t") is None
    assert calls == ["t", "t"]


def test_auth_store_sanitizes_url_client_ids(monkeypatch):
    # claude.ai signs in with a CIMD client ID, which is a URL.
    import key_value.aio.stores.firestore as fs

    made = {}
    monkeypatch.setattr(fs, "FirestoreStore", lambda **kw: made.update(kw) or object())
    settings, _ = http_mode.check_settings(GOOD_ENV)
    http_mode.build_auth_store(settings)
    key = made["key_sanitization_strategy"].sanitize(
        "https://claude.ai/oauth/mcp-oauth-client-metadata")
    assert "/" not in key
    assert made["key_sanitization_strategy"].sanitize("deploy-smoke-test") == "deploy-smoke-test"


def test_provider_wiring():
    from key_value.aio.stores.memory import MemoryStore

    settings, _ = http_mode.check_settings(GOOD_ENV)
    provider = http_mode.build_provider(settings, MemoryStore())
    assert provider._redirect_path == "/auth/callback"


def test_provider_installs_cache_on_proxy_verifier():
    from key_value.aio.stores.memory import MemoryStore

    settings, _ = http_mode.check_settings(GOOD_ENV)
    provider = http_mode.build_provider(settings, MemoryStore())
    bound = provider._token_validator.verify_token
    assert getattr(bound, "__self__", None).__class__ is http_mode.ValidationCache


# ---- fastmcp 4 behaviour we pin ------------------------------------


async def _initialize(app) -> dict:
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as c:
            r = await c.post("/mcp", headers={"Accept": "application/json, text/event-stream"},
                             json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                   "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                                              "clientInfo": {"name": "t", "version": "0"}}})
            get = await c.get("/mcp", headers={"Accept": "text/event-stream"})
    return {"init": r.json(), "get_status": get.status_code}


async def test_http_does_not_advertise_list_changed():
    # server.py writes a private fastmcp attribute to turn this off; this guards it.
    from slides_mcp.server import mcp

    out = await _initialize(mcp.http_app(stateless_http=True, json_response=True))
    assert out["init"]["result"]["capabilities"]["tools"]["listChanged"] is False
    assert out["get_status"] == 405


async def test_tool_descriptions_keep_returns_sections():
    # fastmcp would otherwise drop `Returns:` from descriptions agents read.
    from slides_mcp.server import mcp

    tools = {t.name: t for t in await mcp.list_tools()}
    assert "Returns:" in tools["search_deck"].description
    for name, tool in tools.items():
        assert tool.description == tool.fn.__doc__, name


async def test_server_reports_slides_mcp_version_not_fastmcps():
    from slides_mcp import __version__
    from slides_mcp.server import mcp

    out = await _initialize(mcp.http_app(stateless_http=True, json_response=True))
    assert out["init"]["result"]["serverInfo"]["version"] == __version__



async def test_raw_requests_tool_routes_to_scripts_and_the_skill():
    # With tool search an agent may load only this tool; its description must
    # carry the facts raw requests need and point at the rest.
    from slides_mcp.server import mcp

    tools = {t.name: t for t in await mcp.list_tools()}
    text = tools["exec_batch_update"].description
    for needle in ("run_deck_script", "914400", "autofitType", "install_skill"):
        assert needle in text, needle


async def test_server_instructions_route_agents():
    from slides_mcp.server import INSTRUCTIONS, mcp

    out = await _initialize(mcp.http_app(stateless_http=True, json_response=True))
    assert out["init"]["result"]["instructions"] == INSTRUCTIONS


async def test_each_tool_call_logs_its_name_and_nothing_else(capsys):
    from slides_mcp.server import mcp

    await mcp.call_tool("install_skill", {})
    lines = [x for x in capsys.readouterr().err.splitlines()
             if x.startswith("slides-mcp call ")]
    record = json.loads(lines[-1].removeprefix("slides-mcp call "))
    assert record["tool"] == "install_skill" and record["ok"] is True
    assert set(record) == {"tool", "ms", "ok"}
