"""Real Docker daemon bind-path release gate for the CodeAgent runner."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import CodeAgentRun
from app.services.code_agent.lifecycle_janitor import CodeLifecycleJanitor
from app.services.code_agent.lifecycle import cleanup_code_resources
from app.services.code_agent.runner import (
    CodeContainerRunner,
    RunnerPolicyError,
    RunnerUnavailableError,
)
from app.services.code_agent.workspace_mount import write_workspace_sentinel
from app.services.code_agent.workspace import WorkspaceManager
import app.services.code_agent.workspace_mount as workspace_mount


IMAGE = os.getenv("CODE_AGENT_TEST_IMAGE", "python:3.12-slim")
IMAGE_DIGEST = os.getenv(
    "CODE_AGENT_TEST_IMAGE_DIGEST",
    "sha256:229a2c5bfa27522db7815ea81f9bed70af17ccb9de9fc7ad142b1877b5830d36",
)


def _docker_client():
    try:
        import docker

        client = docker.from_env()
        client.ping()
        client.images.get(f"{IMAGE}@{IMAGE_DIGEST}")
        return client
    except Exception as exc:
        pytest.skip(f"real Docker daemon/image unavailable: {type(exc).__name__}")


def _subject(tmp_path, monkeypatch, *, host_root: Path | None = None):
    api_root = tmp_path / "api-root"
    run_root = api_root / "run1"
    workspace = run_root / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "sentinel.txt").write_text("api-before\n", encoding="utf-8")
    snapshot_hash = "a" * 64
    write_workspace_sentinel(run_root, "run1", snapshot_hash)
    monkeypatch.setattr(
        workspace_mount,
        "get_settings",
        lambda: SimpleNamespace(
            code_workspace_api_root=str(api_root),
            code_workspace_host_root=str(host_root or api_root),
        ),
    )
    run = SimpleNamespace(
        id="run1",
        status="pending",
        workspace_state="prepared",
        snapshot_hash=snapshot_hash,
        effective_policy=json.dumps({
            "budgets": {
                "cpu_count": 1,
                "memory_mb": 256,
                "pids_limit": 64,
                "tmpfs_mb": 32,
                "disk_mb": 128,
                "timeout_seconds": 60,
                "output_limit_bytes": 10_000,
            }
        }),
        image=IMAGE,
        image_digest=IMAGE_DIGEST,
        container_id="",
        runner_facts="{}",
    )
    return run, SimpleNamespace(path=str(workspace)), workspace


def test_real_daemon_reads_and_modifies_the_exact_api_workspace(tmp_path, monkeypatch):
    client = _docker_client()
    run, facts, workspace = _subject(tmp_path, monkeypatch)
    runner = CodeContainerRunner(client)

    started = runner.start(run, facts)
    code, output = runner.exec(
        started.container_id,
        "printf 'runner-after\\n' > sentinel.txt && cat sentinel.txt",
        timeout_seconds=10,
    )

    assert code == 0
    assert output == "runner-after\n"
    assert (workspace / "sentinel.txt").read_text(encoding="utf-8") == "runner-after\n"
    container = client.containers.get(started.container_id)
    attrs = container.attrs
    assert attrs["HostConfig"]["NetworkMode"] == "none"
    assert attrs["HostConfig"]["Privileged"] is False
    assert all(mount["Source"] != "/var/run/docker.sock" for mount in attrs["Mounts"])
    asyncio.run(cleanup_code_resources("agent1:session1", run.id))


def test_real_daemon_wrong_host_root_or_empty_bind_fails_probe(tmp_path, monkeypatch):
    client = _docker_client()
    wrong_root = tmp_path / "wrong-host-root"
    wrong_root.mkdir()
    run, facts, _workspace = _subject(
        tmp_path, monkeypatch, host_root=wrong_root
    )

    with pytest.raises(RunnerPolicyError, match="workspace_mount_invalid"):
        CodeContainerRunner(client).start(run, facts)


def test_cross_run_extra_mount_socket_and_digest_mismatch_block_start(
    tmp_path, monkeypatch
):
    client = _docker_client()
    run, facts, _workspace = _subject(tmp_path, monkeypatch)
    runner = CodeContainerRunner(client)
    run.id = "other-run"
    with pytest.raises(RunnerPolicyError, match="workspace_mount_invalid"):
        runner.start(run, facts)

    run.id = "run1"
    with pytest.raises(RunnerPolicyError, match="runner_mount_not_allowed"):
        runner.start(run, facts, extra_mounts={"/var/run/docker.sock": "/sock"})

    run.image_digest = "sha256:" + "f" * 64
    with pytest.raises(RunnerUnavailableError, match="runner_start_failed"):
        runner.start(run, facts)


def test_real_daemon_startup_recovery_removes_crashed_runner(tmp_path, monkeypatch):
    client = _docker_client()
    run_facts, facts, workspace = _subject(tmp_path, monkeypatch)
    runner = CodeContainerRunner(client)
    started = runner.start(run_facts, facts)
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    run = CodeAgentRun(
        id="run1",
        agent_id="agent1",
        project_id="project1",
        manifest_id="manifest1",
        manifest_version=1,
        status="running",
        workspace_path=str(workspace),
        workspace_state="prepared",
        workspace_retention_hours=0,
        container_id=started.container_id,
        runner_state="active",
        execution_eligible=True,
        cleanup_state="pending",
    )
    db.add(run)
    db.commit()

    result = CodeLifecycleJanitor(
        db,
        workspace_manager=WorkspaceManager(workspace.parent.parent, retention_hours=0),
        docker_client=client,
    ).run_due(startup_recovery=True)

    db.refresh(run)
    assert [item.outcome for item in result] == ["completed"]
    assert run.runner_state == "removed"
    assert run.workspace_state == "deleted"
    assert run.execution_eligible is False
    with pytest.raises(Exception) as missing:
        client.containers.get(started.container_id)
    assert type(missing.value).__name__ == "NotFound"
    db.close()
