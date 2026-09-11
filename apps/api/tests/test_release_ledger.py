from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import ReleaseCallbackDelivery, ReleaseLifecycleAudit, ReleaseManifestRecord, ReleaseRollbackRequest
from app.services.release_ledger import ReleaseLedger, ReleaseLedgerError


def _ledger() -> tuple[object, ReleaseLedger]:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    return db, ReleaseLedger(db)


def _manifest() -> dict[str, object]:
    return {
        "release_id": "release-1", "target_id": "production", "version": "v1.2.3", "git_tag": "v1.2.3",
        "commit_sha": "a" * 40, "api_image": "registry/gap-api@sha256:" + "b" * 64,
        "web_image": "registry/gap-web@sha256:" + "c" * 64, "created_at": "2026-09-11 10:00:00",
    }


def test_release_ledger_persists_fixed_manifest_and_redacts_by_omission():
    db, ledger = _ledger()
    record = ledger.store_manifest(_manifest())
    assert db.get(ReleaseManifestRecord, record.release_id).api_image.endswith("b" * 64)
    serialized = json.dumps({column.name: getattr(record, column.name) for column in record.__table__.columns})
    assert "password" not in serialized and "private_key" not in serialized
    with pytest.raises(ReleaseLedgerError, match="release_sensitive_field_forbidden"):
        ledger.store_manifest({**_manifest(), "private_key": "must-not-store"})


def test_terminal_callback_is_idempotent_and_keeps_one_delivery_record():
    db, ledger = _ledger()
    event = {"release_id": "release-1", "target_id": "production", "status": "succeeded", "health_result": "ok"}
    first, created = ledger.record_terminal_callback(event)
    second, repeated = ledger.record_terminal_callback(event)
    assert created is True and repeated is False and first.id == second.id
    assert db.query(ReleaseLifecycleAudit).count() == 1
    assert db.query(ReleaseCallbackDelivery).count() == 1


def test_release_history_is_newest_first_and_rollback_request_is_auditable():
    db, ledger = _ledger()
    ledger.record_terminal_callback({"release_id": "old", "target_id": "production", "status": "succeeded", "occurred_at": "2026-09-11 10:00:00"})
    ledger.record_terminal_callback({"release_id": "new", "target_id": "production", "status": "rolled_back", "occurred_at": "2026-09-11 11:00:00"})
    request = ledger.request_rollback(release_id="old", target_id="production", requested_by="admin")
    assert [row.release_id for row in ledger.history(target_id="production")] == ["new", "old"]
    assert db.get(ReleaseRollbackRequest, request.id).requested_by == "admin"
