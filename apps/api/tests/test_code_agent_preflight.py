"""OpenSpec task 6.5: per-tool-call preflight and stable terminal states.

Every Code Tool call must pass an active-lifecycle + run/container binding
preflight, and when the runner becomes unavailable or a bounded resource
(memory/pids/disk/output) is exceeded the run must enter a stable terminal
state that blocks all subsequent tool calls. These are proven at two levels:

1. The runner adapter (``CodeContainerRunner``) fails closed on an inactive run,
   a container_id that does not match the run, a container bound to another run,
   a stale container, an OOM exit code or a helper spawn failure.
2. The executor maps those runner failures onto ``infrastructure_error`` /
   ``resource_limit_exceeded`` / ``timed_out`` and later calls are rejected with
   ``code_runner_not_active``.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.services.code_agent.runner import (
    RUNNER_HELPER_COMMAND,
    CodeContainerRunner,
    RunnerCommandTimeout,
    RunnerPolicyError,
    RunnerResourceLimitError,
    RunnerUnavailableError,
)
from app.services.code_agent.runner_protocol import (
    RUNNER_HELPER_VERSION,
    RunnerProtocolError,
    RunnerToolResponse,
)
from app.services.code_agent.tools import CodeToolExecutor


# --- executor-level fixtures -------------------------------------------------


def _executor_with_runner(tmp_path, runner_exc):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    run = SimpleNamespace(
        id="run1",
        workspace_path=str(workspace),
        status="running",
        workspace_state="prepared",
        container_id="container1",
        failure_reason="",
        effective_policy=json.dumps({
            "allowed_paths": ["."],
            "allowed_tools": ["read", "search", "edit", "test", "shell", "git_read"],
            "shell_commands": ["pytest -q"],
            "budgets": {"max_tool_calls": 50, "timeout_seconds": 60},
        }),
        task_contract=json.dumps({"validation_plan": [{"command": "pytest -q"}]}),
        tool_calls_used=0,
        tool_audit="[]",
        budget_usage="{}",
    )
    db = SimpleNamespace(commit=MagicMock())
    runner = SimpleNamespace(run_tool=MagicMock(side_effect=runner_exc))
    tools = CodeToolExecutor(db, run, runner)
    tools.integrity = SimpleNamespace(
        check_before_seal=lambda: None,
        check_before_write=lambda _path: None,
    )
    return tools, run, runner


def test_runner_unavailable_marks_infrastructure_error_and_blocks_later(tmp_path):
    tools, run, runner = _executor_with_runner(
        tmp_path, RunnerUnavailableError("runner_exec_failed")
    )

    with pytest.raises(RunnerUnavailableError):
        asyncio.run(tools("code_read", 'CODE_READ: {"path":"src/app.py"}'))

    assert run.status == "infrastructure_error"
    assert run.failure_reason == "runner_unavailable"
    assert json.loads(run.tool_audit)[-1]["status"] == "infrastructure_error"
    runner.run_tool.assert_called_once()

    rejected = json.loads(asyncio.run(
        tools("code_read", 'CODE_READ: {"path":"src/app.py"}')
    ))
    assert rejected["reason"] == "code_runner_not_active"
    assert runner.run_tool.call_count == 1  # no further runner call was attempted


@pytest.mark.parametrize("reason", [
    "runner_oom_killed",
    "runner_resource_limit",
    "resource_limit_exceeded",
])
def test_resource_limit_marks_terminal_and_blocks_later(tmp_path, reason):
    tools, run, runner = _executor_with_runner(tmp_path, RunnerResourceLimitError(reason))

    with pytest.raises(RunnerResourceLimitError):
        asyncio.run(tools("code_read", 'CODE_READ: {"path":"src/app.py"}'))

    assert run.status == "resource_limit_exceeded"
    assert run.failure_reason == reason
    assert json.loads(run.tool_audit)[-1]["status"] == "resource_limit_exceeded"

    rejected = json.loads(asyncio.run(
        tools("code_read", 'CODE_READ: {"path":"src/app.py"}')
    ))
    assert rejected["reason"] == "code_runner_not_active"


def test_protocol_error_marks_infrastructure_error(tmp_path):
    tools, run, runner = _executor_with_runner(
        tmp_path, RunnerProtocolError("runner_tool_response_output_exceeds_limit")
    )

    with pytest.raises(RunnerProtocolError):
        asyncio.run(tools("code_read", 'CODE_READ: {"path":"src/app.py"}'))

    assert run.status == "infrastructure_error"
    assert run.failure_reason == "runner_tool_response_output_exceeds_limit"
    assert json.loads(run.tool_audit)[-1]["status"] == "infrastructure_error"


def test_timeout_marks_timed_out_and_blocks_later(tmp_path):
    tools, run, runner = _executor_with_runner(tmp_path, RunnerCommandTimeout())

    with pytest.raises(RunnerCommandTimeout):
        asyncio.run(tools("code_read", 'CODE_READ: {"path":"src/app.py"}'))

    assert run.status == "timed_out"
    assert run.failure_reason == "runner_command_timed_out"
    rejected = json.loads(asyncio.run(
        tools("code_read", 'CODE_READ: {"path":"src/app.py"}')
    ))
    assert rejected["reason"] == "code_runner_not_active"


# --- runner-adapter preflight -----------------------------------------------


def _fake_client(response_json: str | None):
    class _FakeSocket:
        def __init__(self, response: bytes):
            self.sent = b""
            self.response = response
            self.closed = False
            self._pos = 0

        def sendall(self, data: bytes) -> None:
            self.sent += data

        def shutdown(self, _how: int) -> None:
            pass

        def recv(self, n: int) -> bytes:
            chunk = self.response[self._pos:self._pos + n]
            self._pos += len(chunk)
            return chunk

        def close(self) -> None:
            self.closed = True

    client = MagicMock()
    client.containers.get.return_value = MagicMock()
    client.api.exec_create.return_value = {"Id": "exec123"}
    client.api.exec_start.return_value = _FakeSocket(
        (response_json + "\n").encode("utf-8") if response_json else b""
    )
    client.api.exec_inspect.return_value = {"ExitCode": 0}
    return client


def test_preflight_rejects_inactive_run():
    runner = CodeContainerRunner(MagicMock())
    run = SimpleNamespace(id="run1", container_id="container1", status="cancelled")

    with pytest.raises(RunnerUnavailableError, match="runner_not_active"):
        runner.run_tool(
            run, "read", {"path": "src/app.py"},
            output_limit_bytes=1000, timeout_seconds=30,
        )


def test_preflight_rejects_container_reused_across_runs():
    runner = CodeContainerRunner(MagicMock())
    # start() records container1 -> runA; a different run claims the same container.
    runner._bindings["container1"] = "runA"
    run = SimpleNamespace(id="runB", container_id="container1", status="running")

    with pytest.raises(RunnerPolicyError, match="runner_binding_mismatch"):
        runner.run_tool(
            run, "read", {"path": "src/app.py"},
            output_limit_bytes=1000, timeout_seconds=30,
        )


def test_run_tool_fails_closed_on_stale_container():
    client = MagicMock()
    client.containers.get.side_effect = Exception("No such container")
    runner = CodeContainerRunner(client)
    run = SimpleNamespace(id="run1", container_id="container1", status="running")

    with pytest.raises(RunnerUnavailableError, match="runner_exec_failed"):
        runner.run_tool(
            run, "read", {"path": "src/app.py"},
            output_limit_bytes=1000, timeout_seconds=30,
        )


def _response(**overrides):
    base = dict(
        run_id="run1", container_id="container1", tool="shell",
        helper_version=RUNNER_HELPER_VERSION, exit_code=0, stdout="", stderr="",
        output_limit_bytes=1000,
    )
    base.update(overrides)
    return RunnerToolResponse(**base)


def test_run_tool_detects_oom_exit_code_as_resource_limit():
    client = _fake_client(json.dumps(_response(exit_code=137).to_dict()))
    runner = CodeContainerRunner(client)
    run = SimpleNamespace(id="run1", container_id="container1", status="running")

    with pytest.raises(RunnerResourceLimitError, match="runner_oom_killed"):
        runner.run_tool(
            run, "shell", {"command": "pytest -q"},
            output_limit_bytes=1000, timeout_seconds=30,
        )


def test_run_tool_detects_helper_spawn_failure_as_resource_limit():
    client = _fake_client(json.dumps(_response(exit_code=1, error="runner_exec_failed").to_dict()))
    runner = CodeContainerRunner(client)
    run = SimpleNamespace(id="run1", container_id="container1", status="running")

    with pytest.raises(RunnerResourceLimitError, match="runner_resource_limit"):
        runner.run_tool(
            run, "shell", {"command": "pytest -q"},
            output_limit_bytes=1000, timeout_seconds=30,
        )


def test_run_tool_propagates_protocol_error_on_output_exceeding_limit():
    client = _fake_client(json.dumps(_response(
        exit_code=0, stdout="x" * 2000, truncated=False,
    ).to_dict()))
    runner = CodeContainerRunner(client)
    run = SimpleNamespace(id="run1", container_id="container1", status="running")

    # Untruncated stdout over the declared limit fails closed in from_dict.
    with pytest.raises(RunnerProtocolError, match="runner_tool_response_output_exceeds_limit"):
        runner.run_tool(
            run, "shell", {"command": "pytest -q"},
            output_limit_bytes=1000, timeout_seconds=30,
        )


def test_run_tool_still_serializes_to_bound_helper():
    client = _fake_client(json.dumps(_response(stdout="ok").to_dict()))
    runner = CodeContainerRunner(client)
    run = SimpleNamespace(id="run1", container_id="container1", status="running")

    result = runner.run_tool(
        run, "shell", {"command": "pytest -q"},
        output_limit_bytes=1000, timeout_seconds=30,
    )
    assert result.stdout == "ok"
    assert client.api.exec_create.call_args.args[0] == "container1"
    assert client.api.exec_create.call_args.args[1] == RUNNER_HELPER_COMMAND
