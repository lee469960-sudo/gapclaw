"""Persistent lifecycle revocation and cleanup coverage for CodeAgent runs."""

from __future__ import annotations

import asyncio
import uuid
import json
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import CodeAgentRun
from app.services.code_agent.lifecycle import (
    CodeCleanupError,
    active_code_run,
    bind_code_run,
    cleanup_code_resources,
    register_code_cleanup,
)
from app.services.code_agent.tools import CodeToolExecutor


def _active_run(tmp_path):
    from app.services.code_agent import lifecycle as _lifecycle
    _lifecycle._cleanup_hooks.clear()
    _lifecycle._chat_runs.clear()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "src").mkdir()
    (workspace / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    run = CodeAgentRun(
        id=uuid.uuid4().hex[:16],
        agent_id="agent1",
        project_id="project1",
        manifest_id="manifest1",
        manifest_version=1,
        status="running",
        workspace_path=str(workspace),
        workspace_state="prepared",
        container_id="container1",
        runner_network_id="network1",
        runner_state="active",
        execution_eligible=True,
        cleanup_state="pending",
        effective_policy=json.dumps({
            "allowed_paths": ["src/"],
            "allowed_tools": ["read"],
            "budgets": {"max_tool_calls": 10},
        }),
        task_contract="{}",
    )
    db.add(run)
    db.commit()
    return db, run


@pytest.mark.parametrize(
    "terminal_status",
    [
        "execution_completed",
        "cancelled",
        "timed_out",
        "policy_rejected",
        "infrastructure_error",
    ],
)
def test_terminal_state_revokes_route_before_cleanup_and_blocks_new_tools(
    tmp_path, terminal_status
):
    db, run = _active_run(tmp_path)
    chat_key = f"agent1:{terminal_status}"
    cleanup_order = []
    observed = {}

    def _workspace_cleanup():
        cleanup_order.append("workspace")
        run.workspace_state = "retained_read_only"

    def _runner_cleanup():
        db.refresh(run)
        observed.update({
            "active_route": active_code_run(chat_key),
            "execution_eligible": run.execution_eligible,
            "runner_state": run.runner_state,
            "cleanup_state": run.cleanup_state,
        })
        cleanup_order.append("runner")

    bind_code_run(chat_key, run.id)
    register_code_cleanup(run.id, _workspace_cleanup)
    register_code_cleanup(run.id, _runner_cleanup)
    asyncio.run(cleanup_code_resources(
        chat_key,
        run.id,
        db=db,
        terminal_status=terminal_status,
        failure_reason="" if terminal_status == "execution_completed" else terminal_status,
    ))

    db.refresh(run)
    assert observed == {
        "active_route": "",
        "execution_eligible": False,
        "runner_state": "revoking",
        "cleanup_state": "running",
    }
    assert cleanup_order == ["runner", "workspace"]
    assert run.status == terminal_status
    assert run.execution_eligible is False
    assert run.runner_state == "removed"
    assert run.cleanup_state == "completed"
    assert run.container_id == ""
    assert run.runner_network_id == ""
    assert run.workspace_state == "retained_read_only"

    runner = MagicMock()
    response = asyncio.run(CodeToolExecutor(db, run, runner)(
        "code_read", 'CODE_READ: {"path":"src/app.py"}'
    ))
    assert json.loads(response)["reason"] == "code_runner_not_active"
    runner.run_tool.assert_not_called()
    db.close()


def test_cleanup_failure_remains_revoked_and_is_persisted(tmp_path):
    db, run = _active_run(tmp_path)
    chat_key = "agent1:failure"
    bind_code_run(chat_key, run.id)
    register_code_cleanup(run.id, lambda: (_ for _ in ()).throw(RuntimeError("remove failed")))

    with pytest.raises(CodeCleanupError, match="cleanup_failed"):
        asyncio.run(cleanup_code_resources(
            chat_key,
            run.id,
            db=db,
            terminal_status="infrastructure_error",
            failure_reason="cleanup_failed",
        ))

    db.refresh(run)
    assert active_code_run(chat_key) == ""
    assert run.execution_eligible is False
    assert run.runner_state == "cleanup_failed"
    assert run.cleanup_state == "failed"
    assert run.status == "infrastructure_error"
    db.close()
