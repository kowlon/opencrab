# tests/unit/test_seecrab_sessions_api.py
"""Tests for GET /api/seecrab/sessions pagination."""
from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from seeagent.api.routes.seecrab import router


def _make_session(chat_id: str, title: str, last_active: datetime, messages=None):
    s = MagicMock()
    s.chat_id = chat_id
    s.last_active = last_active
    s.metadata = {"title": title}
    ctx = MagicMock()
    ctx.messages = messages or []
    s.context = ctx
    return s


def _build_app(sessions: list | None = None):
    """Build a minimal FastAPI app with mocked session_manager."""
    app = FastAPI()
    app.include_router(router)
    if sessions is not None:
        sm = MagicMock()
        sm.list_sessions.return_value = sessions
        app.state.session_manager = sm
    return app


@pytest.fixture
def five_sessions():
    now = datetime.now()
    return [
        _make_session(f"s{i}", f"Session {i}", now - timedelta(hours=i))
        for i in range(5)
    ]


class TestListSessionsPagination:
    @pytest.mark.asyncio
    async def test_no_session_manager_returns_empty(self):
        app = _build_app()  # no session_manager on app.state
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/seecrab/sessions")
        data = resp.json()
        assert resp.status_code == 200
        assert data["total"] == 0
        assert data["sessions"] == []
        assert data["limit"] == 50
        assert data["offset"] == 0

    @pytest.mark.asyncio
    async def test_default_pagination_returns_all(self, five_sessions):
        app = _build_app(five_sessions)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/seecrab/sessions")
        data = resp.json()
        assert data["total"] == 5
        assert len(data["sessions"]) == 5
        assert data["limit"] == 50
        assert data["offset"] == 0

    @pytest.mark.asyncio
    async def test_limit_slices_correctly(self, five_sessions):
        app = _build_app(five_sessions)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/seecrab/sessions?limit=2")
        data = resp.json()
        assert data["total"] == 5
        assert len(data["sessions"]) == 2
        assert data["limit"] == 2
        assert data["offset"] == 0

    @pytest.mark.asyncio
    async def test_offset_skips_sessions(self, five_sessions):
        app = _build_app(five_sessions)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/seecrab/sessions?limit=2&offset=3")
        data = resp.json()
        assert data["total"] == 5
        assert len(data["sessions"]) == 2
        assert data["offset"] == 3

    @pytest.mark.asyncio
    async def test_offset_beyond_total_returns_empty(self, five_sessions):
        app = _build_app(five_sessions)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/seecrab/sessions?offset=100")
        data = resp.json()
        assert data["total"] == 5
        assert len(data["sessions"]) == 0

    @pytest.mark.asyncio
    async def test_sort_order_is_newest_first(self, five_sessions):
        app = _build_app(five_sessions)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/seecrab/sessions")
        data = resp.json()
        # s0 has the latest last_active (now - 0h), should be first
        assert data["sessions"][0]["id"] == "s0"
        assert data["sessions"][-1]["id"] == "s4"

    @pytest.mark.asyncio
    async def test_pagination_preserves_sort_order(self, five_sessions):
        app = _build_app(five_sessions)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/seecrab/sessions?limit=2&offset=2")
        data = resp.json()
        # After sort: s0, s1, s2, s3, s4 → offset=2 gives s2, s3
        assert data["sessions"][0]["id"] == "s2"
        assert data["sessions"][1]["id"] == "s3"
