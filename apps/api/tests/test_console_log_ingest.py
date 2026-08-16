"""POST /api/console writes ERROR lines to web.log."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.deps import get_session_user
from app.models import User
from app.routers import console_log as cl


def test_console_ingest_writes_web_log(tmp_path, monkeypatch):
    web_log = tmp_path / "web.log"
    monkeypatch.setenv("GAP_WEB_LOG", str(web_log))
    cl._web_logger = None

    app = FastAPI()
    app.include_router(cl.router)
    app.dependency_overrides[get_session_user] = lambda: User(
        username="admin",
        password_hash="x",
        roles='["admin"]',
        disabled=False,
    )
    client = TestClient(app)
    r = client.post(
        "/api/console",
        json={
            "entries": [
                {
                    "level": "error",
                    "message": "browser boom",
                    "stack": "Error: boom",
                    "url": "http://localhost/chat",
                }
            ]
        },
    )
    assert r.status_code == 200
    assert r.json()["code"] == 0
    text = web_log.read_text(encoding="utf-8")
    assert "ERROR" in text
    assert "browser boom" in text
