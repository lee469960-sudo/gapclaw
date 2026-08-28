"""Transactional snapshot references and retryable orphan cleanup."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import exists, func, select, update

from app.models import CodeProjectManifest, CodeSourceSnapshot
from app.security import now_str
from app.services.code_agent.snapshot_store import SealedSourceSnapshot, SourceSnapshotStore


_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


class SnapshotLifecycleError(RuntimeError):
    def __init__(self, reason: str = "snapshot_invalid"):
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class SnapshotCleanupResult:
    snapshot_id: str
    outcome: str
    ref_count: int
    cleanup_attempts: int


def _format_time(value: datetime) -> str:
    return value.strftime(_TIME_FORMAT)


def _parse_time(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, _TIME_FORMAT) if value else None
    except ValueError:
        return None


class SnapshotLifecycleService:
    def __init__(self, db, store: SourceSnapshotStore):
        self.db = db
        self.store = store

    def register_sealed(
        self,
        sealed: SealedSourceSnapshot,
        *,
        source_id: str,
        scan_report_id: str,
        importer_version: str,
        policy_hash: str,
        size_bytes: int,
        file_count: int,
    ) -> CodeSourceSnapshot:
        if (
            sealed.snapshot_id != sealed.content_hash[:16]
            or size_bytes < 0
            or file_count < 0
        ):
            raise SnapshotLifecycleError()
        self.store.verify(
            Path(sealed.storage_path),
            expected_hash=sealed.content_hash,
            expected_commit=sealed.resolved_commit,
        )
        existing = (
            self.db.query(CodeSourceSnapshot)
            .filter(CodeSourceSnapshot.content_hash == sealed.content_hash)
            .one_or_none()
        )
        if existing is not None:
            if (
                existing.resolved_commit != sealed.resolved_commit
                or existing.storage_path != sealed.storage_path
                or existing.status not in {"sealed", "failed"}
            ):
                raise SnapshotLifecycleError()
            existing.status = "sealed"
            existing.cleanup_after = ""
            existing.cleanup_next_attempt = ""
            existing.cleanup_error = ""
            existing.cleanup_attempts = 0
            return existing
        row = CodeSourceSnapshot(
            id=sealed.snapshot_id,
            source_id=source_id,
            resolved_commit=sealed.resolved_commit,
            content_hash=sealed.content_hash,
            storage_path=sealed.storage_path,
            scan_report_id=scan_report_id,
            importer_version=importer_version,
            policy_hash=policy_hash,
            status="sealed",
            ref_count=0,
            size_bytes=size_bytes,
            file_count=file_count,
            created_at=now_str(),
            sealed_at=now_str(),
            cleanup_after="",
            cleanup_attempts=0,
            cleanup_error="",
            cleanup_next_attempt="",
        )
        self.db.add(row)
        self.db.flush()
        return row

    def _actual_refs(self, snapshot_id: str) -> int:
        return int(
            self.db.query(func.count(CodeProjectManifest.id))
            .filter(CodeProjectManifest.snapshot_id == snapshot_id)
            .scalar()
            or 0
        )

    def reconcile_ref_count(self, snapshot: CodeSourceSnapshot) -> int:
        actual = self._actual_refs(snapshot.id)
        snapshot.ref_count = actual
        return actual

    def attach_manifest(
        self,
        manifest: CodeProjectManifest,
        snapshot: CodeSourceSnapshot,
    ) -> None:
        if (
            snapshot.status != "sealed"
            or not snapshot.storage_path
            or snapshot.id != snapshot.content_hash[:16]
        ):
            raise SnapshotLifecycleError()
        self.store.verify(
            Path(snapshot.storage_path),
            expected_hash=snapshot.content_hash,
            expected_commit=snapshot.resolved_commit,
        )
        if manifest.snapshot_id == snapshot.id:
            manifest.snapshot_hash = snapshot.content_hash
            manifest.resolved_commit = snapshot.resolved_commit
            return
        previous_snapshot_id = manifest.snapshot_id or ""
        result = self.db.execute(
            update(CodeSourceSnapshot)
            .where(
                CodeSourceSnapshot.id == snapshot.id,
                CodeSourceSnapshot.status == "sealed",
            )
            .values(ref_count=CodeSourceSnapshot.ref_count + 1)
        )
        if result.rowcount != 1:
            raise SnapshotLifecycleError()
        if previous_snapshot_id:
            previous = self.db.execute(
                update(CodeSourceSnapshot)
                .where(
                    CodeSourceSnapshot.id == previous_snapshot_id,
                    CodeSourceSnapshot.ref_count > 0,
                )
                .values(ref_count=CodeSourceSnapshot.ref_count - 1)
            )
            if previous.rowcount != 1:
                raise SnapshotLifecycleError()
        manifest.snapshot_id = snapshot.id
        manifest.snapshot_hash = snapshot.content_hash
        manifest.resolved_commit = snapshot.resolved_commit

    def mark_orphan(
        self,
        snapshot: CodeSourceSnapshot,
        *,
        cleanup_after: datetime,
    ) -> bool:
        actual = self._actual_refs(snapshot.id)
        if actual:
            snapshot.ref_count = actual
            snapshot.status = "sealed"
            snapshot.cleanup_after = ""
            snapshot.cleanup_next_attempt = ""
            snapshot.cleanup_error = ""
            return False
        due = _format_time(cleanup_after)
        manifest_exists = exists(
            select(CodeProjectManifest.id).where(
                CodeProjectManifest.snapshot_id == snapshot.id
            )
        )
        claimed = self.db.execute(
            update(CodeSourceSnapshot)
            .where(
                CodeSourceSnapshot.id == snapshot.id,
                CodeSourceSnapshot.ref_count == 0,
                CodeSourceSnapshot.status.in_(("sealed", "failed")),
                ~manifest_exists,
            )
            .values(
                status="failed",
                cleanup_after=due,
                cleanup_next_attempt=due,
            )
        )
        if claimed.rowcount != 1:
            self.db.expire(snapshot)
            return False
        self.db.expire(snapshot)
        return True

    def cleanup_orphan(
        self,
        snapshot_id: str,
        *,
        now: datetime | None = None,
    ) -> SnapshotCleanupResult:
        current = now or datetime.now()
        snapshot = self.db.get(CodeSourceSnapshot, snapshot_id)
        if snapshot is None:
            return SnapshotCleanupResult(snapshot_id, "missing", 0, 0)
        if snapshot.status == "deleted":
            actual = self._actual_refs(snapshot.id)
            if actual:
                raise SnapshotLifecycleError()
            self.db.commit()
            return SnapshotCleanupResult(snapshot.id, "already_deleted", 0, snapshot.cleanup_attempts)
        actual = self._actual_refs(snapshot.id)
        if actual:
            snapshot.ref_count = actual
            snapshot.status = "sealed"
            snapshot.cleanup_after = ""
            snapshot.cleanup_next_attempt = ""
            snapshot.cleanup_error = ""
            self.db.commit()
            return SnapshotCleanupResult(snapshot.id, "protected", actual, snapshot.cleanup_attempts)
        due = _parse_time(snapshot.cleanup_next_attempt or snapshot.cleanup_after)
        if due is None or due > current:
            self.db.commit()
            return SnapshotCleanupResult(snapshot.id, "not_due", 0, snapshot.cleanup_attempts)
        manifest_exists = exists(
            select(CodeProjectManifest.id).where(
                CodeProjectManifest.snapshot_id == snapshot.id
            )
        )
        if snapshot.ref_count != 0:
            reset = self.db.execute(
                update(CodeSourceSnapshot)
                .where(
                    CodeSourceSnapshot.id == snapshot.id,
                    ~manifest_exists,
                )
                .values(ref_count=0)
            )
            if reset.rowcount != 1:
                self.db.rollback()
                return SnapshotCleanupResult(snapshot.id, "protected", 1, snapshot.cleanup_attempts)
        claim = self.db.execute(
            update(CodeSourceSnapshot)
            .where(
                CodeSourceSnapshot.id == snapshot.id,
                CodeSourceSnapshot.ref_count == 0,
                CodeSourceSnapshot.status.in_(("sealed", "failed")),
                ~manifest_exists,
            )
            .values(status="failed")
        )
        if claim.rowcount != 1:
            self.db.rollback()
            refreshed = self.db.get(CodeSourceSnapshot, snapshot.id)
            protected_refs = self._actual_refs(snapshot.id)
            return SnapshotCleanupResult(
                snapshot.id,
                "protected",
                protected_refs or int(getattr(refreshed, "ref_count", 0) or 0),
                int(getattr(refreshed, "cleanup_attempts", 0) or 0),
            )
        self.db.expire(snapshot)
        snapshot = self.db.get(CodeSourceSnapshot, snapshot_id)
        try:
            self.store.delete_verified(
                Path(snapshot.storage_path),
                expected_hash=snapshot.content_hash,
                expected_commit=snapshot.resolved_commit,
            )
        except Exception:
            snapshot.status = "failed"
            snapshot.cleanup_attempts += 1
            snapshot.cleanup_error = "snapshot_cleanup_failed"
            retry_seconds = min(3600, 60 * (2 ** min(snapshot.cleanup_attempts - 1, 6)))
            snapshot.cleanup_next_attempt = _format_time(
                current + timedelta(seconds=retry_seconds)
            )
            self.db.commit()
            return SnapshotCleanupResult(
                snapshot.id,
                "retry_scheduled",
                0,
                snapshot.cleanup_attempts,
            )
        snapshot.status = "deleted"
        snapshot.storage_path = ""
        snapshot.cleanup_after = ""
        snapshot.cleanup_next_attempt = ""
        snapshot.cleanup_error = ""
        self.db.commit()
        return SnapshotCleanupResult(snapshot.id, "deleted", 0, snapshot.cleanup_attempts)
