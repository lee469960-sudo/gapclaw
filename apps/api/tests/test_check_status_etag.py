"""check_status ETag / 304."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from starlette.requests import Request


def test_check_status_etag_and_304(monkeypatch):
    from app.routers import agent_chat as ac

    running_flag = {"v": False}
    monkeypatch.setattr(ac, "is_running", lambda aid, sid: running_flag["v"])

    def _req(etag: str | None = None) -> Request:
        headers = []
        if etag:
            headers.append((b"if-none-match", etag.encode()))
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/pages/page_agent_chat.cgi",
            "raw_path": b"/pages/page_agent_chat.cgi",
            "query_string": b"",
            "headers": headers,
            "client": ("127.0.0.1", 123),
            "server": ("test", 80),
        }
        return Request(scope)

    user = SimpleNamespace(username="admin")

    async def _run():
        r1 = await ac.chat_get(
            request=_req(),
            action="check_status",
            agent_id="a1",
            session_id="s1",
            path=None,
            message_id=None,
            limit=None,
            user=user,
            db=None,
        )
        assert r1.status_code == 200
        etag = r1.headers.get("etag")
        assert etag

        r2 = await ac.chat_get(
            request=_req(etag),
            action="check_status",
            agent_id="a1",
            session_id="s1",
            path=None,
            message_id=None,
            limit=None,
            user=user,
            db=None,
        )
        assert r2.status_code == 304

        running_flag["v"] = True
        r3 = await ac.chat_get(
            request=_req(etag),
            action="check_status",
            agent_id="a1",
            session_id="s1",
            path=None,
            message_id=None,
            limit=None,
            user=user,
            db=None,
        )
        assert r3.status_code == 200
        assert b"true" in r3.body

    asyncio.run(_run())
