"""OpenSpec task 3.6: transactional references and retryable orphan cleanup."""

from __future__ import annotations

import shutil
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import (
    CodeProject,
    CodeProjectManifest,
    CodeRepositorySource,
    CodeScanReport,
    CodeSourceSnapshot,
)
from app.services.code_agent.snapshot_lifecycle import SnapshotLifecycleService
from app.services.code_agent.snapshot_store import SealedSourceSnapshot


SNAPSHOT_ID = "b" * 16


class FakeSnapshotStore:
    def __init__(self, failures: int = 0):
        self.failures = failures
        self.calls = []

    def verify(self, snapshot_path, *, expected_hash, expected_commit):
        assert Path(snapshot_path).exists()
        return None

    def delete_verified(self, snapshot_path, *, expected_hash, expected_commit):
        self.calls.append((str(snapshot_path), expected_hash, expected_commit))
        if self.failures:
            self.failures -= 1
            raise OSError("simulated cleanup failure")
        shutil.rmtree(snapshot_path)
        return True


def _database(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'lifecycle.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    db.add(CodeProject(id="project-a", name="Project", creator="owner"))
    db.add(CodeScanReport(
        id="scan-a",
        scope="source",
        input_hash="a" * 64,
        scanner="test",
        scanner_version="1",
        status="complete",
        complete=True,
    ))
    db.add(CodeRepositorySource(
        id="source-a",
        project_id="project-a",
        source_type="https",
        locator="https://git.example.test/repo.git",
        status="active",
    ))
    db.add_all([
        CodeProjectManifest(id="manifest-a", project_id="project-a", version=1, status="draft"),
        CodeProjectManifest(id="manifest-b", project_id="project-a", version=2, status="draft"),
    ])
    db.commit()
    return engine, Session


def _registered_snapshot(db, tmp_path, store=None):
    path = tmp_path / "snapshot-content"
    path.mkdir(exist_ok=True)
    (path / "payload").write_text("sealed", encoding="utf-8")
    sealed = SealedSourceSnapshot(
        snapshot_id=SNAPSHOT_ID,
        content_hash="b" * 64,
        resolved_commit="c" * 40,
        storage_path=str(path),
        deduplicated=False,
    )
    service = SnapshotLifecycleService(db, store or FakeSnapshotStore())
    snapshot = service.register_sealed(
        sealed,
        source_id="source-a",
        scan_report_id="scan-a",
        importer_version="1",
        policy_hash="d" * 64,
        size_bytes=6,
        file_count=1,
    )
    db.commit()
    return service, snapshot, path


def test_stale_concurrent_publish_sessions_increment_without_lost_update(tmp_path):
    _engine, Session = _database(tmp_path)
    setup = Session()
    _service, _snapshot, _path = _registered_snapshot(setup, tmp_path)
    setup.close()
    first = Session()
    second = Session()
    stale_second_snapshot = second.get(CodeSourceSnapshot, SNAPSHOT_ID)

    SnapshotLifecycleService(first, FakeSnapshotStore()).attach_manifest(
        first.get(CodeProjectManifest, "manifest-a"),
        first.get(CodeSourceSnapshot, SNAPSHOT_ID),
    )
    first.commit()
    SnapshotLifecycleService(second, FakeSnapshotStore()).attach_manifest(
        second.get(CodeProjectManifest, "manifest-b"),
        stale_second_snapshot,
    )
    second.commit()

    check = Session()
    assert check.get(CodeSourceSnapshot, SNAPSHOT_ID).ref_count == 2
    assert {
        check.get(CodeProjectManifest, "manifest-a").snapshot_id,
        check.get(CodeProjectManifest, "manifest-b").snapshot_id,
    } == {SNAPSHOT_ID}
    SnapshotLifecycleService(check, FakeSnapshotStore()).attach_manifest(
        check.get(CodeProjectManifest, "manifest-a"),
        check.get(CodeSourceSnapshot, SNAPSHOT_ID),
    )
    check.commit()
    assert check.get(CodeSourceSnapshot, SNAPSHOT_ID).ref_count == 2


def test_publish_transaction_failure_rolls_back_reference_then_marks_orphan(tmp_path):
    _engine, Session = _database(tmp_path)
    db = Session()
    _service, snapshot, path = _registered_snapshot(db, tmp_path)
    manifest = db.get(CodeProjectManifest, "manifest-a")
    service = SnapshotLifecycleService(db, FakeSnapshotStore())

    try:
        service.attach_manifest(manifest, snapshot)
        db.flush()
        raise RuntimeError("simulated publish transaction failure")
    except RuntimeError:
        db.rollback()

    snapshot = db.get(CodeSourceSnapshot, SNAPSHOT_ID)
    manifest = db.get(CodeProjectManifest, "manifest-a")
    assert snapshot.ref_count == 0
    assert manifest.snapshot_id == ""
    assert service.mark_orphan(snapshot, cleanup_after=datetime(2026, 8, 23)) is True
    db.commit()
    assert snapshot.status == "failed"
    assert path.exists()


def test_cleanup_rechecks_manifests_and_never_deletes_a_referenced_snapshot(tmp_path):
    _engine, Session = _database(tmp_path)
    db = Session()
    store = FakeSnapshotStore()
    service, snapshot, path = _registered_snapshot(db, tmp_path, store)
    manifest = db.get(CodeProjectManifest, "manifest-a")
    manifest.snapshot_id = snapshot.id
    manifest.snapshot_hash = snapshot.content_hash
    snapshot.ref_count = 0
    snapshot.status = "failed"
    snapshot.cleanup_after = "2026-08-23 00:00:00"
    snapshot.cleanup_next_attempt = snapshot.cleanup_after
    db.commit()

    result = service.cleanup_orphan(snapshot.id, now=datetime(2026, 8, 23, 1, 0, 0))

    assert result.outcome == "protected"
    assert result.ref_count == 1
    assert path.exists()
    assert store.calls == []
    assert db.get(CodeSourceSnapshot, snapshot.id).status == "sealed"


def test_cleanup_failure_persists_backoff_and_retry_eventually_converges(tmp_path):
    _engine, Session = _database(tmp_path)
    db = Session()
    store = FakeSnapshotStore(failures=1)
    service, snapshot, path = _registered_snapshot(db, tmp_path, store)
    due = datetime(2026, 8, 23, 0, 0, 0)
    assert service.mark_orphan(snapshot, cleanup_after=due) is True
    db.commit()

    first = service.cleanup_orphan(snapshot.id, now=due)
    too_early = service.cleanup_orphan(snapshot.id, now=due + timedelta(seconds=30))
    retry = service.cleanup_orphan(snapshot.id, now=due + timedelta(seconds=61))

    assert first.outcome == "retry_scheduled"
    assert first.cleanup_attempts == 1
    assert too_early.outcome == "not_due"
    assert retry.outcome == "deleted"
    assert len(store.calls) == 2
    assert not path.exists()
    deleted = db.get(CodeSourceSnapshot, snapshot.id)
    assert deleted.status == "deleted"
    assert deleted.storage_path == ""
    assert deleted.cleanup_error == ""


def test_replacing_manifest_snapshot_decrements_previous_reference_atomically(tmp_path):
    _engine, Session = _database(tmp_path)
    db = Session()
    service, first, _path = _registered_snapshot(db, tmp_path)
    second_path = tmp_path / "snapshot-second"
    second_path.mkdir()
    second = CodeSourceSnapshot(
        id="f" * 16,
        source_id="source-a",
        resolved_commit="e" * 40,
        content_hash="f" * 64,
        storage_path=str(second_path),
        scan_report_id="scan-a",
        importer_version="1",
        policy_hash="d" * 64,
        status="sealed",
        ref_count=0,
    )
    db.add(second)
    db.commit()
    manifest = db.get(CodeProjectManifest, "manifest-a")
    service.attach_manifest(manifest, first)
    db.commit()
    service.attach_manifest(manifest, second)
    db.commit()

    db.refresh(first)
    db.refresh(second)
    assert first.ref_count == 0
    assert second.ref_count == 1
    assert manifest.snapshot_id == "f" * 16
