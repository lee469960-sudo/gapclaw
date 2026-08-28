"""OpenSpec task 5.1: sealed-snapshot Workspace preparation validation and offline guarantees.

Preparation must materialize the immutable, content-addressed snapshot without
re-cloning the repository, and must fail closed on snapshot hash/commit/identity
or sanitized-Git-metadata drift. It must never touch the Git service (remote
import) or the Secret Store.
"""

from __future__ import annotations

import subprocess
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.services.code_agent.git_importer as git_importer
import app.services.code_agent.secret_store as secret_store
from app.services.code_agent.snapshot_store import SourceSnapshotStore
from app.services.code_agent.workspace import (
    WorkspaceManager,
    WorkspacePreparationError,
    workspace_changed_paths,
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
    (repo / "src").mkdir()
    (repo / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")
    return repo, _git(repo, "rev-parse", "HEAD")


def _seal(tmp_path, repo, commit):
    snapshots = tmp_path / "snapshots"
    snapshots.mkdir()
    store = SourceSnapshotStore(snapshots)
    sealed = store.seal(repo, resolved_commit=commit)
    return store, sealed


def _run(repo, commit, sealed, **overrides):
    values = dict(
        id="run1", repository=str(repo), base_commit=commit,
        resolved_commit=commit, snapshot_id=sealed.snapshot_id, snapshot_hash=sealed.content_hash,
        workspace_path="", source_facts="{}",
        workspace_state="", workspace_downloadable=False,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _claude_run(repo, commit, sealed, **overrides):
    values = dict(
        task_contract=json.dumps({"coding_runtime": "claude_code"}),
        effective_policy=json.dumps({"coding_runtime": "claude_code"}),
    )
    values.update(overrides)
    return _run(repo, commit, sealed, **values)


def test_prepare_materializes_normal_git_worktree(tmp_path):
    repo, commit = _repo(tmp_path)
    store, sealed = _seal(tmp_path, repo, commit)
    run = _run(repo, commit, sealed)
    facts = WorkspaceManager(tmp_path / "runs").prepare(run, snapshot_store=store)
    run_root = Path(facts.path).parent
    workspace = Path(facts.path)
    assert (workspace / ".git").is_dir()
    assert not (workspace / ".git").is_symlink()
    assert (run_root / "source.git").resolve() == (workspace / ".git").resolve()
    assert (workspace / ".git" / "objects" / "info" / "alternates").exists() is False
    assert _git(workspace, "status", "--short") == ""
    assert (workspace / "src" / "app.py").read_text() == "value = 1\n"


def test_claude_code_prepare_flattens_safe_single_top_level_snapshot(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "gamestat" / "src").mkdir(parents=True)
    (repo / "gamestat" / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")
    commit = _git(repo, "rev-parse", "HEAD")
    store, sealed = _seal(tmp_path, repo, commit)
    run = _claude_run(repo, commit, sealed)

    facts = WorkspaceManager(tmp_path / "runs").prepare(run, snapshot_store=store)
    workspace = Path(facts.path)
    run_root = workspace.parent

    assert not (workspace / "gamestat").exists()
    assert (workspace / "src" / "app.py").read_text(encoding="utf-8") == "value = 1\n"
    assert (workspace / ".git").is_dir()
    assert (run_root / "source.git").resolve() == (workspace / ".git").resolve()
    assert Path(_git(workspace, "rev-parse", "--show-toplevel")).resolve() == workspace.resolve()
    assert _git(workspace, "status", "--short") is not None
    source_facts = json.loads(run.source_facts)
    assert source_facts["repo_root_mode"] == "single_top_level_flattened"
    assert source_facts["repo_root_ready"] is True
    assert source_facts["repo_root_reason"] == "ready"
    assert workspace_changed_paths(run) == ()


def test_claude_code_prepare_preserves_multi_top_level_snapshot(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "models").mkdir()
    (repo / "macros").mkdir()
    (repo / "models" / "model.sql").write_text("select 1\n", encoding="utf-8")
    (repo / "macros" / "macro.sql").write_text("{% macro x() %}{% endmacro %}\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")
    commit = _git(repo, "rev-parse", "HEAD")
    store, sealed = _seal(tmp_path, repo, commit)
    run = _claude_run(repo, commit, sealed)

    facts = WorkspaceManager(tmp_path / "runs").prepare(run, snapshot_store=store)
    workspace = Path(facts.path)

    assert (workspace / "models" / "model.sql").is_file()
    assert (workspace / "macros" / "macro.sql").is_file()
    assert json.loads(run.source_facts)["repo_root_mode"] == "preserved"
    assert Path(_git(workspace, "rev-parse", "--show-toplevel")).resolve() == workspace.resolve()


def test_claude_code_prepare_does_not_migrate_historical_workspace(tmp_path):
    repo, commit = _repo(tmp_path)
    store, sealed = _seal(tmp_path, repo, commit)
    runs_root = tmp_path / "runs"
    historical_workspace = runs_root / "old-run" / "workspace" / "gamestat"
    historical_workspace.mkdir(parents=True)
    (historical_workspace / "legacy.txt").write_text("unchanged\n", encoding="utf-8")
    run = _claude_run(repo, commit, sealed, id="new-run")

    WorkspaceManager(runs_root).prepare(run, snapshot_store=store)

    assert (historical_workspace / "legacy.txt").read_text(encoding="utf-8") == "unchanged\n"


def test_git_metadata_changes_are_excluded_from_workspace_changed_paths(tmp_path):
    repo, commit = _repo(tmp_path)
    store, sealed = _seal(tmp_path, repo, commit)
    run = _run(repo, commit, sealed)
    facts = WorkspaceManager(tmp_path / "runs").prepare(run, snapshot_store=store)
    git_runtime_file = Path(facts.path) / ".git" / "code-agent-runtime-state"
    git_runtime_file.write_text("changed\n", encoding="utf-8")
    assert workspace_changed_paths(run) == ()


def test_prepare_rejects_snapshot_hash_mismatch(tmp_path):
    repo, commit = _repo(tmp_path)
    store, sealed = _seal(tmp_path, repo, commit)
    run = _run(repo, commit, sealed, snapshot_hash="f" * 64, snapshot_id="f" * 16)
    with pytest.raises(WorkspacePreparationError, match="workspace_snapshot_invalid"):
        WorkspaceManager(tmp_path / "runs").prepare(run, snapshot_store=store)


def test_prepare_rejects_commit_mismatch(tmp_path):
    repo, commit = _repo(tmp_path)
    store, sealed = _seal(tmp_path, repo, commit)
    run = _run(repo, commit, sealed, resolved_commit="e" * 40)
    with pytest.raises(WorkspacePreparationError, match="workspace_snapshot_invalid"):
        WorkspaceManager(tmp_path / "runs").prepare(run, snapshot_store=store)


def test_prepare_rejects_run_identity_mismatch(tmp_path):
    repo, commit = _repo(tmp_path)
    store, sealed = _seal(tmp_path, repo, commit)
    run = _run(repo, commit, sealed, snapshot_id="wrong-id")
    with pytest.raises(WorkspacePreparationError, match="workspace_snapshot_identity_invalid"):
        WorkspaceManager(tmp_path / "runs").prepare(run, snapshot_store=store)


def test_prepare_rejects_malformed_commit(tmp_path):
    repo, commit = _repo(tmp_path)
    store, sealed = _seal(tmp_path, repo, commit)
    run = _run(repo, commit, sealed, resolved_commit="not-a-sha")
    with pytest.raises(WorkspacePreparationError, match="workspace_snapshot_identity_invalid"):
        WorkspaceManager(tmp_path / "runs").prepare(run, snapshot_store=store)


def test_prepare_rejects_tampered_snapshot_git_metadata(tmp_path):
    repo, commit = _repo(tmp_path)
    store, sealed = _seal(tmp_path, repo, commit)
    snapshot_root = store.root / sealed.content_hash
    hooks = snapshot_root / ".git" / "hooks"
    hooks.chmod(0o700)
    (hooks / "pre-commit").write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    run = _run(repo, commit, sealed)
    with pytest.raises(WorkspacePreparationError, match="workspace_snapshot_invalid"):
        WorkspaceManager(tmp_path / "runs").prepare(run, snapshot_store=store)


def test_prepare_is_fully_offline_and_never_touches_git_service_or_secret_store(tmp_path, monkeypatch):
    repo, commit = _repo(tmp_path)
    store, sealed = _seal(tmp_path, repo, commit)
    run = _run(repo, commit, sealed)

    def _offline(*args, **kwargs):
        raise AssertionError("network/subprocess access during snapshot materialization")

    monkeypatch.setattr(subprocess, "run", _offline)
    monkeypatch.setattr(git_importer.RestrictedGitImporter, "import_remote", _offline)
    monkeypatch.setattr(git_importer.RestrictedGitImporter, "import_from_staging", _offline)
    monkeypatch.setattr(secret_store.DeployTokenSecretStore, "resolve_for_importer", _offline)

    facts = WorkspaceManager(tmp_path / "runs").prepare(run, snapshot_store=store)
    assert (Path(facts.path) / "src" / "app.py").read_text() == "value = 1\n"
