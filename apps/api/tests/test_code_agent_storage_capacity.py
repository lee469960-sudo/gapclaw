"""Storage metrics, low-water admission and safe reclaim ordering."""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import (
    CodeAgentRun,
    CodeProjectManifest,
    CodeSourceSnapshot,
)
from app.services.code_agent.kill_switch import (
    CodeKillSwitchError,
    enforce_code_kill_switches,
    set_code_kill_switch,
)
from app.services.code_agent.storage_capacity import (
    StorageCapacityService,
    enforce_storage_admission,
)
import app.services.code_agent.storage_capacity as capacity_module


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _settings(tmp_path, **overrides):
    values = {
        "data_dir": str(tmp_path),
        "code_snapshot_capacity_bytes": 1_000,
        "code_workspace_capacity_bytes": 1_000,
        "code_storage_low_watermark_bytes": 100,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _run(db, workspace, *, run_id="run1", retained_until=""):
    row = CodeAgentRun(
        id=run_id,
        agent_id="agent1",
        project_id="project1",
        manifest_id="manifest1",
        manifest_version=1,
        status="execution_completed",
        workspace_path=str(workspace),
        workspace_state="retained_read_only",
        retained_until=retained_until,
        cleanup_state="completed",
        runner_state="removed",
    )
    db.add(row)
    db.commit()
    return row


def _snapshot(snapshot_id, *, size_bytes, ref_count=0):
    return CodeSourceSnapshot(
        id=snapshot_id,
        source_id="source1",
        resolved_commit="a" * 40,
        content_hash=(snapshot_id[0] * 64),
        storage_path=f"/snapshots/{snapshot_id}",
        scan_report_id="scan1",
        importer_version="1",
        policy_hash="b" * 64,
        status="sealed",
        ref_count=ref_count,
        size_bytes=size_bytes,
        created_at="2026-08-01 00:00:00",
    )


def test_snapshot_workspace_and_disk_capacity_metrics_are_separate(tmp_path, monkeypatch):
    db = _db()
    workspace = tmp_path / "runs" / "run1" / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "payload.bin").write_bytes(b"x" * 25)
    _run(db, workspace)
    db.add(_snapshot("c" * 16, size_bytes=75))
    db.commit()
    monkeypatch.setattr(
        capacity_module.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(total=1_000, used=850, free=150),
    )

    metrics = StorageCapacityService(db, settings=_settings(tmp_path)).metrics()

    assert metrics.snapshot_bytes == 75
    assert metrics.workspace_bytes == 25
    assert metrics.free_bytes == 150
    assert metrics.admission_allowed is True


@pytest.mark.parametrize(
    "overrides",
    [
        {"code_snapshot_capacity_bytes": 50},
        {"code_workspace_capacity_bytes": 20},
        {"code_storage_low_watermark_bytes": 200},
    ],
)
def test_each_capacity_boundary_activates_storage_admission_kill_switch(
    tmp_path, monkeypatch, overrides
):
    db = _db()
    workspace = tmp_path / "runs" / "run1" / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "payload.bin").write_bytes(b"x" * 25)
    _run(db, workspace)
    db.add(_snapshot("c" * 16, size_bytes=75))
    db.commit()
    monkeypatch.setattr(
        capacity_module.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(total=1_000, used=850, free=150),
    )

    with pytest.raises(CodeKillSwitchError, match="code_kill_switch_storage_capacity"):
        enforce_storage_admission(db, settings=_settings(tmp_path, **overrides))


def test_manual_storage_kill_switch_blocks_new_admission():
    db = _db()
    set_code_kill_switch(db, scope="storage", target="capacity")
    db.commit()

    with pytest.raises(CodeKillSwitchError, match="code_kill_switch_storage"):
        enforce_code_kill_switches(
            db,
            project_id="project1",
            repository="repo",
            tools=["read"],
            image="image",
            model="model",
        )


def test_reclaim_orders_expired_workspace_before_only_unreferenced_snapshot(tmp_path):
    db = _db()
    workspace = tmp_path / "runs" / "run1" / "workspace"
    workspace.mkdir(parents=True)
    due = (datetime(2026, 8, 24, 10, 0, 0) - timedelta(seconds=1)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    _run(db, workspace, retained_until=due)
    protected = _snapshot("d" * 16, size_bytes=100, ref_count=1)
    orphan = _snapshot("e" * 16, size_bytes=100, ref_count=0)
    db.add_all([protected, orphan])
    db.add(CodeProjectManifest(
        id="manifest1",
        project_id="project1",
        version=1,
        status="published",
        snapshot_id=protected.id,
    ))
    db.commit()
    calls = []

    class FakeJanitor:
        def cleanup_run(self, run, *, now):
            calls.append(f"workspace:{run.id}")
            return SimpleNamespace(outcome="completed")

    class FakeSnapshotLifecycle:
        def mark_orphan(self, snapshot, *, cleanup_after):
            calls.append(f"mark:{snapshot.id}")
            return True

        def cleanup_orphan(self, snapshot_id, *, now):
            calls.append(f"snapshot:{snapshot_id}")
            return SimpleNamespace(outcome="deleted")

    events = StorageCapacityService(
        db,
        settings=_settings(tmp_path),
        lifecycle_janitor=FakeJanitor(),
        snapshot_lifecycle=FakeSnapshotLifecycle(),
    ).reclaim(now=datetime(2026, 8, 24, 10, 0, 0))

    assert calls == [f"workspace:run1", f"mark:{orphan.id}", f"snapshot:{orphan.id}"]
    assert events == (
        "workspace:run1:completed",
        f"snapshot:{orphan.id}:deleted",
    )
    assert protected.id not in " ".join(calls)
