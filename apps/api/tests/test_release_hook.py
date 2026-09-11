from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.models import ReleaseHookDelivery, ReleaseLifecycleAudit
from app.routers import release_hook
from app.services.release_hook import ReleaseHookConfig, ReleaseHookError, ReleaseHookService
from app.services.release_ledger import ReleaseLedger
from app.services.release_runner import ReleaseRunnerError
from fastapi import FastAPI
from fastapi.testclient import TestClient


NOW = datetime(2026, 9, 12, 8, 0, tzinfo=timezone.utc)
SECRET = "h" * 32


def _manifest(**overrides: object) -> dict[str, object]:
    result: dict[str, object] = {
        "schema_version": 1,
        "release_id": "v1.2.3-01234567",
        "git_tag": "v1.2.3",
        "version": "v1.2.3",
        "commit_sha": "0123456789abcdef0123456789abcdef01234567",
        "target_id": "production",
        "api_image": "registry.example.com/gap-api@sha256:" + "a" * 64,
        "web_image": "registry.example.com/gap-web@sha256:" + "b" * 64,
        "created_at": "2026-09-12T07:59:00Z",
        "health_check_version": "v1",
    }
    result.update(overrides)
    return result


def _signed_body(*, delivery_id: str = "release-1", issued_at: datetime = NOW, manifest: dict[str, object] | None = None) -> tuple[bytes, str]:
    envelope = {
        "delivery_id": delivery_id,
        "issued_at": issued_at.isoformat().replace("+00:00", "Z"),
        "manifest": manifest or _manifest(),
    }
    raw = json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode()
    signature = "sha256=" + hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return raw, signature


class Runner:
    def __init__(self, *, error: bool = False):
        self.error, self.calls = error, []

    def deploy(self, manifest: dict[str, object]) -> dict[str, object]:
        self.calls.append(manifest)
        if self.error:
            raise ReleaseRunnerError("release_runner_request_failed")
        return {"status": "accepted"}


def _service(*, error: bool = False) -> tuple[object, ReleaseHookService, Runner]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    runner = Runner(error=error)
    service = ReleaseHookService(
        ReleaseLedger(sessionmaker(bind=engine)()), runner, ReleaseHookConfig(SECRET, 300),
        target_id="production", clock=lambda: NOW,
    )
    return engine, service, runner


def test_hook_accepts_only_signed_fresh_canonical_manifest_then_calls_runner_once():
    engine, service, runner = _service()
    raw, signature = _signed_body()

    accepted = service.accept(raw, signature=signature)
    repeated = service.accept(raw, signature=signature)

    assert accepted["status"] == "accepted"
    assert repeated == {"status": "duplicate", "delivery_id": "release-1", "release_id": "v1.2.3-01234567"}
    assert len(runner.calls) == 1
    db = sessionmaker(bind=engine)()
    assert db.get(ReleaseHookDelivery, "release-1").state == "accepted"
    assert db.query(ReleaseLifecycleAudit).filter_by(status="received").count() == 1


@pytest.mark.parametrize("kind", ["signature", "expired", "noncanonical", "target"])
def test_hook_rejects_invalid_inputs_without_runner_or_delivery_record(kind: str):
    engine, service, runner = _service()
    raw, signature = _signed_body(
        issued_at=NOW - timedelta(seconds=301) if kind == "expired" else NOW,
        manifest=_manifest(target_id="staging") if kind == "target" else None,
    )
    if kind == "signature":
        signature = "sha256=" + "0" * 64
    elif kind == "noncanonical":
        raw = json.dumps(json.loads(raw), indent=2).encode()
        signature = "sha256=" + hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()

    with pytest.raises(ReleaseHookError):
        service.accept(raw, signature=signature)

    db = sessionmaker(bind=engine)()
    assert runner.calls == []
    assert db.query(ReleaseHookDelivery).count() == 0


def test_hook_records_failed_runner_dispatch_and_refuses_to_replay_it():
    engine, service, runner = _service(error=True)
    raw, signature = _signed_body()

    with pytest.raises(ReleaseHookError, match="release_hook_runner_unavailable"):
        service.accept(raw, signature=signature)
    assert service.accept(raw, signature=signature)["status"] == "duplicate"

    db = sessionmaker(bind=engine)()
    assert db.get(ReleaseHookDelivery, "release-1").state == "dispatch_failed"
    assert db.query(ReleaseLifecycleAudit).filter_by(status="dispatch_failed").count() == 1
    assert len(runner.calls) == 1


def test_hook_route_uses_raw_signed_body_and_returns_no_runner_error_detail(monkeypatch):
    engine, service, _runner = _service()
    db = sessionmaker(bind=engine)()
    app = FastAPI()
    app.include_router(release_hook.router)
    app.dependency_overrides[get_db] = lambda: db
    monkeypatch.setattr(release_hook, "_service", lambda _db: service)
    raw, signature = _signed_body()
    client = TestClient(app)

    assert client.post("/internal/release-hook", content=raw).status_code == 403
    response = client.post("/internal/release-hook", content=raw, headers={"X-Gap-Release-Signature": signature})
    assert response.status_code == 202
    assert response.json()["status"] == "accepted"
