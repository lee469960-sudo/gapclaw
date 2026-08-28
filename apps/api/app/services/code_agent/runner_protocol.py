"""Immutable runner protocol for the containerized Code Tool runtime.

Task 6.1 defines two frozen value objects that isolate the API process from the
runner image:

* ``RunnerSpec`` — the desired, immutable runner configuration derived from the
  merged effective policy (image digest, security options, network, mount and the
  complete resource budgets). It is what Docker inspect facts must later match.
* ``RunnerToolRequest`` / ``RunnerToolResponse`` — the typed request/response
  envelope used to route each approved Code Tool through the bound run/container
  without shell concatenation.

Both are strictly validated on deserialization: illegal fields, unknown tools,
non-positive budgets, malformed digests and helper/version mismatches all raise
``RunnerProtocolError`` so a malformed or downgraded payload can never be executed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

RUNNER_PROTOCOL_VERSION = 1
RUNNER_HELPER_VERSION = "1"

# The six containerized Code Tool capabilities (git exposes status/diff/log only).
RUNNER_TOOLS = frozenset({"read", "search", "edit", "git", "test", "shell"})
_GIT_OPERATIONS = frozenset({"status", "diff", "log"})

# image_digest must be a pinned content digest (optionally name@sha256:...).
_DIGEST_RE = re.compile(r"^(?:[^\s@]+@)?sha256:[0-9a-f]{64}$")

# Allowed request parameter keys per tool; unknown keys are rejected as illegal.
_REQUEST_PARAMS: dict[str, frozenset[str]] = {
    "read": frozenset({"path"}),
    "search": frozenset({"query", "path"}),
    "edit": frozenset({"path", "content"}),
    "git": frozenset({"operation"}),
    "test": frozenset({"test_index", "command"}),
    "shell": frozenset({"command"}),
}


class RunnerProtocolError(ValueError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise RunnerProtocolError(reason)


@dataclass(frozen=True)
class RunnerBudgets:
    """Complete resource budgets resolved to the most restrictive layer value."""

    cpu_count: int
    memory_mb: int
    pids_limit: int
    tmpfs_mb: int
    disk_mb: int
    timeout_seconds: int
    output_limit_bytes: int

    def to_dict(self) -> dict[str, int]:
        return {
            "cpu_count": self.cpu_count,
            "memory_mb": self.memory_mb,
            "pids_limit": self.pids_limit,
            "tmpfs_mb": self.tmpfs_mb,
            "disk_mb": self.disk_mb,
            "timeout_seconds": self.timeout_seconds,
            "output_limit_bytes": self.output_limit_bytes,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "RunnerBudgets":
        _require(isinstance(raw, dict), "runner_spec_invalid_budgets")
        values: dict[str, int] = {}
        for key in (
            "cpu_count",
            "memory_mb",
            "pids_limit",
            "tmpfs_mb",
            "disk_mb",
            "timeout_seconds",
            "output_limit_bytes",
        ):
            value = raw.get(key)
            _require(isinstance(value, int) and not isinstance(value, bool), "runner_spec_invalid_budgets")
            _require(value > 0, "runner_spec_non_positive_budget")
            values[key] = value
        return cls(**values)


@dataclass(frozen=True)
class RunnerSpec:
    """Immutable desired runner configuration checked against Docker inspect facts."""

    run_id: str
    image: str
    image_digest: str
    budgets: RunnerBudgets
    workspace_mount_source: str
    workspace_mount_target: str = "/workspace"
    network_mode: str = "none"
    network_targets: tuple[str, ...] = ()
    privileged: bool = False
    cap_drop: tuple[str, ...] = ("ALL",)
    no_new_privileges: bool = True
    read_only_rootfs: bool = True
    tmpfs_mounts: dict[str, str] = field(default_factory=lambda: {"/tmp": "rw,noexec,nosuid"})
    helper_version: str = RUNNER_HELPER_VERSION
    schema_version: int = RUNNER_PROTOCOL_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "image": self.image,
            "image_digest": self.image_digest,
            "budgets": self.budgets.to_dict(),
            "workspace_mount_source": self.workspace_mount_source,
            "workspace_mount_target": self.workspace_mount_target,
            "network_mode": self.network_mode,
            "network_targets": list(self.network_targets),
            "privileged": self.privileged,
            "cap_drop": list(self.cap_drop),
            "no_new_privileges": self.no_new_privileges,
            "read_only_rootfs": self.read_only_rootfs,
            "tmpfs_mounts": dict(self.tmpfs_mounts),
            "helper_version": self.helper_version,
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "RunnerSpec":
        _require(isinstance(raw, dict), "runner_spec_invalid")
        _require(bool(str(raw.get("run_id", "")).strip()), "runner_spec_missing_run_id")
        image_digest = str(raw.get("image_digest", "")).strip()
        _require(bool(image_digest), "runner_spec_missing_image_digest")
        _require(bool(_DIGEST_RE.fullmatch(image_digest)), "runner_spec_invalid_image_digest")
        _require(
            str(raw.get("helper_version", "")) == RUNNER_HELPER_VERSION,
            "runner_spec_helper_version_mismatch",
        )
        _require(
            raw.get("schema_version") == RUNNER_PROTOCOL_VERSION,
            "runner_spec_schema_version_mismatch",
        )
        _require(bool(str(raw.get("workspace_mount_source", "")).strip()), "runner_spec_missing_mount")
        network_mode = str(raw.get("network_mode", "none") or "none")
        _require(network_mode in {"none", "bridge"}, "runner_spec_network_not_allowed")
        _require(raw.get("privileged") is False, "runner_spec_privileged_not_allowed")
        _require(raw.get("read_only_rootfs") is True, "runner_spec_rootfs_not_read_only")
        _require(raw.get("no_new_privileges") is True, "runner_spec_privilege_escalation_allowed")

        budgets = RunnerBudgets.from_dict(raw.get("budgets"))
        network_targets = raw.get("network_targets", ())
        _require(isinstance(network_targets, (list, tuple)), "runner_spec_invalid_network_targets")
        _require(
            all(isinstance(target, str) and target.strip() for target in network_targets),
            "runner_spec_invalid_network_targets",
        )
        cap_drop = raw.get("cap_drop", ("ALL",))
        _require(isinstance(cap_drop, (list, tuple)), "runner_spec_invalid_cap_drop")
        tmpfs_mounts = raw.get("tmpfs_mounts", {"/tmp": "rw,noexec,nosuid"})
        _require(isinstance(tmpfs_mounts, dict), "runner_spec_invalid_tmpfs")

        return cls(
            run_id=str(raw["run_id"]).strip(),
            image=str(raw.get("image", "")).strip(),
            image_digest=image_digest,
            budgets=budgets,
            workspace_mount_source=str(raw["workspace_mount_source"]).strip(),
            workspace_mount_target=str(raw.get("workspace_mount_target", "/workspace")),
            network_mode=network_mode,
            network_targets=tuple(str(target) for target in network_targets),
            privileged=False,
            cap_drop=tuple(str(item) for item in cap_drop),
            no_new_privileges=True,
            read_only_rootfs=True,
            tmpfs_mounts=dict(tmpfs_mounts),
            helper_version=RUNNER_HELPER_VERSION,
            schema_version=RUNNER_PROTOCOL_VERSION,
        )


@dataclass(frozen=True)
class RunnerToolRequest:
    """Typed request envelope routing one approved Code Tool into the bound runner."""

    run_id: str
    container_id: str
    tool: str
    parameters: dict[str, Any]
    request_id: str = ""
    output_limit_bytes: int = 100_000
    helper_version: str = RUNNER_HELPER_VERSION
    schema_version: int = RUNNER_PROTOCOL_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "container_id": self.container_id,
            "tool": self.tool,
            "parameters": dict(self.parameters),
            "request_id": self.request_id,
            "output_limit_bytes": self.output_limit_bytes,
            "helper_version": self.helper_version,
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "RunnerToolRequest":
        _require(isinstance(raw, dict), "runner_tool_request_invalid")
        _require(bool(str(raw.get("run_id", "")).strip()), "runner_tool_request_missing_run_id")
        _require(
            bool(str(raw.get("container_id", "")).strip()),
            "runner_tool_request_missing_container_id",
        )
        tool = str(raw.get("tool", "")).strip()
        _require(tool in RUNNER_TOOLS, "runner_tool_request_unknown_tool")
        _require(
            str(raw.get("helper_version", "")) == RUNNER_HELPER_VERSION,
            "runner_tool_request_helper_version_mismatch",
        )
        _require(
            raw.get("schema_version") == RUNNER_PROTOCOL_VERSION,
            "runner_tool_request_schema_version_mismatch",
        )
        output_limit = raw.get("output_limit_bytes", 100_000)
        _require(
            isinstance(output_limit, int) and not isinstance(output_limit, bool) and output_limit > 0,
            "runner_tool_request_invalid_output_limit",
        )

        parameters = raw.get("parameters")
        _require(isinstance(parameters, dict), "runner_tool_request_invalid_parameters")
        allowed = _REQUEST_PARAMS[tool]
        _require(
            set(parameters) <= allowed,
            "runner_tool_request_illegal_field",
        )
        cls._validate_parameters(tool, parameters)
        return cls(
            run_id=str(raw["run_id"]).strip(),
            container_id=str(raw["container_id"]).strip(),
            tool=tool,
            parameters={k: parameters[k] for k in parameters},
            request_id=str(raw.get("request_id", "")),
            output_limit_bytes=output_limit,
            helper_version=RUNNER_HELPER_VERSION,
            schema_version=RUNNER_PROTOCOL_VERSION,
        )

    @staticmethod
    def _validate_parameters(tool: str, parameters: dict[str, Any]) -> None:
        def _string(key: str) -> str:
            value = parameters.get(key, "")
            _require(isinstance(value, str) and bool(value.strip()), "runner_tool_request_invalid_parameters")
            return value.strip()

        if tool == "read":
            _string("path")
        elif tool == "search":
            _string("query")
            if "path" in parameters:
                _string("path")
        elif tool == "edit":
            _string("path")
            _require(isinstance(parameters.get("content"), str), "runner_tool_request_invalid_parameters")
        elif tool == "git":
            _require(_string("operation") in _GIT_OPERATIONS, "runner_tool_request_git_write_rejected")
        elif tool == "test":
            index = parameters.get("test_index")
            command = parameters.get("command")
            has_index = isinstance(index, int) and not isinstance(index, bool) and index >= 0
            has_command = isinstance(command, str) and bool(command.strip())
            _require(has_index or has_command, "runner_tool_request_invalid_parameters")
        elif tool == "shell":
            _string("command")


@dataclass(frozen=True)
class RunnerToolResponse:
    """Typed, bounded response envelope returned by the runner helper."""

    run_id: str
    container_id: str
    tool: str
    helper_version: str
    exit_code: int
    stdout: str
    stderr: str
    output_limit_bytes: int
    changed_files: tuple[str, ...] = ()
    truncated: bool = False
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "container_id": self.container_id,
            "tool": self.tool,
            "helper_version": self.helper_version,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "output_limit_bytes": self.output_limit_bytes,
            "changed_files": list(self.changed_files),
            "truncated": self.truncated,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "RunnerToolResponse":
        _require(isinstance(raw, dict), "runner_tool_response_invalid")
        _require(bool(str(raw.get("run_id", "")).strip()), "runner_tool_response_missing_run_id")
        _require(
            bool(str(raw.get("container_id", "")).strip()),
            "runner_tool_response_missing_container_id",
        )
        tool = str(raw.get("tool", "")).strip()
        _require(tool in RUNNER_TOOLS, "runner_tool_response_unknown_tool")
        _require(
            str(raw.get("helper_version", "")) == RUNNER_HELPER_VERSION,
            "runner_tool_response_helper_version_mismatch",
        )
        exit_code = raw.get("exit_code")
        _require(
            isinstance(exit_code, int) and not isinstance(exit_code, bool),
            "runner_tool_response_invalid_exit_code",
        )
        output_limit = raw.get("output_limit_bytes")
        _require(
            isinstance(output_limit, int) and not isinstance(output_limit, bool) and output_limit > 0,
            "runner_tool_response_invalid_output_limit",
        )
        stdout = raw.get("stdout", "")
        stderr = raw.get("stderr", "")
        _require(isinstance(stdout, str) and isinstance(stderr, str), "runner_tool_response_invalid_output")
        truncated = bool(raw.get("truncated", False))
        # Bounded output fails closed: an untruncated response must stay within the limit.
        _require(
            truncated or (len(stdout) <= output_limit and len(stderr) <= output_limit),
            "runner_tool_response_output_exceeds_limit",
        )
        changed_files = raw.get("changed_files", ())
        _require(
            isinstance(changed_files, (list, tuple))
            and all(isinstance(path, str) for path in changed_files),
            "runner_tool_response_invalid_changed_files",
        )
        return cls(
            run_id=str(raw["run_id"]).strip(),
            container_id=str(raw["container_id"]).strip(),
            tool=tool,
            helper_version=RUNNER_HELPER_VERSION,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            output_limit_bytes=output_limit,
            changed_files=tuple(changed_files),
            truncated=truncated,
            error=str(raw.get("error", "")),
        )
