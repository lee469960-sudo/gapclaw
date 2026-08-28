"""Workspace retention policy, permissions and non-executable boundary tests."""

from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import CodeProject
from app.routers.code_project import CodeProjectBody, code_project_post
import app.services.code_agent.workspace as workspace_module
from app.services.code_agent.runner import (
    CodeContainerRunner,
    RunnerPolicyError,
)
from app.services.code_agent.workspace import (
    WorkspaceManager,
    effective_workspace_retention_hours,
)


def _retained_subject(tmp_path, *, retention_hours=168):
    root = tmp_path / "runs"
    workspace = root / "run1" / "workspace"
    control = root / "run1" / "control"
    source = root / "run1" / "source.git"
    workspace.mkdir(parents=True)
    control.mkdir()
    source.mkdir()
    (workspace / "app.py").write_text("value = 1\n", encoding="utf-8")
    (control / "state.json").write_text("{}", encoding="utf-8")
    run = SimpleNamespace(
        id="run1",
        workspace_path=str(workspace),
        workspace_state="prepared",
        workspace_retention_hours=retention_hours,
        retained_until="",
        workspace_downloadable=False,
        artifact_id="artifact1",
        tool_audit='[{"status":"completed"}]',
    )
    return WorkspaceManager(root, retention_hours=168), run, workspace


def test_default_and_project_retention_choose_only_the_shorter_limit():
    settings = SimpleNamespace(code_workspace_retention_hours=168)
    assert effective_workspace_retention_hours(
        SimpleNamespace(workspace_retention_hours=None), settings
    ) == 168
    assert effective_workspace_retention_hours(
        SimpleNamespace(workspace_retention_hours=24), settings
    ) == 24
    assert effective_workspace_retention_hours(
        SimpleNamespace(workspace_retention_hours=0), settings
    ) == 0
    assert effective_workspace_retention_hours(
        SimpleNamespace(workspace_retention_hours=720), settings
    ) == 168


def test_workspace_manager_uses_platform_168_hour_default(tmp_path, monkeypatch):
    monkeypatch.setattr(
        workspace_module,
        "get_settings",
        lambda: SimpleNamespace(
            data_dir=str(tmp_path), code_workspace_retention_hours=168
        ),
    )

    assert WorkspaceManager().retention_hours == 168


def test_retained_workspace_is_168_hour_read_only_and_independent_of_records(tmp_path):
    manager, run, workspace = _retained_subject(tmp_path)
    before = datetime.now()

    manager.retain_after_run(run)

    expires = datetime.strptime(run.retained_until, "%Y-%m-%d %H:%M:%S")
    assert 167.9 < (expires - before).total_seconds() / 3600 <= 168
    assert run.workspace_state == "retained_read_only"
    assert manager.downloadable_workspace(run) is None
    assert not (workspace.parent / "source.git").exists()
    assert not (workspace / ".git").exists()
    assert all(
        path.is_symlink() or path.stat().st_mode & 0o222 == 0
        for path in [workspace.parent, *workspace.parent.rglob("*")]
    )
    assert run.artifact_id == "artifact1"
    assert run.tool_audit == '[{"status":"completed"}]'


def test_zero_retention_deletes_workspace_without_deleting_artifact_or_audit(tmp_path):
    manager, run, workspace = _retained_subject(tmp_path, retention_hours=0)

    manager.retain_after_run(run)

    assert run.workspace_state == "deleted"
    assert run.workspace_path == ""
    assert run.retained_until == ""
    assert not workspace.parent.exists()
    assert run.artifact_id == "artifact1"
    assert run.tool_audit == '[{"status":"completed"}]'


def test_retained_workspace_cannot_be_remounted_by_runner(tmp_path):
    manager, run, workspace = _retained_subject(tmp_path)
    manager.retain_after_run(run)
    run.snapshot_hash = "a" * 64
    run.effective_policy = "{}"
    run.image = "runner:test"
    run.image_digest = "sha256:" + "b" * 64
    client = MagicMock()

    with pytest.raises(RunnerPolicyError, match="runner_workspace_unavailable"):
        CodeContainerRunner(client).start(run, SimpleNamespace(path=str(workspace)))

    client.containers.run.assert_not_called()


def test_only_project_owner_can_set_shorter_or_immediate_retention(monkeypatch):
    monkeypatch.setattr(
        "app.routers.code_project.get_settings",
        lambda: SimpleNamespace(code_workspace_retention_hours=168),
    )
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    page = '["/pages/page_code_project.cgi"]'
    owner = SimpleNamespace(
        username="alice", roles='["owner"]', permissions=page,
        organization_id="default",
    )
    operator = SimpleNamespace(
        username="bob", roles='["operator"]', permissions=page,
        organization_id="default",
    )
    created = asyncio.run(code_project_post(
        CodeProjectBody(
            action="create", name="Project", allowed_users=["bob"],
            workspace_retention_hours=24,
        ),
        owner,
        db,
    ))
    project_id = created["data"]["id"]
    assert created["data"]["workspace_retention_hours"] == 24

    denied = asyncio.run(code_project_post(
        CodeProjectBody(
            action="update", id=project_id, workspace_retention_hours=0,
        ),
        operator,
        db,
    ))
    assert denied["msg"] == "code_project_not_found"
    assert db.get(CodeProject, project_id).workspace_retention_hours == 24

    updated = asyncio.run(code_project_post(
        CodeProjectBody(
            action="update", id=project_id, workspace_retention_hours=0,
        ),
        owner,
        db,
    ))
    assert updated["data"]["workspace_retention_hours"] == 0

    too_long = asyncio.run(code_project_post(
        CodeProjectBody(
            action="update", id=project_id, workspace_retention_hours=169,
        ),
        owner,
        db,
    ))
    assert too_long["msg"] == "project_workspace_retention_exceeds_platform"
    db.close()
