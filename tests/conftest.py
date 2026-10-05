from __future__ import annotations

import pytest

from slides_mcp import auth, slides_api
from tests.fake_api import FakeSlides

WRITE_SCOPES = ["https://www.googleapis.com/auth/presentations",
                "https://www.googleapis.com/auth/drive.readonly"]


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeSlides:
    """Swap the Slides API for the fake and grant a write-scoped token."""
    api = FakeSlides()
    monkeypatch.setattr(slides_api, "get_presentation", api.get_presentation)
    monkeypatch.setattr(slides_api, "batch_update", api.batch_update)
    monkeypatch.setattr(slides_api, "create_presentation", api.create_presentation)
    monkeypatch.setattr(slides_api, "get_thumbnail_bytes", api.get_thumbnail_bytes)
    monkeypatch.setattr(auth, "credentials_info", lambda: {"exists": True, "scopes": WRITE_SCOPES})
    monkeypatch.delenv("SLIDES_MCP_AUDIT_LOG", raising=False)
    return api
