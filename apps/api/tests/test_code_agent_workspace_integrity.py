"""OpenSpec task 5.2: per-run workspace isolation + extended integrity facts.

The integrity guard must fail closed (``workspace_integrity_error``) on snapshot
identity drift, cross-run mount/identity mismatch and uncontrolled checkout of the
sanitized ``source.git``, in addition to the existing baseline-drift and external
write checks. Each failure blocks tools and patch delivery.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

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
        workspace_path="", source_facts="{}", status="pending", failure_reason="",
        workspace_state="prepared", workspace_downloadable=False, retained_until="",
    )
    values.update(extra)
    return SimpleNamespace(**values)


def test_snapshot_identity_drift_blocks_tools_and_patch(tmp_path):
    repo, commit = _repo(tmp_path)
    store, sealed = _seal(tmp_path, repo, commit)
    run = _run(repo, commit, sealed)
    WorkspaceManager(tmp_path / "runs").prepare(run, snapshot_store=store)
    # Simulate the frozen run contract drifting from the recorded facts.
    run.snapshot_hash = "f" * 64

    guard = WorkspaceIntegrityGuard(run)
    with pytest.raises(WorkspaceIntegrityError, match="workspace_snapshot_identity_invalid"):
        guard.check_before_write("app.py")
    assert run.status == "workspace_integrity_error"


def test_cross_run_mount_detection_blocks_tools_and_patch(tmp_path):
    repo, commit = _repo(tmp_path)
    store, sealed = _seal(tmp_path, repo, commit)
    manager = WorkspaceManager(tmp_path / "runs")
    run1 = _run(repo, commit, sealed, id="run1")
    run2 = _run(repo, commit, sealed, id="run2")
    manager.prepare(run1, snapshot_store=store)
    manager.prepare(run2, snapshot_store=store)
    # A run must never operate through another run's writable workspace.
    run1.workspace_path = run2.workspace_path

    with pytest.raises(WorkspaceIntegrityError, match="workspace_cross_run_mount"):
        WorkspaceIntegrityGuard(run1).check_before_write("app.py")
    assert run1.status == "workspace_integrity_error"


def test_recorded_path_identity_mismatch_is_rejected(tmp_path):
    repo, commit = _repo(tmp_path)
    store, sealed = _seal(tmp_path, repo, commit)
    run = _run(repo, commit, sealed)
    manager = WorkspaceManager(tmp_path / "runs")
    manager.prepare(run, snapshot_store=store)
    facts = json.loads(run.source_facts)
    facts["path"] = str(tmp_path / "runs" / "other" / "workspace")
    run.source_facts = json.dumps(facts, sort_keys=True)

    with pytest.raises(WorkspaceIntegrityError, match="workspace_cross_run_mount"):
        WorkspaceIntegrityGuard(run).check_before_seal()
    assert run.status == "workspace_integrity_error"


def test_uncontrolled_head_checkout_is_rejected(tmp_path):
    repo, commit = _repo(tmp_path)
    store, sealed = _seal(tmp_path, repo, commit)
    run = _run(repo, commit, sealed)
    manager = WorkspaceManager(tmp_path / "runs")
    manager.prepare(run, snapshot_store=store)
    source_git = Path(run.workspace_path).parent / "source.git"
    (source_git / "HEAD").write_text("ref: refs/heads/detached\n", encoding="utf-8")

    with pytest.raises(WorkspaceIntegrityError, match="workspace_uncontrolled_checkout"):
        WorkspaceIntegrityGuard(run).check_before_write("app.py")
    assert run.status == "workspace_integrity_error"


def test_uncontrolled_ref_move_is_rejected(tmp_path):
    repo, commit = _repo(tmp_path)
    store, sealed = _seal(tmp_path, repo, commit)
    run = _run(repo, commit, sealed)
    manager = WorkspaceManager(tmp_path / "runs")
    manager.prepare(run, snapshot_store=store)
    source_git = Path(run.workspace_path).parent / "source.git"
    (source_git / "refs" / "heads" / "snapshot").write_text("e" * 40 + "\n", encoding="utf-8")

    with pytest.raises(WorkspaceIntegrityError, match="workspace_uncontrolled_checkout"):
        WorkspaceIntegrityGuard(run).check_before_seal()
    assert run.status == "workspace_integrity_error"


def test_integrity_failure_never_marks_workspace_downloadable(tmp_path):
    repo, commit = _repo(tmp_path)
    store, sealed = _seal(tmp_path, repo, commit)
    run = _run(repo, commit, sealed)
    WorkspaceManager(tmp_path / "runs").prepare(run, snapshot_store=store)
    run.snapshot_id = "wrong-id"

    with pytest.raises(WorkspaceIntegrityError, match="workspace_snapshot_identity_invalid"):
        WorkspaceIntegrityGuard(run).check_before_seal()
    assert run.status == "workspace_integrity_error"
    assert run.workspace_downloadable is False
