from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.config import Settings
from app.deps import get_session_user
from app.models import Agent, User
from app.routers import release_management
from app.services.release_agent import RELEASE_AGENT, ReleaseAgent
from app.services.release_ledger import ReleaseLedger


def _event(release_id: str, status: str = "succeeded") -> dict[str, object]:
    occurred_at = "2026-09-11 11:00:00" if release_id.endswith("new") else "2026-09-11 10:00:00"
    return {"release_id": release_id, "target_id": "production", "status": status, "occurred_at": occurred_at}


def _manifest(release_id: str) -> dict[str, object]:
    return {
        "release_id": release_id, "target_id": "production", "version": release_id, "git_tag": release_id,
        "commit_sha": "a" * 40, "api_image": f"registry/gap-api:{release_id}",
        "web_image": f"registry/gap-web:{release_id}", "created_at": "2026-09-11 10:00:00",
    }


def _db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_release_agent_is_a_deterministic_read_only_system_definition():
    db = _db()
    ledger = ReleaseLedger(db)
    ledger.record_terminal_callback(_event("release-old"))
    ledger.record_terminal_callback(_event("release-new", "reconciliation_required"))
    agent = ReleaseAgent(ledger)

    assert RELEASE_AGENT.capabilities == ("release_status", "release_history", "release_explanation")
    assert agent.status(target_id="production")["state"] == "reconciliation_required"
    assert [item["release_id"] for item in agent.history(target_id="production")] == ["release-new", "release-old"]
    assert "不会自动继续部署" in agent.explain(target_id="production")
    assert db.query(Agent).count() == 0


def test_release_agent_limits_history_and_bulk_loads_manifests():
    db = _db()
    ledger = ReleaseLedger(db)
    for release_id, occurred_at in (
        ("release-1", "2026-09-11 10:00:00"),
        ("release-2", "2026-09-11 11:00:00"),
        ("release-3", "2026-09-11 12:00:00"),
    ):
        ledger.store_manifest(_manifest(release_id))
        ledger.record_terminal_callback({
            "release_id": release_id, "target_id": "production", "status": "succeeded", "occurred_at": occurred_at,
        })

    manifest_selects = {"count": 0}

    def count_manifest_select(_conn, _cursor, statement, _parameters, _context, _executemany):
        if "FROM release_manifest_records" in statement:
            manifest_selects["count"] += 1

    event.listen(db.bind, "before_cursor_execute", count_manifest_select)
    try:
        history = ReleaseAgent(ledger).history(target_id="production", limit=2)
    finally:
        event.remove(db.bind, "before_cursor_execute", count_manifest_select)

    assert [item["release_id"] for item in history] == ["release-3", "release-2"]
    assert [item["api_image"] for item in history] == ["registry/gap-api:release-3", "registry/gap-api:release-2"]
    assert manifest_selects["count"] == 1


def test_release_management_exposes_authenticated_read_only_status_and_history():
    db = _db()
    ReleaseLedger(db).record_terminal_callback(_event("release-1"))
    app = FastAPI()
    app.include_router(release_management.router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_session_user] = lambda: User(username="viewer", password_hash="x")
    client = TestClient(app)

    status = client.get("/api/release-management/status")
    history = client.get("/api/release-management/history?limit=1")
    assert status.status_code == history.status_code == 200
    assert status.json()["data"]["agent"]["id"] == "release-agent"
    assert history.json()["data"]["items"][0]["release_id"] == "release-1"
    assert client.post("/api/release-management/status").status_code == 405


def test_staging_release_management_ignores_request_target_and_returns_only_staging_records(monkeypatch):
    db = _db()
    ledger = ReleaseLedger(db)
    ledger.record_terminal_callback(_event("release-production"))
    ledger.record_terminal_callback({**_event("release-staging"), "target_id": "staging"})
    app = FastAPI()
    app.include_router(release_management.router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_session_user] = lambda: User(username="viewer", password_hash="x")
    monkeypatch.setattr(release_management, "get_settings", lambda: Settings(release_environment="staging"))
    client = TestClient(app)

    status = client.get("/api/release-management/status?target_id=production")
    history = client.get("/api/release-management/history?target_id=production")

    assert status.status_code == history.status_code == 200
    assert status.json()["data"]["target_id"] == "staging"
    assert history.json()["data"]["target_id"] == "staging"
    assert [item["release_id"] for item in history.json()["data"]["items"]] == ["release-staging"]
