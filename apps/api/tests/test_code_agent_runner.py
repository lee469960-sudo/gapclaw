"""OpenSpec task 6.3: hardened, digest-pinned CodeAgent container runner.

The runner must launch by image digest as a non-root user with read-only rootfs,
cap-drop ALL, no-new-privileges, no network, a single rw Workspace mount and every
resource budget applied; then it must verify Docker inspect facts match the
``RunnerSpec`` exactly, removing the container and failing on any drift.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.services.code_agent.runner import (
    RUNNER_USER,
    CodeContainerRunner,
    RunnerCommandTimeout,
    RunnerPolicyError,
    _image_reference,
    spec_for_run,
    verify_inspect_matches,
)
from app.services.code_agent.runner_protocol import RunnerBudgets, RunnerSpec
from app.services.code_agent.lifecycle import cleanup_code_resources
from app.services.code_agent.workspace_mount import write_workspace_sentinel
from app.services.code_agent import workspace_mount
from app.services import workplace as workplace_service

HOST_WORKSPACE = "/srv/gap/code-agent/runs/run1/workspace"
DIGEST = "sha256:" + "d" * 64


def _budgets(**overrides):
    base = {
        "cpu_count": 2,
        "memory_mb": 512,
        "pids_limit": 256,
        "tmpfs_mb": 64,
        "disk_mb": 1024,
        "timeout_seconds": 1800,
        "output_limit_bytes": 100000,
    }
    base.update(overrides)
    return RunnerBudgets(**base)


def _spec(**overrides):
    spec = RunnerSpec(
        run_id="run1",
        image="internal/python:3.12",
        image_digest=DIGEST,
        budgets=_budgets(),
        workspace_mount_source=HOST_WORKSPACE,
        workspace_mount_target="/workspace",
    )
    return replace(spec, **overrides)


def _attrs(spec, image_id="sha256:image"):
    return {
        "Image": image_id,
        "Config": {"Image": _image_reference(spec), "User": RUNNER_USER},
        "HostConfig": {
            "NetworkMode": spec.network_mode,
            "Privileged": spec.privileged,
            "ReadonlyRootfs": spec.read_only_rootfs,
            "CapDrop": list(spec.cap_drop),
            "SecurityOpt": ["no-new-privileges"],
            "PidsLimit": spec.budgets.pids_limit,
            "NanoCpus": spec.budgets.cpu_count * 1_000_000_000,
            "Memory": spec.budgets.memory_mb * 1024 * 1024,
            "Tmpfs": {"/tmp": f"rw,noexec,nosuid,size={spec.budgets.tmpfs_mb}m"},
            "StorageOpt": {"size": f"{spec.budgets.disk_mb}m"},
        },
        "Mounts": [
            {
                "Source": spec.workspace_mount_source,
                "Destination": spec.workspace_mount_target,
                "RW": True,
            }
        ],
    }


def _runner(tmp_path, monkeypatch, *, coding_runtime="legacy"):
    api_root = tmp_path / "api-runs"
    run_root = api_root / "run1"
    workspace = run_root / "workspace"
    workspace.mkdir(parents=True)
    snapshot_hash = "a" * 64
    write_workspace_sentinel(run_root, "run1", snapshot_hash)
    monkeypatch.setattr(
        workspace_mount, "get_settings",
        lambda: SimpleNamespace(
            code_workspace_api_root=str(api_root),
            code_workspace_host_root="/srv/gap/code-agent/runs",
        ),
    )
    monkeypatch.setattr(workplace_service, "workplace_root", lambda _sandbox_id: api_root)
    run = SimpleNamespace(
        id="run1",
        image="internal/python:3.12",
        image_digest=DIGEST,
        snapshot_hash=snapshot_hash,
        effective_policy=json.dumps({
            "coding_runtime": coding_runtime,
            "budgets": {
                "cpu_count": 1,
                "memory_mb": 256,
                "pids_limit": 128,
                "tmpfs_mb": 32,
                "disk_mb": 512,
                "timeout_seconds": 600,
                "output_limit_bytes": 5000,
            }
        }),
        task_contract=json.dumps({"coding_runtime": coding_runtime}),
        container_id="",
        runner_facts="{}",
    )
    spec = spec_for_run(run, host_path=HOST_WORKSPACE)
    image = SimpleNamespace(id="sha256:image")
    container = SimpleNamespace(
        id="container1",
        status="running",
        remove=MagicMock(),
        reload=MagicMock(),
        image=SimpleNamespace(tags=["internal/python:3.12"]),
        attrs=_attrs(spec),
        exec_run=MagicMock(side_effect=lambda command, **_kwargs: SimpleNamespace(
            exit_code=0,
            output=(
                b"Claude Code 2.1.246\n"
                if command == ["claude", "--version"]
                else b""
            ),
        )),
    )
    client = MagicMock()
    client.images.get.return_value = image
    client.containers.run.return_value = container
    client.containers.get.return_value = container
    sandbox = SimpleNamespace(id="sandbox1", container_id="container1", image="internal/python:3.12")
    facts = SimpleNamespace(path=str(workspace), sandbox=sandbox)
    return CodeContainerRunner(client), client, run, facts


def test_runner_dockerfile_pins_claude_code_cli_version():
    dockerfile = Path(__file__).resolve().parents[3] / "deploy" / "code-agent-runner.Dockerfile"
    text = dockerfile.read_text()
    assert "ARG CLAUDE_CODE_VERSION=2.1.246" in text
    assert "ARG OPENSPEC_VERSION=1.10.0" in text
    assert "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" in text
    assert "@fission-ai/openspec@${OPENSPEC_VERSION}" in text
    assert "npm install -g" in text
    assert "ARG NPM_REGISTRY=" in text
    assert "@anthropic-ai/claude-code@latest" not in text
    assert "@fission-ai/openspec@latest" not in text
    assert "COPY apps/api/app/services/code_agent/sop/" in text
    assert "grill-me" not in text
    assert "claude --version" in text
    assert "openspec --version" in text
    assert "CODE_AGENT_GIT_DIR=/workspace/.git" in text
    assert "clickhouse-client" not in text
    assert "clickhouse client --help" in text
    assert "ARG CLICKHOUSE_VERSION=" in text
    assert "CH_ARCH=arm64" in text
    assert "clickhouse-common-static-" in text
    assert "ARG NODE_VERSION=" in text
    assert "dbt-core==${DBT_CORE_VERSION}" in text
    assert "dbt-clickhouse==${DBT_CLICKHOUSE_VERSION}" in text
    assert "jq --version" in text
    assert "yq --version" in text


def test_runner_multiarch_publish_script_documents_manifest_list_digest():
    script = Path(__file__).resolve().parents[3] / "deploy" / "build-code-agent-runner.sh"
    text = script.read_text()
    assert "docker buildx build" in text
    assert "--platform \"${PLATFORMS}\"" in text
    assert "linux/arm64,linux/amd64" in text
    assert "--push" in text
    assert "manifest-list-digest" in text
    assert "DBT_CORE_VERSION" in text
    assert "DBT_CLICKHOUSE_VERSION" in text
    assert "CLICKHOUSE_VERSION" in text
    assert "--arch-tags" in text


def test_runner_uses_bound_sandbox_and_reports_runtime_facts(tmp_path, monkeypatch):
    runner, client, run, workspace = _runner(tmp_path, monkeypatch)
    facts = runner.start(run, workspace)
    assert runner._bindings["container1"] == "run1"
    client.containers.run.assert_not_called()
    client.containers.get.assert_called_with("container1")
    assert facts.workspace_mount == "/workplace/run1/workspace"
    assert facts.image_id == "sha256:image"
    assert json.loads(run.runner_facts)["network_mode"] == "none"
    asyncio.run(cleanup_code_resources("run1:s1", "run1"))


def test_runner_does_not_require_claude_code_cli_before_git_sync(tmp_path, monkeypatch):
    runner, client, run, workspace = _runner(tmp_path, monkeypatch, coding_runtime="claude_code")
    client.containers.get.return_value.exec_run.side_effect = lambda command, **_kwargs: SimpleNamespace(
        exit_code=0,
        output=b"",
    )
    facts = runner.start(run, workspace)
    assert facts.claude_code_version == ""
    assert json.loads(run.runner_facts)["claude_code_version"] == ""
    client.containers.get.return_value.exec_run.assert_called_once_with(
        ["/bin/sh", "-lc", 'test -d "/workplace/run1/workspace" && test -r "/workplace/run1/workspace" && test -w "/workplace/run1/workspace"'],
        workdir="/workplace",
    )
    asyncio.run(cleanup_code_resources("run1:s1", "run1"))


def test_runner_does_not_inspect_missing_image_object_for_bound_sandbox(tmp_path, monkeypatch):
    runner, client, run, workspace = _runner(tmp_path, monkeypatch)
    base = client.containers.get.return_value

    class BoundSandboxContainer:
        id = base.id
        status = base.status
        attrs = base.attrs
        exec_run = base.exec_run
        reload = base.reload
        remove = base.remove

        @property
        def image(self):
            raise RuntimeError("image_digest_not_present_locally")

    container = BoundSandboxContainer()
    client.containers.get.return_value = container

    facts = runner.start(run, workspace)

    assert facts.container_id == "container1"
    assert facts.image == _image_reference(spec_for_run(run, host_path=HOST_WORKSPACE))
    asyncio.run(cleanup_code_resources("run1:s1", "run1"))


def test_runner_does_not_require_platform_local_publish_tool_profile(tmp_path, monkeypatch):
    runner, client, run, workspace_info = _runner(tmp_path, monkeypatch)
    workspace_path = Path(workspace_info.path)
    run.task_contract = json.dumps({
        "coding_runtime": "legacy",
        "local_publish": {"dependencies": ["curl", "dbt-clickhouse"]},
    })
    container = client.containers.get.return_value
    container.attrs = _attrs(spec_for_run(run, host_path=HOST_WORKSPACE))
    container.exec_run.side_effect = lambda command, **_kwargs: SimpleNamespace(
        exit_code=0,
        output=b"",
    )
    facts = runner.start(run, workspace_info)
    assert facts.container_id == "container1"
    assert "local_publish_tool_versions" not in json.loads(run.runner_facts)
    asyncio.run(cleanup_code_resources("run1:s1", "run1"))


def test_runner_does_not_fail_startup_when_repository_dependencies_are_missing(tmp_path, monkeypatch):
    runner, client, run, workspace = _runner(tmp_path, monkeypatch)
    run.task_contract = json.dumps({
        "local_publish": {"dependencies": ["dbt-clickhouse", "jq", "clickhouse-client", "custom-tool"]},
    })
    container = client.containers.get.return_value
    container.attrs = _attrs(spec_for_run(run, host_path=HOST_WORKSPACE))
    container.exec_run.side_effect = lambda command, **_kwargs: SimpleNamespace(
        exit_code=0,
        output=b"",
    )
    facts = runner.start(run, workspace)
    assert facts.container_id == "container1"
    assert "local_publish_tool_missing" not in json.loads(run.runner_facts)
    asyncio.run(cleanup_code_resources("run1:s1", "run1"))


def test_runner_reuses_bound_sandbox_and_keeps_policy_budgets(tmp_path, monkeypatch):
    runner, client, run, workspace = _runner(tmp_path, monkeypatch)
    facts = runner.start(run, workspace)
    client.containers.run.assert_not_called()
    assert facts.timeout_seconds == 600
    assert facts.output_limit_bytes == 5000
    assert facts.container_id == "container1"
    asyncio.run(cleanup_code_resources("run1:s1", "run1"))


def test_spec_for_run_resolves_and_clamps_budgets_from_frozen_policy(tmp_path, monkeypatch):
    _, _, run, _ = _runner(tmp_path, monkeypatch)
    spec = spec_for_run(run, host_path=HOST_WORKSPACE)
    assert spec.budgets.cpu_count == 1
    assert spec.budgets.memory_mb == 256
    assert spec.budgets.pids_limit == 128
    assert spec.budgets.tmpfs_mb == 32
    assert spec.budgets.disk_mb == 512
    assert spec.budgets.timeout_seconds == 600
    assert spec.budgets.output_limit_bytes == 5000
    assert spec.workspace_mount_source == HOST_WORKSPACE
    assert spec.network_mode == "none"


def test_spec_for_run_uses_bridge_for_claude_code(tmp_path, monkeypatch):
    _, _, run, _ = _runner(tmp_path, monkeypatch, coding_runtime="claude_code")
    spec = spec_for_run(run, host_path=HOST_WORKSPACE)
    assert spec.network_mode == "bridge"


def test_runner_starts_claude_code_with_bridge(tmp_path, monkeypatch):
    runner, client, run, workspace = _runner(tmp_path, monkeypatch, coding_runtime="claude_code")
    facts = runner.start(run, workspace)
    client.containers.run.assert_not_called()
    assert facts.network_mode == "bridge"
    asyncio.run(cleanup_code_resources("run1:s1", "run1"))


def test_spec_for_run_clamps_out_of_range_budgets(tmp_path, monkeypatch):
    _, _, run, _ = _runner(tmp_path, monkeypatch)
    run.effective_policy = json.dumps({"budgets": {"cpu_count": 99, "memory_mb": 1, "pids_limit": 0}})
    spec = spec_for_run(run, host_path=HOST_WORKSPACE)
    assert spec.budgets.cpu_count == 8
    assert spec.budgets.memory_mb == 128
    assert spec.budgets.pids_limit == 16


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"network": True}, "runner_network_not_allowed"),
        ({"privileged": True}, "runner_privileged_not_allowed"),
        ({"nested_container": True}, "runner_nested_container_not_allowed"),
        ({"extra_mounts": {"/var/run/docker.sock": "/docker.sock"}}, "runner_mount_not_allowed"),
    ],
)
def test_runner_rejects_unsafe_capabilities(tmp_path, monkeypatch, kwargs, reason):
    runner, client, run, workspace = _runner(tmp_path, monkeypatch)
    with pytest.raises(RunnerPolicyError, match=reason):
        runner.start(run, workspace, **kwargs)
    client.containers.run.assert_not_called()


def test_runner_exec_enforces_deadline_and_kills_blocked_container():
    release = threading.Event()
    container = MagicMock()
    container.exec_run.side_effect = lambda *_args, **_kwargs: (
        release.wait(2),
        SimpleNamespace(exit_code=0, output=b"late"),
    )[1]
    container.kill.side_effect = release.set
    client = MagicMock()
    client.containers.get.return_value = container
    runner = CodeContainerRunner(client)

    started = time.monotonic()
    with pytest.raises(RunnerCommandTimeout, match="runner_command_timed_out"):
        runner.exec("container1", "pytest -q", timeout_seconds=0.05)

    assert time.monotonic() - started < 0.5
    container.kill.assert_called_once_with()


def test_runner_exec_streams_output_to_callback():
    client = MagicMock()
    client.containers.get.return_value = MagicMock()
    client.api.exec_create.return_value = {"Id": "exec1"}
    client.api.exec_start.return_value = iter([b"first\n", (b"second", b"warning")])
    client.api.exec_inspect.return_value = {"ExitCode": 0}
    runner = CodeContainerRunner(client)
    chunks = []

    exit_code, output = runner.exec(
        "container1",
        "claude -p test --output-format stream-json",
        timeout_seconds=30,
        on_output=chunks.append,
    )

    assert exit_code == 0
    assert output == "first\nsecondwarning"
    assert chunks == ["first\n", "second"]
    client.api.exec_create.assert_called_once()
    client.api.exec_start.assert_called_once_with("exec1", stream=True, demux=True)


def test_runner_exec_falls_back_when_stream_negotiation_is_unavailable():
    client = MagicMock()
    container = client.containers.get.return_value
    client.api.exec_create.side_effect = RuntimeError("stream unsupported")
    container.exec_run.return_value = SimpleNamespace(exit_code=0, output=b"fallback")
    runner = CodeContainerRunner(client)

    exit_code, output = runner.exec(
        "container1", "claude -p test --output-format stream-json", timeout_seconds=30, on_output=lambda _chunk: None
    )

    assert (exit_code, output) == (0, "fallback")
    container.exec_run.assert_called_once()


def test_runner_exec_decodes_tuple_bytes_output():
    client = MagicMock()
    container = client.containers.get.return_value
    commit = "b" * 40
    container.exec_run.return_value = (0, f"line1\n{commit}\n".encode("utf-8"))
    runner = CodeContainerRunner(client)

    exit_code, output = runner.exec(
        "container1",
        "git rev-parse HEAD",
        timeout_seconds=30,
    )

    assert exit_code == 0
    assert output.splitlines() == ["line1", commit]
    assert not output.startswith("b'")


def test_runner_exec_ignores_stream_observer_failure():
    """A live UI callback must not make an otherwise successful exec fail."""
    client = MagicMock()
    client.containers.get.return_value = MagicMock()
    client.api.exec_create.return_value = {"Id": "exec1"}
    client.api.exec_start.return_value = iter([b"result\n"])
    client.api.exec_inspect.return_value = {"ExitCode": 0}
    runner = CodeContainerRunner(client)

    def observer(_chunk):
        raise RuntimeError("websocket closed")

    exit_code, output = runner.exec(
        "container1",
        "claude -p test --output-format stream-json",
        timeout_seconds=30,
        on_output=observer,
    )

    assert (exit_code, output) == (0, "result\n")


def test_runner_exec_handles_transient_null_exit_code():
    client = MagicMock()
    client.containers.get.return_value = MagicMock()
    client.api.exec_create.return_value = {"Id": "exec1"}
    client.api.exec_start.return_value = iter([b"result\n"])
    client.api.exec_inspect.side_effect = [{"ExitCode": None}, {"ExitCode": 0}]
    runner = CodeContainerRunner(client)

    exit_code, output = runner.exec(
        "container1",
        "claude -p test --output-format stream-json",
        timeout_seconds=30,
        on_output=lambda _chunk: None,
    )

    assert (exit_code, output) == (0, "result\n")


def test_runner_exec_keeps_output_when_docker_stream_closes_after_process_exit():
    client = MagicMock()
    client.containers.get.return_value = MagicMock()
    client.api.exec_create.return_value = {"Id": "exec1"}

    def stream_with_transport_close():
        yield b"{\"type\":\"result\",\"status\":\"success\"}\n"
        raise RuntimeError("connection reset by peer")

    client.api.exec_start.return_value = stream_with_transport_close()
    client.api.exec_inspect.return_value = {"ExitCode": 0}
    runner = CodeContainerRunner(client)

    exit_code, output = runner.exec(
        "container1",
        "claude -p test --output-format stream-json",
        timeout_seconds=30,
        on_output=lambda _chunk: None,
    )

    assert exit_code == 0
    assert '"status":"success"' in output


def test_runner_exec_ignores_stream_close_oserror_after_process_exit():
    client = MagicMock()
    client.containers.get.return_value = MagicMock()
    client.api.exec_create.return_value = {"Id": "exec1"}

    class _Stream:
        def __iter__(self):
            yield b"{\"type\":\"result\",\"status\":\"success\"}\n"

        def close(self):
            raise OSError("socket already closed")

    client.api.exec_start.return_value = _Stream()
    client.api.exec_inspect.return_value = {"ExitCode": 0}
    runner = CodeContainerRunner(client)

    exit_code, output = runner.exec(
        "container1",
        "claude -p test --output-format stream-json",
        timeout_seconds=30,
        on_output=lambda _chunk: None,
    )

    assert exit_code == 0
    assert '"status":"success"' in output


def test_runner_does_not_remove_bound_sandbox_on_start_failure(tmp_path, monkeypatch):
    runner, client, run, workspace = _runner(tmp_path, monkeypatch)

    with patch(
        "app.services.code_agent.runner.RunnerFacts",
        side_effect=RuntimeError("post-allocation failure"),
    ):
        with pytest.raises(RuntimeError, match="post-allocation failure"):
            runner.start(run, workspace)

    asyncio.run(cleanup_code_resources("run1:s1", "run1"))
    client.containers.get.return_value.remove.assert_not_called()


def test_verify_inspect_accepts_consistent_facts():
    spec = _spec()
    verify_inspect_matches(spec, "sha256:image", _attrs(spec))


def test_verify_inspect_rejects_each_fact_drift():
    spec = _spec()
    image_id = "sha256:image"
    mutators = {
        "image_id": lambda a: a.update(Image="sha256:other"),
        "network": lambda a: a["HostConfig"].update(NetworkMode="bridge"),
        "privileged": lambda a: a["HostConfig"].update(Privileged=True),
        "rootfs": lambda a: a["HostConfig"].update(ReadonlyRootfs=False),
        "cap_drop": lambda a: a["HostConfig"].update(CapDrop=[]),
        "no_new_privileges": lambda a: a["HostConfig"].update(SecurityOpt=[]),
        "root_user": lambda a: a["Config"].update(User="root"),
        "pids": lambda a: a["HostConfig"].update(PidsLimit=999),
        "cpu": lambda a: a["HostConfig"].update(NanoCpus=99_000_000_000),
        "memory": lambda a: a["HostConfig"].update(Memory=1),
        "tmpfs_size": lambda a: a["HostConfig"].update(Tmpfs={"/tmp": "rw,noexec,nosuid,size=999m"}),
        "disk_size": lambda a: a["HostConfig"].update(StorageOpt={"size": "999m"}),
        "mount_source": lambda a: a["Mounts"][0].update(Source="/other/workspace"),
        "mount_target": lambda a: a["Mounts"][0].update(Destination="/elsewhere"),
        "mount_read_only": lambda a: a["Mounts"][0].update(RW=False),
        "extra_mount": lambda a: a["Mounts"].append(
            {"Source": "/etc", "Destination": "/etc", "RW": True}
        ),
    }
    for name, mutate in mutators.items():
        attrs = _attrs(spec)
        mutate(attrs)
        with pytest.raises(RunnerPolicyError, match="runner_facts_mismatch"):
            verify_inspect_matches(spec, image_id, attrs)


def test_runner_records_existing_sandbox_facts_without_removing_container(tmp_path, monkeypatch):
    runner, client, run, workspace = _runner(tmp_path, monkeypatch)
    client.containers.get.return_value.attrs = _attrs(spec_for_run(run, host_path=HOST_WORKSPACE))
    client.containers.get.return_value.attrs["Image"] = "sha256:drifted"
    facts = runner.start(run, workspace)
    assert facts.image_id == "sha256:drifted"
    client.containers.get.return_value.remove.assert_not_called()
