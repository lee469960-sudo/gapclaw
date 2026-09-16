from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.config import Settings
from app.models import ReleaseLifecycleAudit
from app.routers import release_callback
from app.services.release_ledger import ReleaseLedger
from app.services.release_runner import (
    ReleaseCallbackService,
    ReleaseReconciler,
    ReleaseRunnerClient,
    ReleaseRunnerError,
    ReleaseRunnerTls,
)


def _payload(**overrides):
    payload = {
        "schema_version": 1,
        "release_id": "release-1",
        "git_tag": "v1.2.3",
        "version": "v1.2.3",
        "commit_sha": "0123456789abcdef0123456789abcdef01234567",
        "target_id": "production",
        "api_image": "registry.example.com/gap-api@sha256:" + "a" * 64,
        "web_image": "registry.example.com/gap-web@sha256:" + "b" * 64,
        "created_at": "2026-09-11T08:00:00Z",
        "health_check_version": "v1",
        "status": "succeeded",
        "occurred_at": "2026-09-11T08:01:00Z",
        "health_result": "ok",
    }
    payload.update(overrides)
    return payload


def test_runner_client_allows_only_private_https_runner_host():
    with pytest.raises(ReleaseRunnerError, match="release_runner_url_not_allowed"):
        ReleaseRunnerTls("https://example.test", Path("ca"), Path("cert"), Path("key"))


def test_runner_client_allows_the_distinct_staging_private_runner_host():
    tls = ReleaseRunnerTls("https://gap-runner-staging.internal:9443", Path("ca"), Path("cert"), Path("key"))

    assert tls.base_url == "https://gap-runner-staging.internal:9443"


def test_runner_client_uses_fixed_mtls_files_and_fixed_operation_path(monkeypatch):
    captured = {}

    class SslContext:
        def load_cert_chain(self, *, certfile, keyfile):
            captured["cert"] = (certfile, keyfile)

    ssl_context = SslContext()

    def create_default_context(*, cafile):
        captured["ca"] = cafile
        return ssl_context

    monkeypatch.setattr("app.services.release_runner.ssl.create_default_context", create_default_context)

    class Response:
        def raise_for_status(self): pass
        def json(self): return {"status": "ok"}

    class Client:
        def __init__(self, **kwargs): captured.update(kwargs)
        def __enter__(self): return self
        def __exit__(self, *_args): pass
        def request(self, method, url):
            captured["request"] = (method, url)
            return Response()

    client = ReleaseRunnerClient(
        ReleaseRunnerTls("https://gap-runner.internal:9443/", Path("ca"), Path("cert"), Path("key")),
        client_factory=Client,
    )
    assert client.health() == {"status": "ok"}
    assert captured["ca"] == "ca"
    assert captured["verify"] is ssl_context
    assert captured["cert"] == ("cert", "key")
    assert captured["request"] == ("GET", "https://gap-runner.internal:9443/v1/health")


def test_runner_client_sends_only_the_verified_manifest_to_fixed_deploy_path(monkeypatch):
    captured = {}

    class SslContext:
        def load_cert_chain(self, **_kwargs): pass

    monkeypatch.setattr("app.services.release_runner.ssl.create_default_context", lambda **_kwargs: SslContext())

    class Response:
        def raise_for_status(self): pass
        def json(self): return {"status": "accepted"}

    class Client:
        def __init__(self, **_kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *_args): pass
        def request(self, method, url, **kwargs):
            captured["request"] = (method, url, kwargs)
            return Response()

    manifest = _payload()
    manifest.pop("status")
    manifest.pop("occurred_at")
    manifest.pop("health_result")
    client = ReleaseRunnerClient(
        ReleaseRunnerTls("https://gap-runner.internal:9443", Path("ca"), Path("cert"), Path("key")),
        client_factory=Client,
    )

    assert client.deploy(manifest) == {"status": "accepted"}
    assert captured["request"] == ("POST", "https://gap-runner.internal:9443/v1/deploy", {"json": manifest})


def test_callback_requires_nginx_verified_client_and_is_idempotent():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    service = ReleaseCallbackService(ReleaseLedger(sessionmaker(bind=engine)()))
    payload = _payload()
    with pytest.raises(ReleaseRunnerError, match="release_runner_client_untrusted"):
        service.accept(payload, proxy_verified="FAILED")
    assert service.accept(payload, proxy_verified="SUCCESS")["created"] is True
    assert service.accept(payload, proxy_verified="SUCCESS")["created"] is False


def test_callback_route_rejects_untrusted_or_invalid_payload_and_deduplicates():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    app = FastAPI()
    app.include_router(release_callback.router)
    app.dependency_overrides[get_db] = lambda: db
    client = TestClient(app)

    assert client.post("/internal/release-runner/callback", json=_payload()).status_code == 403
    assert client.post(
        "/internal/release-runner/callback", headers={"X-Gap-Runner-Client-Verify": "SUCCESS"},
        json=_payload(api_image="not-a-digest"),
    ).status_code == 422
    accepted = client.post(
        "/internal/release-runner/callback", headers={"X-Gap-Runner-Client-Verify": "SUCCESS"}, json=_payload(),
    )
    repeated = client.post(
        "/internal/release-runner/callback", headers={"X-Gap-Runner-Client-Verify": "SUCCESS"}, json=_payload(),
    )
    assert accepted.status_code == repeated.status_code == 200
    assert accepted.json()["created"] is True
    assert repeated.json()["created"] is False


def test_staging_callback_rejects_production_target_before_ledger_write(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    app = FastAPI()
    app.include_router(release_callback.router)
    app.dependency_overrides[get_db] = lambda: db
    monkeypatch.setattr(release_callback, "get_settings", lambda: Settings(release_environment="staging"))
    client = TestClient(app)

    rejected = client.post(
        "/internal/release-runner/callback", headers={"X-Gap-Runner-Client-Verify": "SUCCESS"}, json=_payload(),
    )
    accepted = client.post(
        "/internal/release-runner/callback", headers={"X-Gap-Runner-Client-Verify": "SUCCESS"},
        json=_payload(target_id="staging"),
    )

    assert rejected.status_code == 422
    assert rejected.json()["detail"] == "release_callback_target_not_allowed"
    assert accepted.status_code == 200
    assert db.query(ReleaseLifecycleAudit).filter_by(target_id="production").count() == 0


def test_reconciliation_marks_missing_or_non_terminal_runner_state():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    ledger = ReleaseLedger(sessionmaker(bind=engine)())
    service = ReleaseCallbackService(ledger)
    service.accept(_payload(), proxy_verified="SUCCESS")
    reconciler = ReleaseReconciler(ledger)

    assert reconciler.reconcile({"release": {"release_id": "release-1", "target_id": "production", "phase": "succeeded"}})["state"] == "synchronized"
    assert reconciler.reconcile({"release": {"release_id": "release-2", "target_id": "production", "phase": "succeeded"}})["reason"] == "release_audit_missing"
    assert reconciler.reconcile({"release": {"release_id": "release-1", "target_id": "production", "phase": "deploying"}})["reason"] == "release_runner_not_terminal"
