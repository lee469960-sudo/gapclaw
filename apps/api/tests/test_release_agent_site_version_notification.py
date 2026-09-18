from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.deps import get_session_user
from app.config import Settings
from app.models import (
    ImChannel,
    ReleaseLifecycleAudit,
    ReleaseManifestRecord,
    ReleaseNotificationDelivery,
    ReleaseVersionSync,
    SiteConfig,
    User,
)
from app.routers import httpmcp
from app.routers import release_callback
from app.routers import channel
from app.services.release_lifecycle import (
    render_release_notification,
    sanitized_release_summary,
    sync_site_version,
)


def _db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _seed(db, *, status="succeeded", health="ok"):
    db.add(ReleaseManifestRecord(
        release_id="release-1", target_id="production", version="v1.2.3", git_tag="v1.2.3",
        commit_sha="a" * 40, api_image="registry/gap-api@sha256:" + "b" * 64,
        web_image="registry/gap-web@sha256:" + "c" * 64, created_at="2026-09-18 10:00:00",
    ))
    db.add(ReleaseLifecycleAudit(
        id="audit-1", release_id="release-1", target_id="production", status=status,
        health_result=health, occurred_at="2026-09-18 10:01:00",
    ))
    db.commit()


def test_release_version_sync_is_healthy_manifest_only_and_idempotent():
    db = _db()
    _seed(db)
    first = sync_site_version(db, release_id="release-1", target_id="production")
    second = sync_site_version(db, release_id="release-1", target_id="production")
    assert first["state"] == "applied"
    assert second["state"] == "idempotent"
    assert db.query(SiteConfig).filter_by(key="version").one().value == "v1.2.3"


def test_release_version_sync_rejects_unhealthy_and_non_release_sources():
    db = _db()
    _seed(db, status="failed", health="failed")
    try:
        sync_site_version(db, release_id="release-1", target_id="production")
        assert False, "unhealthy release must be rejected"
    except ValueError as exc:
        assert str(exc) == "release_version_sync_requires_healthy_release"
    try:
        sync_site_version(db, release_id="release-1", target_id="production", source="user")
        assert False, "arbitrary caller must be rejected"
    except ValueError as exc:
        assert str(exc) == "release_version_sync_source_forbidden"
    assert db.query(SiteConfig).count() == 0


def test_httpmcp_release_version_sync_is_fixed_and_authorized():
    db = _db()
    _seed(db)
    app = FastAPI()
    app.include_router(httpmcp.router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_session_user] = lambda: User(
        username="admin", password_hash="x", roles='["admin"]',
    )
    client = TestClient(app)
    forbidden = client.post("/pages/page_httpmcp.cgi", json={
        "action": "release_version_sync", "agent_id": "other", "release_id": "release-1",
        "target_id": "production", "url": "https://attacker.invalid", "version": "v9.9.9",
    })
    assert forbidden.status_code == 403
    ok = client.post("/pages/page_httpmcp.cgi", json={
        "action": "release_version_sync", "agent_id": "release-agent", "release_id": "release-1",
        "target_id": "production", "url": "https://attacker.invalid", "version": "v9.9.9",
    })
    assert ok.status_code == 200
    assert ok.json()["data"]["version"] == "v1.2.3"


def test_release_notification_summary_is_sanitized():
    db = _db()
    _seed(db)
    audit = db.query(ReleaseLifecycleAudit).one()
    summary = sanitized_release_summary(db, audit)
    text = render_release_notification(summary)
    encoded = json.dumps(summary, ensure_ascii=False)
    assert "sha256:" in encoded and "v1.2.3" in text
    assert "secret" not in text.lower()
    assert "signature" not in text.lower()


def test_missing_release_channel_is_audited_without_changing_release_state():
    db = _db()
    _seed(db)
    from app.services.release_lifecycle import notify_release_transition
    import asyncio
    result = asyncio.run(notify_release_transition(db, release_id="release-1", status="succeeded"))
    assert result["skipped"] == 1
    row = db.query(ReleaseNotificationDelivery).one()
    assert row.state == "missing_channel"


def test_release_notification_is_deduplicated_and_delivery_failure_is_non_authoritative(monkeypatch):
    db = _db()
    _seed(db)
    channel = ImChannel(
        id="channel-1", name="Release Feishu", provider="feishu", enabled=True,
        agent_id="release-agent", creator="admin",
    )
    channel.set_config({"app_id": "app", "app_secret": "secret", "release_chat_id": "oc_release"})
    db.add(channel)
    db.commit()

    class Adapter:
        calls = 0
        async def send_text(self, _chat_id, _text):
            self.calls += 1

    adapter = Adapter()
    monkeypatch.setattr("app.services.release_lifecycle.create_adapter", lambda *_args: adapter)
    from app.services.release_lifecycle import notify_release_transition
    import asyncio
    first = asyncio.run(notify_release_transition(db, release_id="release-1", status="succeeded"))
    second = asyncio.run(notify_release_transition(db, release_id="release-1", status="succeeded"))
    assert first["sent"] == 1 and second["skipped"] == 1 and adapter.calls == 1

    class BrokenAdapter:
        async def send_text(self, _chat_id, _text):
            raise RuntimeError("provider timeout")

    failed_channel = ImChannel(
        id="channel-2", name="Broken Release Feishu", provider="feishu", enabled=True,
        agent_id="release-agent", creator="admin",
    )
    failed_channel.set_config({"release_chat_id": "oc_release"})
    db.add(failed_channel)
    db.add(ReleaseLifecycleAudit(
        id="audit-failed", release_id="release-1", target_id="production", status="failed",
        health_result="failed", failure_summary="health check failed", occurred_at="2026-09-18 10:02:00",
    ))
    db.commit()
    monkeypatch.setattr("app.services.release_lifecycle.create_adapter", lambda *_args: BrokenAdapter())
    failed = asyncio.run(notify_release_transition(db, release_id="release-1", status="failed"))
    assert failed["failed"] >= 1
    assert db.query(ReleaseLifecycleAudit).filter_by(release_id="release-1", status="succeeded").count() == 1


def test_trusted_runner_healthy_callback_triggers_sync_but_failed_callback_does_not(monkeypatch):
    db = _db()
    app = FastAPI()
    app.include_router(release_callback.router)
    app.dependency_overrides[get_db] = lambda: db
    monkeypatch.setattr(release_callback, "get_settings", lambda: Settings(release_environment="production"))
    import asyncio
    from app.services.release_lifecycle import notify_release_transition
    monkeypatch.setattr(
        release_callback,
        "_notify_release_transition_bg",
        lambda release_id, status: asyncio.run(notify_release_transition(db, release_id=release_id, status=status)),
    )
    client = TestClient(app)
    payload = {
        "schema_version": 1, "release_id": "release-healthy", "git_tag": "v1.2.3", "version": "v1.2.3",
        "commit_sha": "a" * 40, "target_id": "production",
        "api_image": "registry/gap-api@sha256:" + "b" * 64,
        "web_image": "registry/gap-web@sha256:" + "c" * 64,
        "created_at": "2026-09-18T10:00:00Z", "health_check_version": "v1",
        "status": "succeeded", "occurred_at": "2026-09-18T10:01:00Z", "health_result": "ok",
    }
    response = client.post("/internal/release-runner/callback", json=payload, headers={"X-Gap-Runner-Client-Verify": "SUCCESS"})
    assert response.status_code == 200
    assert response.json()["version_sync"]["state"] == "applied"
    assert db.query(SiteConfig).filter_by(key="version").one().value == "v1.2.3"
    duplicate = client.post("/internal/release-runner/callback", json=payload, headers={"X-Gap-Runner-Client-Verify": "SUCCESS"})
    assert duplicate.status_code == 200
    assert db.query(ReleaseVersionSync).filter_by(release_id="release-healthy").count() == 1
    assert db.query(ReleaseNotificationDelivery).filter_by(release_id="release-healthy").count() == 1

    failed_payload = {**payload, "release_id": "release-failed", "status": "failed", "health_result": "failed"}
    failed = client.post("/internal/release-runner/callback", json=failed_payload, headers={"X-Gap-Runner-Client-Verify": "SUCCESS"})
    assert failed.status_code == 200
    assert failed.json()["version_sync"] is None
    assert db.query(SiteConfig).filter_by(key="version").one().value == "v1.2.3"


def test_release_agent_feishu_binding_is_explicit_and_admin_only():
    db = _db()
    app = FastAPI()
    app.include_router(channel.router)
    app.dependency_overrides[get_db] = lambda: db
    admin = User(username="admin", password_hash="x", roles='["admin"]')
    app.dependency_overrides[get_session_user] = lambda: admin
    client = TestClient(app)
    created = client.post("/pages/page_channel.cgi", json={
        "action": "create", "name": "Release Feishu", "provider": "feishu",
        "agent_id": "release-agent", "config": {"release_chat_id": "oc_release"},
    })
    assert created.status_code == 200
    assert created.json()["data"]["agent_id"] == "release-agent"
    assert created.json()["data"]["agent_name"] == "Release Agent"

    app.dependency_overrides[get_session_user] = lambda: User(username="viewer", password_hash="x", roles='["user"]')
    denied = client.post("/pages/page_channel.cgi", json={
        "action": "create", "name": "Bad Release Feishu", "provider": "feishu", "agent_id": "release-agent",
    })
    assert denied.status_code == 200
    assert denied.json()["code"] != 0
