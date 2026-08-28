"""Crash recovery and retry convergence for persistent CodeAgent cleanup."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import CodeAgentRun, CodeControlAudit
from app.services.code_agent.lifecycle_janitor import CodeLifecycleJanitor
from app.services.code_agent.workspace import WorkspaceManager
import app.services.code_agent.workspace as workspace_module


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _run(db, root, *, run_id="run1", status="running", container_id="container1"):
    workspace = root / run_id / "workspace"
    control = root / run_id / "control"
    workspace.mkdir(parents=True)
    control.mkdir()
    (workspace / "app.py").write_text("value = 1\n", encoding="utf-8")
    row = CodeAgentRun(
        id=run_id,
        agent_id="agent1",
        project_id="project1",
        manifest_id="manifest1",
        manifest_version=1,
        status=status,
        workspace_path=str(workspace),
        workspace_state="prepared",
        workspace_retention_hours=168,
        workspace_downloadable=False,
        container_id=container_id,
        runner_state="active" if container_id else "removed",
        execution_eligible=status in {"pending", "running"},
        cleanup_state="pending" if status in {"pending", "running"} else "failed",
        cleanup_attempts=0,
        cleanup_error="",
        cleanup_next_attempt="",
        effective_policy_hash="a" * 64,
    )
    db.add(row)
    db.commit()
    return row, workspace


def _container(run_id="run1"):
    return SimpleNamespace(
        id=f"container-{run_id}",
        name=f"code-agent-{run_id}",
        attrs={"Config": {"Labels": {"code_agent.run_id": run_id}}},
        remove=MagicMock(),
    )


def test_startup_recovery_revokes_crashed_run_and_is_idempotent(tmp_path):
    db = _db()
    root = tmp_path / "runs"
    run, workspace = _run(db, root)
    other_root = root / "other-run"
    other_root.mkdir()
    (other_root / "keep.txt").write_text("keep\n", encoding="utf-8")
    container = _container()
    client = MagicMock()
    client.containers.get.return_value = container
    janitor = CodeLifecycleJanitor(
        db,
        workspace_manager=WorkspaceManager(root, retention_hours=168),
        docker_client=client,
    )

    first = janitor.run_due(startup_recovery=True)
    second = janitor.run_due(startup_recovery=False)

    db.refresh(run)
    assert [item.outcome for item in first] == ["completed"]
    assert second == ()
    assert run.status == "infrastructure_error"
    assert run.execution_eligible is False
    assert run.runner_state == "removed"
    assert run.cleanup_state == "completed"
    assert run.container_id == ""
    assert run.workspace_state == "retained_read_only"
    assert workspace.exists()
    assert workspace.stat().st_mode & 0o222 == 0
    assert (other_root / "keep.txt").read_text(encoding="utf-8") == "keep\n"
    container.remove.assert_called_once_with(force=True)


def test_docker_delete_failure_retries_then_converges(tmp_path):
    db = _db()
    root = tmp_path / "runs"
    run, _workspace = _run(db, root, status="infrastructure_error")
    container = _container()
    container.remove.side_effect = [RuntimeError("daemon failed"), None]
    client = MagicMock()
    client.containers.get.return_value = container
    janitor = CodeLifecycleJanitor(
        db,
        workspace_manager=WorkspaceManager(root, retention_hours=168),
        docker_client=client,
    )
    current = datetime(2026, 8, 24, 10, 0, 0)

    first = janitor.cleanup_run(run, now=current)
    db.refresh(run)
    assert first.outcome == "retry_scheduled"
    assert run.cleanup_attempts == 1
    assert run.cleanup_error == "sandbox_cleanup_failed"
    assert run.cleanup_next_attempt == "2026-08-24 10:01:00"
    assert run.workspace_state == "prepared"
    alert = db.query(CodeControlAudit).one()
    assert json.loads(alert.details)["facts"] == {
        "cleanup_state": "failed",
        "resource_type": "runner",
        "retry_count": 1,
    }

    second = janitor.cleanup_run(run, now=current + timedelta(seconds=61))
    db.refresh(run)
    assert second.outcome == "completed"
    assert run.cleanup_state == "completed"
    assert run.cleanup_error == ""
    assert run.cleanup_next_attempt == ""
    assert run.workspace_state == "retained_read_only"
    assert container.remove.call_count == 2


def test_workspace_delete_failure_retries_then_converges(tmp_path, monkeypatch):
    db = _db()
    root = tmp_path / "runs"
    run, _workspace = _run(
        db, root, status="infrastructure_error", container_id=""
    )
    run.workspace_retention_hours = 0
    db.commit()
    manager = WorkspaceManager(root, retention_hours=168)
    real_rmtree = workspace_module.shutil.rmtree
    attempts = 0

    def _flaky_rmtree(path, *args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("filesystem busy")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(workspace_module.shutil, "rmtree", _flaky_rmtree)
    janitor = CodeLifecycleJanitor(
        db, workspace_manager=manager, docker_client=MagicMock()
    )
    current = datetime(2026, 8, 24, 11, 0, 0)

    first = janitor.cleanup_run(run, now=current)
    db.refresh(run)
    assert first.outcome == "retry_scheduled"
    assert run.cleanup_attempts == 1
    assert run.cleanup_error == "workspace_cleanup_failed"
    assert json.loads(db.query(CodeControlAudit).one().details)["facts"][
        "resource_type"
    ] == "workspace"

    second = janitor.cleanup_run(run, now=current + timedelta(seconds=61))
    db.refresh(run)
    assert second.outcome == "completed"
    assert run.workspace_state == "deleted"
    assert run.cleanup_state == "completed"


def test_binding_mismatch_never_deletes_another_runs_container(tmp_path):
    db = _db()
    root = tmp_path / "runs"
    run, _workspace = _run(db, root, status="infrastructure_error")
    other = _container("run2")
    client = MagicMock()
    client.containers.get.return_value = other
    janitor = CodeLifecycleJanitor(
        db,
        workspace_manager=WorkspaceManager(root, retention_hours=168),
        docker_client=client,
    )

    result = janitor.cleanup_run(run, now=datetime(2026, 8, 24, 12, 0, 0))

    assert result.outcome == "retry_scheduled"
    other.remove.assert_not_called()
    db.refresh(run)
    assert run.container_id == "container1"
    assert run.cleanup_state == "failed"
