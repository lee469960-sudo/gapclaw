"""OpenSpec task 6.4: Code Tools route to the bound runner protocol (no host fallback).

Two concerns are proven here:

1. ``CodeToolExecutor`` no longer touches the API filesystem or subprocess for any
   of read/search/edit/git/test/shell. A monkeypatch that makes ``Path.read_text``,
   ``Path.write_text``, ``Path.rglob`` and ``subprocess.run`` raise is armed, and the
   six tools still complete through a fake runner adapter that returns canned
   ``RunnerToolResponse`` values.
2. ``CodeContainerRunner.run_tool`` serializes a typed ``RunnerToolRequest`` to the
   baked helper over stdin and re-validates the stdout ``RunnerToolResponse``,
   failing closed on malformed or mismatched replies.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.services.code_agent.runner import (
    RUNNER_HELPER_COMMAND,
    CodeContainerRunner,
    RunnerUnavailableError,
)
from app.services.code_agent.runner_protocol import (
    RUNNER_HELPER_VERSION,
    RunnerProtocolError,
    RunnerToolResponse,
)
from app.services.code_agent.tools import CodeToolExecutor


def _canned_run_tool(run, tool, parameters, *, output_limit_bytes, timeout_seconds):
    """Deterministic per-tool response; never touches host filesystem/subprocess."""
    stdout = {
        "read": "value = 1\n",
        "search": "src/app.py:1:value = 1\n",
        "git": "M src/app.py\n",
        "test": "1 passed",
        "shell": "1 passed",
    }.get(tool, "")
    if tool == "edit":
        stdout = json.dumps({
            "path": parameters["path"],
            "bytes": len(parameters["content"].encode("utf-8")),
        })
    return RunnerToolResponse(
        run_id=run.id,
        container_id=run.container_id,
        tool=tool,
        helper_version=RUNNER_HELPER_VERSION,
        exit_code=0,
        stdout=stdout,
        stderr="",
        output_limit_bytes=output_limit_bytes,
    )


def _routing_executor(tmp_path):
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
    )
    db = SimpleNamespace(commit=MagicMock())
    runner = SimpleNamespace(run_tool=MagicMock(side_effect=_canned_run_tool))
    tools = CodeToolExecutor(db, run, runner)
    # Integrity guard reads/writes its own control files on the host; it is covered
    # by workspace tests and is not part of the tool routing under test.
    tools.integrity = SimpleNamespace(
        check_before_seal=lambda: None,
        check_before_write=lambda _path: None,
    )
    return tools, run, runner


def test_six_tools_route_through_runner_without_host_fs_or_subprocess(tmp_path, monkeypatch):
    tools, run, runner = _routing_executor(tmp_path)

    def _boom(*_args, **_kwargs):
        raise AssertionError("host filesystem/subprocess accessed by a Code tool")

    monkeypatch.setattr(Path, "read_text", _boom)
    monkeypatch.setattr(Path, "write_text", _boom)
    monkeypatch.setattr(Path, "rglob", _boom)
    monkeypatch.setattr(subprocess, "run", _boom)

    async def _run():
        read = await tools("code_read", 'CODE_READ: {"path":"src/app.py"}')
        search = await tools("code_search", 'CODE_SEARCH: {"query":"value","path":"src"}')
        edit = await tools("code_edit", 'CODE_EDIT: {"path":"src/app.py","content":"value = 2\\n"}')
        git = await tools("code_git", 'CODE_GIT: {"operation":"status"}')
        test = await tools("code_test", 'CODE_TEST: {"test_index":0}')
        shell = await tools("code_shell", 'CODE_SHELL: {"command":"pytest -q"}')
        return read, search, edit, git, test, shell

    read, search, edit, git, test, shell = asyncio.run(_run())

    assert "value = 1" in read
    assert "src/app.py:1" in search
    assert json.loads(edit)["path"] == "src/app.py"
    assert "src/app.py" in git
    assert json.loads(test)["exit_code"] == 0
    assert json.loads(shell)["exit_code"] == 0
    assert [call.args[1] for call in runner.run_tool.call_args_list] == [
        "read", "search", "edit", "git", "test", "shell",
    ]
    assert run.tool_calls_used == 6


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


def _runner_client(response_json: str):
    client = MagicMock()
    client.containers.get.return_value = MagicMock()
    client.api.exec_create.return_value = {"Id": "exec123"}
    client.api.exec_start.return_value = _FakeSocket((response_json + "\n").encode("utf-8"))
    client.api.exec_inspect.return_value = {"ExitCode": 0}
    return client


def test_runner_run_tool_serializes_request_and_parses_helper_response():
    response = RunnerToolResponse(
        run_id="run1", container_id="container1", tool="read",
        helper_version=RUNNER_HELPER_VERSION, exit_code=0, stdout="value = 1\n",
        stderr="", output_limit_bytes=100_000,
    )
    client = _runner_client(json.dumps(response.to_dict()))
    runner = CodeContainerRunner(client)
    run = SimpleNamespace(id="run1", container_id="container1", status="running")

    result = runner.run_tool(
        run, "read", {"path": "src/app.py"},
        output_limit_bytes=100_000, timeout_seconds=30,
    )

    assert result.tool == "read"
    assert result.stdout == "value = 1\n"
    # Request went to the baked helper with no shell, over stdin.
    create_args = client.api.exec_create.call_args
    assert create_args.args[0] == "container1"
    assert create_args.args[1] == RUNNER_HELPER_COMMAND
    assert create_args.kwargs["stdin"] is True
    assert create_args.kwargs["workdir"] == "/workspace"
    sock = client.api.exec_start.return_value
    request = json.loads(sock.sent.decode("utf-8"))
    assert request["run_id"] == "run1"
    assert request["container_id"] == "container1"
    assert request["tool"] == "read"
    assert request["parameters"] == {"path": "src/app.py"}
    assert request["output_limit_bytes"] == 100_000
    assert sock.closed is True


def test_runner_run_tool_fails_closed_on_malformed_response():
    client = _runner_client("not-a-json-response")
    runner = CodeContainerRunner(client)
    run = SimpleNamespace(id="run1", container_id="container1", status="running")

    with pytest.raises(RunnerUnavailableError, match="runner_exec_failed"):
        runner.run_tool(run, "read", {"path": "src/app.py"},
                        output_limit_bytes=100_000, timeout_seconds=30)


def test_runner_run_tool_rejects_protocol_mismatch_response():
    response = RunnerToolResponse(
        run_id="run1", container_id="container1", tool="read",
        helper_version="99", exit_code=0, stdout="", stderr="",
        output_limit_bytes=100_000,
    )
    client = _runner_client(json.dumps(response.to_dict()))
    runner = CodeContainerRunner(client)
    run = SimpleNamespace(id="run1", container_id="container1", status="running")

    with pytest.raises(RunnerProtocolError):
        runner.run_tool(run, "read", {"path": "src/app.py"},
                        output_limit_bytes=100_000, timeout_seconds=30)


def _docker_mux_frame(payload: bytes, stream: int = 1) -> bytes:
    import struct
    return struct.pack(">BxxxL", stream, len(payload)) + payload


class _SocketIOWrapper:
    """Mirrors docker-py Unix exec_start(socket=True): SocketIO without sendall/recv."""

    def __init__(self, inner: _FakeSocket):
        self._sock = inner
        self.closed = False

    def read(self, n: int = -1) -> bytes:
        raise AssertionError("run_tool must use the raw _sock, not SocketIO.read")

    def close(self) -> None:
        self.closed = True
        self._sock.close()


def test_runner_run_tool_uses_raw_socket_and_strips_docker_mux_frames():
    response = RunnerToolResponse(
        run_id="run1", container_id="container1", tool="read",
        helper_version=RUNNER_HELPER_VERSION, exit_code=0, stdout="value = 1\n",
        stderr="", output_limit_bytes=100_000,
    )
    body = (json.dumps(response.to_dict()) + "\n").encode("utf-8")
    inner = _FakeSocket(_docker_mux_frame(body))
    wrapper = _SocketIOWrapper(inner)
    client = MagicMock()
    client.containers.get.return_value = MagicMock()
    client.api.exec_create.return_value = {"Id": "exec123"}
    client.api.exec_start.return_value = wrapper
    client.api.exec_inspect.return_value = {"ExitCode": 0}
    runner = CodeContainerRunner(client)
    run = SimpleNamespace(id="run1", container_id="container1", status="running")

    result = runner.run_tool(
        run, "read", {"path": "src/app.py"},
        output_limit_bytes=100_000, timeout_seconds=30,
    )

    assert result.stdout == "value = 1\n"
    assert json.loads(inner.sent.decode("utf-8"))["tool"] == "read"
    assert inner.closed is True
    assert wrapper.closed is True
