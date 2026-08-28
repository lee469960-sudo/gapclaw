"""OpenSpec task 6.1: immutable RunnerSpec and typed runner request/response protocol.

The protocol must fail closed on serialization round-trips, illegal fields,
non-positive budgets, malformed digests and helper/schema version mismatches, so a
malformed or downgraded payload can never reach a container.
"""

from __future__ import annotations

import pytest

from app.services.code_agent.runner_protocol import (
    RUNNER_HELPER_VERSION,
    RUNNER_PROTOCOL_VERSION,
    RunnerProtocolError,
    RunnerSpec,
    RunnerToolRequest,
    RunnerToolResponse,
)

DIGEST = "sha256:" + "a" * 64


def _valid_spec(**overrides):
    spec = {
        "run_id": "run1",
        "image": "internal/python:3.12",
        "image_digest": DIGEST,
        "budgets": {
            "cpu_count": 1,
            "memory_mb": 256,
            "pids_limit": 128,
            "tmpfs_mb": 64,
            "disk_mb": 1024,
            "timeout_seconds": 1800,
            "output_limit_bytes": 100000,
        },
        "workspace_mount_source": "/srv/gap/code-agent/runs/run1/workspace",
        "workspace_mount_target": "/workspace",
        "network_mode": "none",
        "network_targets": [],
        "privileged": False,
        "cap_drop": ["ALL"],
        "no_new_privileges": True,
        "read_only_rootfs": True,
        "tmpfs_mounts": {"/tmp": "rw,noexec,nosuid"},
        "helper_version": RUNNER_HELPER_VERSION,
        "schema_version": RUNNER_PROTOCOL_VERSION,
    }
    spec.update(overrides)
    return spec


def _valid_request(**overrides):
    request = {
        "run_id": "run1",
        "container_id": "container1",
        "tool": "read",
        "parameters": {"path": "src/main.py"},
        "request_id": "req1",
        "output_limit_bytes": 100000,
        "helper_version": RUNNER_HELPER_VERSION,
        "schema_version": RUNNER_PROTOCOL_VERSION,
    }
    request.update(overrides)
    return request


def _valid_response(**overrides):
    response = {
        "run_id": "run1",
        "container_id": "container1",
        "tool": "read",
        "helper_version": RUNNER_HELPER_VERSION,
        "exit_code": 0,
        "stdout": "print('hi')\n",
        "stderr": "",
        "output_limit_bytes": 100000,
        "changed_files": [],
        "truncated": False,
        "error": "",
    }
    response.update(overrides)
    return response


# --- RunnerSpec: serialization + fail-closed validation ------------------------


def test_runner_spec_round_trips_serialization():
    raw = _valid_spec()
    assert RunnerSpec.from_dict(raw).to_dict() == raw


def test_runner_spec_rejects_malformed_digest():
    with pytest.raises(RunnerProtocolError, match="runner_spec_invalid_image_digest"):
        RunnerSpec.from_dict(_valid_spec(image_digest="latest"))


def test_runner_spec_rejects_missing_run_id():
    with pytest.raises(RunnerProtocolError, match="runner_spec_missing_run_id"):
        RunnerSpec.from_dict(_valid_spec(run_id=""))


@pytest.mark.parametrize(
    ("budget", "reason"),
    [
        ({"cpu_count": 0}, "runner_spec_non_positive_budget"),
        ({"memory_mb": -1}, "runner_spec_non_positive_budget"),
        ({"output_limit_bytes": "big"}, "runner_spec_invalid_budgets"),
    ],
)
def test_runner_spec_rejects_illegal_budgets(budget, reason):
    raw = _valid_spec()
    raw["budgets"].update(budget)
    with pytest.raises(RunnerProtocolError, match=reason):
        RunnerSpec.from_dict(raw)


def test_runner_spec_rejects_host_network():
    with pytest.raises(RunnerProtocolError, match="runner_spec_network_not_allowed"):
        RunnerSpec.from_dict(_valid_spec(network_mode="host"))


def test_runner_spec_accepts_bridge_network():
    spec = RunnerSpec.from_dict(_valid_spec(network_mode="bridge"))
    assert spec.network_mode == "bridge"


@pytest.mark.parametrize(
    ("override", "reason"),
    [
        ({"privileged": True}, "runner_spec_privileged_not_allowed"),
        ({"read_only_rootfs": False}, "runner_spec_rootfs_not_read_only"),
        ({"no_new_privileges": False}, "runner_spec_privilege_escalation_allowed"),
    ],
)
def test_runner_spec_rejects_unsafe_security_options(override, reason):
    with pytest.raises(RunnerProtocolError, match=reason):
        RunnerSpec.from_dict(_valid_spec(**override))


def test_runner_spec_rejects_helper_version_mismatch():
    with pytest.raises(RunnerProtocolError, match="runner_spec_helper_version_mismatch"):
        RunnerSpec.from_dict(_valid_spec(helper_version="0"))


def test_runner_spec_rejects_schema_version_mismatch():
    with pytest.raises(RunnerProtocolError, match="runner_spec_schema_version_mismatch"):
        RunnerSpec.from_dict(_valid_spec(schema_version=0))


# --- RunnerToolRequest: serialization + fail-closed validation ------------------


@pytest.mark.parametrize(
    ("tool", "parameters"),
    [
        ("read", {"path": "src/main.py"}),
        ("search", {"query": "def main"}),
        ("search", {"query": "def main", "path": "src/"}),
        ("edit", {"path": "src/main.py", "content": "print('x')\n"}),
        ("git", {"operation": "status"}),
        ("git", {"operation": "diff"}),
        ("git", {"operation": "log"}),
        ("test", {"test_index": 0}),
        ("test", {"command": "pytest -q"}),
        ("shell", {"command": "pytest -q"}),
    ],
)
def test_runner_tool_request_round_trips_valid_tools(tool, parameters):
    raw = _valid_request(tool=tool, parameters=parameters)
    assert RunnerToolRequest.from_dict(raw).to_dict() == raw


def test_runner_tool_request_rejects_unknown_tool():
    with pytest.raises(RunnerProtocolError, match="runner_tool_request_unknown_tool"):
        RunnerToolRequest.from_dict(_valid_request(tool="exec", parameters={"command": "ls"}))


def test_runner_tool_request_rejects_illegal_parameter_field():
    with pytest.raises(RunnerProtocolError, match="runner_tool_request_illegal_field"):
        RunnerToolRequest.from_dict(
            _valid_request(parameters={"path": "src/main.py", "sudo": True})
        )


def test_runner_tool_request_rejects_missing_required_parameter():
    with pytest.raises(RunnerProtocolError, match="runner_tool_request_invalid_parameters"):
        RunnerToolRequest.from_dict(_valid_request(tool="read", parameters={}))


def test_runner_tool_request_rejects_git_write_operation():
    with pytest.raises(RunnerProtocolError, match="runner_tool_request_git_write_rejected"):
        RunnerToolRequest.from_dict(
            _valid_request(tool="git", parameters={"operation": "commit"})
        )


def test_runner_tool_request_rejects_invalid_test_selector():
    with pytest.raises(RunnerProtocolError, match="runner_tool_request_invalid_parameters"):
        RunnerToolRequest.from_dict(_valid_request(tool="test", parameters={"test_index": -1}))


def test_runner_tool_request_rejects_helper_version_mismatch():
    with pytest.raises(RunnerProtocolError, match="runner_tool_request_helper_version_mismatch"):
        RunnerToolRequest.from_dict(_valid_request(helper_version="0"))


def test_runner_tool_request_rejects_schema_version_mismatch():
    with pytest.raises(RunnerProtocolError, match="runner_tool_request_schema_version_mismatch"):
        RunnerToolRequest.from_dict(_valid_request(schema_version=2))


def test_runner_tool_request_rejects_missing_container_binding():
    with pytest.raises(RunnerProtocolError, match="runner_tool_request_missing_container_id"):
        RunnerToolRequest.from_dict(_valid_request(container_id=""))


# --- RunnerToolResponse: bounded output + fail-closed validation ----------------


def test_runner_tool_response_round_trips_serialization():
    raw = _valid_response()
    assert RunnerToolResponse.from_dict(raw).to_dict() == raw


def test_runner_tool_response_rejects_output_exceeding_limit():
    raw = _valid_response(output_limit_bytes=10, stdout="x" * 11)
    with pytest.raises(RunnerProtocolError, match="runner_tool_response_output_exceeds_limit"):
        RunnerToolResponse.from_dict(raw)


def test_runner_tool_response_accepts_truncated_output():
    raw = _valid_response(output_limit_bytes=10, stdout="x" * 11, truncated=True)
    assert RunnerToolResponse.from_dict(raw).truncated is True


def test_runner_tool_response_rejects_unknown_tool():
    with pytest.raises(RunnerProtocolError, match="runner_tool_response_unknown_tool"):
        RunnerToolResponse.from_dict(_valid_response(tool="exec"))


def test_runner_tool_response_rejects_helper_version_mismatch():
    with pytest.raises(RunnerProtocolError, match="runner_tool_response_helper_version_mismatch"):
        RunnerToolResponse.from_dict(_valid_response(helper_version="0"))


def test_runner_tool_response_rejects_invalid_exit_code():
    with pytest.raises(RunnerProtocolError, match="runner_tool_response_invalid_exit_code"):
        RunnerToolResponse.from_dict(_valid_response(exit_code="0"))
