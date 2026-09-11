from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.deps import get_session_user
from app.models import ReleaseRollbackRequest, User
from app.routers import release_management
from app.services.release_ledger import ReleaseLedger
from app.services.release_rollback import ROLLBACK_CONFIRMATION, ReleaseRollbackService


class Runner:
    def __init__(self, baseline=True):
        self.baseline, self.calls = baseline, []

    def status(self):
        return {"last_known_healthy": {"release_id": "healthy-1", "target_id": "production"}} if self.baseline else {"release": {"phase": "idle"}}

    def rollback(self):
        self.calls.append("rollback")
        return {"status": "accepted"}


def _db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_confirmed_rollback_uses_only_runner_known_healthy_target_and_records_operator():
    db = _db()
    runner = Runner()
    service = ReleaseRollbackService(ReleaseLedger(db), runner, target_id="production")

    result = service.submit(
        displayed_release_id="healthy-1", displayed_target_id="production",
        confirmation=ROLLBACK_CONFIRMATION, requested_by="admin",
    )

    assert result["status"] == "submitted"
    assert runner.calls == ["rollback"]
    request = db.get(ReleaseRollbackRequest, result["request_id"])
    assert request.requested_by == "admin"


def test_invalid_confirmation_or_missing_baseline_never_calls_runner():
    db = _db()
    runner = Runner()
    service = ReleaseRollbackService(ReleaseLedger(db), runner, target_id="production")
    try:
        service.submit(displayed_release_id="healthy-1", displayed_target_id="production", confirmation="wrong", requested_by="admin")
    except Exception as exc:
        assert exc.args[0] == "release_rollback_confirmation_invalid"
    assert runner.calls == []

    no_baseline = Runner(baseline=False)
    try:
        ReleaseRollbackService(ReleaseLedger(db), no_baseline, target_id="production").submit(
            displayed_release_id="anything", displayed_target_id="production", confirmation=ROLLBACK_CONFIRMATION, requested_by="admin",
        )
    except Exception as exc:
        assert exc.args[0] == "release_runner_no_healthy_release"
    assert no_baseline.calls == []


def test_rollback_api_rejects_non_admin_and_invalid_confirmation_without_runner(monkeypatch):
    db = _db()
    runner = Runner()
    app = FastAPI()
    app.include_router(release_management.router)
    app.dependency_overrides[get_db] = lambda: db
    monkeypatch.setattr(release_management, "_rollback_service", lambda _db: ReleaseRollbackService(ReleaseLedger(_db), runner, target_id="production"))
    client = TestClient(app)

    app.dependency_overrides[get_session_user] = lambda: User(username="viewer", password_hash="x", roles='["user"]')
    assert client.post("/api/release-management/rollback", json={"displayed_release_id": "healthy-1", "displayed_target_id": "production", "confirmation": ROLLBACK_CONFIRMATION}).status_code == 403
    assert runner.calls == []

    app.dependency_overrides[get_session_user] = lambda: User(username="admin", password_hash="x", roles='["admin"]')
    assert client.post("/api/release-management/rollback", json={"displayed_release_id": "healthy-1", "displayed_target_id": "production", "confirmation": "wrong"}).status_code == 400
    assert runner.calls == []


def test_rollback_target_is_admin_only_and_comes_from_runner(monkeypatch):
    db = _db()
    runner = Runner()
    app = FastAPI()
    app.include_router(release_management.router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_session_user] = lambda: User(username="admin", password_hash="x", roles='["admin"]')
    monkeypatch.setattr(release_management, "_rollback_service", lambda _db: ReleaseRollbackService(ReleaseLedger(_db), runner, target_id="production"))

    response = TestClient(app).get("/api/release-management/rollback-target")

    assert response.status_code == 200
    assert response.json()["data"] == {"release_id": "healthy-1", "target_id": "production"}
