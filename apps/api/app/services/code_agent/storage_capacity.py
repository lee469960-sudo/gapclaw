"""CodeAgent storage metrics, low-water admission and safe reclaim ordering."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy import func

from app.config import get_settings
from app.models import CodeAgentRun, CodeProjectManifest, CodeSourceSnapshot
from app.services.code_agent.kill_switch import CodeKillSwitchError


@dataclass(frozen=True)
class StorageCapacityMetrics:
    snapshot_bytes: int
    workspace_bytes: int
    free_bytes: int
    snapshot_capacity_bytes: int
    workspace_capacity_bytes: int
    low_watermark_bytes: int

    @property
    def admission_allowed(self) -> bool:
        return (
            self.snapshot_bytes <= self.snapshot_capacity_bytes
            and self.workspace_bytes <= self.workspace_capacity_bytes
            and self.free_bytes >= self.low_watermark_bytes
        )


def _tree_size(root: Path) -> int:
    total = 0
    pending = [root]
    while pending:
        current = pending.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            pending.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            total += entry.stat(follow_symlinks=False).st_size
                    except FileNotFoundError:
                        continue
        except FileNotFoundError:
            continue
    return total


class StorageCapacityService:
    def __init__(
        self,
        db,
        *,
        settings=None,
        lifecycle_janitor=None,
        snapshot_lifecycle=None,
    ):
        self.db = db
        self.settings = settings or get_settings()
        self.lifecycle_janitor = lifecycle_janitor
        self.snapshot_lifecycle = snapshot_lifecycle

    def metrics(self) -> StorageCapacityMetrics:
        snapshot_bytes = int(
            self.db.query(func.sum(CodeSourceSnapshot.size_bytes))
            .filter(CodeSourceSnapshot.status != "deleted")
            .scalar()
            or 0
        )
        roots = set()
        for (workspace_path,) in self.db.query(CodeAgentRun.workspace_path).filter(
            CodeAgentRun.workspace_path != ""
        ):
            path = Path(workspace_path)
            if path.name == "workspace":
                roots.add(path.parent)
        workspace_bytes = sum(_tree_size(root) for root in roots)
        data_root = Path(getattr(self.settings, "data_dir", "/tmp"))
        free_bytes = int(shutil.disk_usage(data_root).free)
        return StorageCapacityMetrics(
            snapshot_bytes=snapshot_bytes,
            workspace_bytes=workspace_bytes,
            free_bytes=free_bytes,
            snapshot_capacity_bytes=max(0, int(getattr(
                self.settings, "code_snapshot_capacity_bytes", 53_687_091_200
            ))),
            workspace_capacity_bytes=max(0, int(getattr(
                self.settings, "code_workspace_capacity_bytes", 21_474_836_480
            ))),
            low_watermark_bytes=max(0, int(getattr(
                self.settings, "code_storage_low_watermark_bytes", 0
            ))),
        )

    def reclaim(self, *, now: datetime | None = None) -> tuple[str, ...]:
        """Reclaim expired per-run state before any unreferenced source snapshot."""
        current = now or datetime.now()
        events = []
        janitor = self.lifecycle_janitor
        if janitor is None:
            from app.services.code_agent.lifecycle_janitor import CodeLifecycleJanitor

            janitor = CodeLifecycleJanitor(self.db)
        retained = self.db.query(CodeAgentRun).filter(
            CodeAgentRun.workspace_state == "retained_read_only",
            CodeAgentRun.retained_until != "",
            CodeAgentRun.retained_until <= current.strftime("%Y-%m-%d %H:%M:%S"),
        ).order_by(CodeAgentRun.retained_until, CodeAgentRun.id).all()
        for run in retained:
            result = janitor.cleanup_run(run, now=current)
            events.append(f"workspace:{run.id}:{result.outcome}")

        snapshot_lifecycle = self.snapshot_lifecycle
        if snapshot_lifecycle is None:
            from app.services.code_agent.snapshot_lifecycle import SnapshotLifecycleService
            from app.services.code_agent.snapshot_store import SourceSnapshotStore

            store_root = Path(getattr(self.settings, "data_dir", "/tmp")) / (
                "code-agent-source/snapshots"
            )
            store_root.mkdir(parents=True, exist_ok=True)
            snapshot_lifecycle = SnapshotLifecycleService(
                self.db, SourceSnapshotStore(store_root)
            )
        referenced_ids = {
            value for (value,) in self.db.query(CodeProjectManifest.snapshot_id).filter(
                CodeProjectManifest.snapshot_id != ""
            )
        }
        candidates = self.db.query(CodeSourceSnapshot).filter(
            CodeSourceSnapshot.ref_count == 0,
            CodeSourceSnapshot.status.in_(("sealed", "failed")),
        ).order_by(CodeSourceSnapshot.created_at, CodeSourceSnapshot.id).all()
        for snapshot in candidates:
            if snapshot.id in referenced_ids:
                continue
            snapshot_lifecycle.mark_orphan(snapshot, cleanup_after=current)
            self.db.commit()
            result = snapshot_lifecycle.cleanup_orphan(snapshot.id, now=current)
            events.append(f"snapshot:{snapshot.id}:{result.outcome}")
        return tuple(events)


def enforce_storage_admission(db, *, settings=None) -> StorageCapacityMetrics:
    metrics = StorageCapacityService(db, settings=settings).metrics()
    if not metrics.admission_allowed:
        raise CodeKillSwitchError("code_kill_switch_storage_capacity")
    return metrics
