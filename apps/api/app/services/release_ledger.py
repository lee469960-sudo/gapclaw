"""Persistent, fixed-field release records for the deterministic Release Agent."""

from __future__ import annotations

from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.models import (
    ReleaseCallbackDelivery,
    ReleaseHookDelivery,
    ReleaseLifecycleAudit,
    ReleaseManifestRecord,
    ReleaseRollbackRequest,
)
from app.security import new_id, now_str


_FORBIDDEN_FIELD_PARTS = ("password", "secret", "private", "credential", "token", "key")


class ReleaseLedgerError(ValueError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def _require_fixed_fields(values: dict[str, object], fields: tuple[str, ...]) -> None:
    if any(not str(values.get(field, "") or "") for field in fields):
        raise ReleaseLedgerError("release_record_invalid")
    if any(part in key.lower() for key in values for part in _FORBIDDEN_FIELD_PARTS):
        raise ReleaseLedgerError("release_sensitive_field_forbidden")


class ReleaseLedger:
    def __init__(self, db: Session):
        self.db = db

    def store_manifest(self, manifest: dict[str, object]) -> ReleaseManifestRecord:
        _require_fixed_fields(manifest, ("release_id", "target_id", "version", "git_tag", "commit_sha", "api_image", "web_image"))
        existing = self.db.get(ReleaseManifestRecord, str(manifest["release_id"]))
        if existing:
            return existing
        record = ReleaseManifestRecord(
            release_id=str(manifest["release_id"]), target_id=str(manifest["target_id"]),
            version=str(manifest["version"]), git_tag=str(manifest["git_tag"]),
            commit_sha=str(manifest["commit_sha"]), api_image=str(manifest["api_image"]),
            web_image=str(manifest["web_image"]), created_at=str(manifest.get("created_at") or now_str()),
        )
        self.db.add(record)
        self.db.commit()
        return record

    def record_terminal_callback(self, event: dict[str, object]) -> tuple[ReleaseLifecycleAudit, bool]:
        _require_fixed_fields(event, ("release_id", "target_id", "status"))
        release_id, status = str(event["release_id"]), str(event["status"])
        existing = self.db.query(ReleaseLifecycleAudit).filter_by(release_id=release_id, status=status).first()
        if existing:
            return existing, False
        audit = ReleaseLifecycleAudit(
            id=new_id(), release_id=release_id, target_id=str(event["target_id"]), status=status,
            health_result=str(event.get("health_result") or ""), rollback_result=str(event.get("rollback_result") or ""),
            failure_summary=str(event.get("failure_summary") or ""), trigger_source=str(event.get("trigger_source") or "runner"),
            occurred_at=str(event.get("occurred_at") or now_str()),
        )
        delivery = ReleaseCallbackDelivery(
            id=new_id(), release_id=release_id, status=status, received_at=now_str(),
        )
        self.db.add_all((audit, delivery))
        self.db.commit()
        return audit, True

    def accept_hook_delivery(self, manifest: dict[str, object], *, delivery_id: str) -> tuple[ReleaseHookDelivery, bool]:
        """Durably deduplicate an accepted Hook before any Runner request."""
        _require_fixed_fields(manifest, ("release_id", "target_id", "version", "git_tag", "commit_sha", "api_image", "web_image"))
        if not delivery_id:
            raise ReleaseLedgerError("release_hook_delivery_invalid")
        existing = self.db.get(ReleaseHookDelivery, delivery_id)
        if existing is not None:
            return existing, False
        release_id = str(manifest["release_id"])
        existing_release = self.db.query(ReleaseHookDelivery).filter_by(release_id=release_id).first()
        if existing_release is not None:
            return existing_release, False
        self.store_manifest(manifest)
        delivery = ReleaseHookDelivery(
            delivery_id=delivery_id,
            release_id=release_id,
            target_id=str(manifest["target_id"]),
            state="accepted",
            accepted_at=now_str(),
        )
        audit = ReleaseLifecycleAudit(
            id=new_id(), release_id=release_id, target_id=str(manifest["target_id"]), status="received",
            trigger_source="github_hook", occurred_at=now_str(),
        )
        self.db.add_all((delivery, audit))
        try:
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            existing = self.db.get(ReleaseHookDelivery, delivery_id)
            if existing is None:
                existing = self.db.query(ReleaseHookDelivery).filter_by(release_id=release_id).first()
            if existing is not None:
                return existing, False
            raise
        return delivery, True

    def mark_hook_dispatch_failed(self, delivery: ReleaseHookDelivery, *, reason: str) -> None:
        delivery.state = "dispatch_failed"
        audit = ReleaseLifecycleAudit(
            id=new_id(), release_id=delivery.release_id, target_id=delivery.target_id, status="dispatch_failed",
            failure_summary=reason, trigger_source="github_hook", occurred_at=now_str(),
        )
        self.db.add(audit)
        self.db.commit()

    def request_rollback(self, *, release_id: str, target_id: str, requested_by: str) -> ReleaseRollbackRequest:
        if not release_id or not target_id or not requested_by:
            raise ReleaseLedgerError("release_rollback_request_invalid")
        request = ReleaseRollbackRequest(
            id=new_id(), release_id=release_id, target_id=target_id, requested_by=requested_by, requested_at=now_str(),
        )
        self.db.add(request)
        self.db.commit()
        return request

    def finish_rollback_request(self, request: ReleaseRollbackRequest, *, status: str) -> ReleaseRollbackRequest:
        if status not in {"submitted", "failed"}:
            raise ReleaseLedgerError("release_rollback_result_invalid")
        request.status = status
        request.completed_at = now_str()
        self.db.commit()
        return request

    def history(self, *, target_id: str) -> list[ReleaseLifecycleAudit]:
        return self.db.query(ReleaseLifecycleAudit).filter_by(target_id=target_id).order_by(
            ReleaseLifecycleAudit.occurred_at.desc(), ReleaseLifecycleAudit.id.desc()
        ).all()
