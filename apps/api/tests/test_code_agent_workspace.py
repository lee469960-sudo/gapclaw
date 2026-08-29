"""OpenSpec task 5.1: snapshot-materialized, fixed-baseline Workspace preparation."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.services.code_agent.workspace as workspace_module
from app.services.code_agent.snapshot_store import SourceSnapshotStore
from app.services.code_agent.workspace import (
    WorkspaceIntegrityError,
    WorkspaceIntegrityGuard,
    WorkspaceManager,
)


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    ).stdout.strip()


def _repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "app.py").write_text("value = 1\n", encoding="utf-8")
    _git(repo, "add", "app.py")
    _git(repo, "commit", "-m", "base")
    return repo, _git(repo, "rev-parse", "HEAD")


def _seal(tmp_path, repo, commit):
    snapshots = tmp_path / "snapshots"
    snapshots.mkdir()
    store = SourceSnapshotStore(snapshots)
    sealed = store.seal(repo, resolved_commit=commit)
    return store, sealed


def _run(repo, commit, sealed, **extra):
    values = dict(
        id="run1", repository=str(repo), base_commit=commit,
        resolved_commit=commit, snapshot_id=sealed.snapshot_id, snapshot_hash=sealed.content_hash,
        workspace_path="", source_facts="{}",
        workspace_state="", workspace_downloadable=False, retained_until="",
    )
    values.update(extra)
    return SimpleNamespace(**values)


def test_workspace_materializes_sealed_snapshot_without_running_hooks(tmp_path):
    repo, commit = _repo(tmp_path)
    marker = tmp_path / "hook-ran"
    hook = repo / ".git" / "hooks" / "post-checkout"
    hook.write_text(f"#!/bin/sh\ntouch {marker}\n", encoding="utf-8")
    hook.chmod(0o755)
    store, sealed = _seal(tmp_path, repo, commit)
    run = _run(repo, commit, sealed)
    facts = WorkspaceManager(tmp_path / "runs").prepare(run, snapshot_store=store)
    assert (tmp_path / "runs" / "run1" / "workspace" / "app.py").read_text() == "value = 1\n"
    assert facts.resolved_commit == commit
    assert facts.hooks_executed is False
    assert facts.submodules_initialized is False
    assert not marker.exists()
    assert json.loads(run.source_facts)["preparation_mode"] == "snapshot_materialize"


def test_concurrent_runs_never_share_writable_workspace(tmp_path):
    repo, commit = _repo(tmp_path)
    store, sealed = _seal(tmp_path, repo, commit)
    manager = WorkspaceManager(tmp_path / "runs")
    run1 = _run(repo, commit, sealed, id="run1")
    run2 = _run(repo, commit, sealed, id="run2")
    first = manager.prepare(run1, snapshot_store=store)
    second = manager.prepare(run2, snapshot_store=store)
    assert first.path != second.path
    (Path(first.path) / "app.py").write_text("changed\n", encoding="utf-8")
    assert (Path(second.path) / "app.py").read_text(encoding="utf-8") == "value = 1\n"


def test_integrity_guard_allows_tracked_write_and_rejects_external_write(tmp_path):
    repo, commit = _repo(tmp_path)
    store, sealed = _seal(tmp_path, repo, commit)
    run = _run(repo, commit, sealed, status="pending", failure_reason="")
    WorkspaceManager(tmp_path / "runs").prepare(run, snapshot_store=store)
    guard = WorkspaceIntegrityGuard(run)
    guard.check_before_write("app.py")
    (Path(run.workspace_path) / "app.py").write_text("expected\n", encoding="utf-8")
    guard.check_before_seal()

    (Path(run.workspace_path) / "external.py").write_text("unexpected\n", encoding="utf-8")
    with pytest.raises(WorkspaceIntegrityError, match="workspace_external_write"):
        guard.check_before_seal()
    assert run.status == "workspace_integrity_error"


def test_integrity_guard_authorizes_managed_runtime_changes_within_allowed_paths(tmp_path):
    repo, commit = _repo(tmp_path)
    store, sealed = _seal(tmp_path, repo, commit)
    run = _run(
        repo, commit, sealed, status="pending", failure_reason="",
        effective_policy=json.dumps({"allowed_paths": ["models/"]}),
    )
    WorkspaceManager(tmp_path / "runs").prepare(run, snapshot_store=store)
    models = Path(run.workspace_path) / "models"
    models.mkdir()
    (models / "model.sql").write_text("select 1\n", encoding="utf-8")

    assert WorkspaceIntegrityGuard(run).authorize_runtime_changes() == ("models/model.sql",)
    assert json.loads((Path(run.workspace_path).parent / "control" / "authorized_writes.json").read_text()) == ["models/model.sql"]


def test_integrity_guard_rejects_frozen_baseline_drift(tmp_path):
    repo, commit = _repo(tmp_path)
    store, sealed = _seal(tmp_path, repo, commit)
    run = _run(repo, commit, sealed, status="pending", failure_reason="")
    WorkspaceManager(tmp_path / "runs").prepare(run, snapshot_store=store)
    run.base_commit = "b" * 40
    with pytest.raises(WorkspaceIntegrityError, match="workspace_baseline_drift"):
        WorkspaceIntegrityGuard(run).check_before_write("app.py")


def test_workspace_cleanup_retains_read_only_non_downloadable_content_then_purges(tmp_path):
    repo, commit = _repo(tmp_path)
    store, sealed = _seal(tmp_path, repo, commit)
    run = _run(repo, commit, sealed, status="pending", failure_reason="")
    manager = WorkspaceManager(tmp_path / "runs", retention_hours=24)
    manager.prepare(run, snapshot_store=store)
    manager.retain_after_run(run)
    workspace = Path(run.workspace_path)
    assert run.workspace_state == "retained_read_only"
    assert manager.downloadable_workspace(run) is None
    assert not (workspace.parent / "source.git").exists()
    assert not (workspace / ".git").exists()
    assert workspace.stat().st_mode & 0o222 == 0
    assert (workspace / "app.py").stat().st_mode & 0o222 == 0

    run.retained_until = (datetime.now() - timedelta(seconds=1)).strftime("%Y-%m-%d %H:%M:%S")
    assert manager.purge_expired(run) is True
    assert run.workspace_state == "deleted"
    assert not workspace.parent.exists()


def test_allocated_sealed_workspace_is_retained_for_run_bound_preview(tmp_path):
    repo, commit = _repo(tmp_path)
    store, sealed = _seal(tmp_path, repo, commit)
    run = _run(repo, commit, sealed, status="patch_ready", failure_reason="patch_ready")
    manager = WorkspaceManager(tmp_path / "runs", retention_hours=24)
    manager.prepare(run, snapshot_store=store)
    run.workspace_state = "sealed"

    manager.cleanup_allocated_workspace(run)

    workspace = Path(run.workspace_path)
    assert run.workspace_state == "retained_read_only"
    assert workspace.is_dir()
    assert (workspace / "app.py").read_text(encoding="utf-8") == "value = 1\n"
    assert not (workspace / ".git").exists()
    assert not (workspace.parent / "source.git").exists()


def test_workspace_manager_default_root_prefers_configured_api_root(tmp_path, monkeypatch):
    api_root = tmp_path / "code-agent" / "runs"
    api_root.mkdir(parents=True)
    monkeypatch.setattr(
        workspace_module,
        "get_settings",
        lambda: SimpleNamespace(
            data_dir=str(tmp_path / "data"),
            code_workspace_api_root=str(api_root),
            code_workspace_retention_hours=168,
        ),
    )

    assert WorkspaceManager().root == api_root.resolve()


def test_workspace_manager_default_root_falls_back_to_hyphenated_path(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(
        workspace_module,
        "get_settings",
        lambda: SimpleNamespace(
            data_dir=str(data_dir),
            code_workspace_api_root="",
            code_workspace_retention_hours=168,
        ),
    )

    manager = WorkspaceManager()
    assert manager.root == (data_dir / "code-agent" / "runs").resolve()
    assert "code_agent" not in manager.root.parts


def test_prepare_materializes_workspace_under_configured_api_root(tmp_path, monkeypatch):
    api_root = tmp_path / "code-agent" / "runs"
    api_root.mkdir(parents=True)
    monkeypatch.setattr(
        workspace_module,
        "get_settings",
        lambda: SimpleNamespace(
            data_dir=str(tmp_path / "data"),
            code_workspace_api_root=str(api_root),
            code_workspace_retention_hours=168,
        ),
    )
    repo, commit = _repo(tmp_path)
    store, sealed = _seal(tmp_path, repo, commit)
    run = _run(repo, commit, sealed)

    facts = WorkspaceManager().prepare(run, snapshot_store=store)

    expected = api_root / "run1" / "workspace"
    assert Path(facts.path).resolve() == expected.resolve()
    assert expected.exists()
    assert not (tmp_path / "data" / "code_agent").exists()
