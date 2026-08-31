"""Container runner adapter for CodeAgent work inside a bound persistent sandbox."""

from __future__ import annotations

import json
import logging
import socket
import struct
import threading
import time
from dataclasses import dataclass
from collections.abc import Callable
from pathlib import Path

from app.services.code_agent.runner_protocol import (
    RUNNER_HELPER_VERSION,
    RUNNER_PROTOCOL_VERSION,
    RunnerBudgets,
    RunnerProtocolError,
    RunnerSpec,
    RunnerToolRequest,
    RunnerToolResponse,
)

# Non-root identity for the container main process (nobody:nogroup).
RUNNER_USER = "65534:65534"
ROOT_USER = "0:0"

# The baked helper executable and its in-container workspace root. Every approved
# Code Tool is routed to the helper as JSON on stdin → JSON on stdout (no shell).
RUNNER_HELPER_COMMAND = ["python", "/opt/code-agent/runner_helper.py"]
RUNNER_WORKSPACE = "/workspace"
CLAUDE_CODE_VERSION_COMMAND = ["claude", "--version"]

# Docker attach/exec multiplex header: stream byte, 3 padding bytes, uint32 size.
_DOCKER_STDOUT = 1
_DOCKER_STDERR = 2

logger = logging.getLogger(__name__)


def _exec_transport_socket(sock):
    """Return an object with sendall/recv for docker-py exec_start(socket=True).

    On Unix, docker-py yields ``socket.SocketIO`` (read/write file wrapper) rather
    than a raw socket. The raw ``socket.socket`` is on ``_sock``.
    """
    if callable(getattr(sock, "sendall", None)) and callable(getattr(sock, "recv", None)):
        return sock
    raw = getattr(sock, "_sock", None)
    if raw is not None and callable(getattr(raw, "sendall", None)) and callable(
        getattr(raw, "recv", None)
    ):
        return raw
    return sock


def _demux_docker_stdout(raw: bytes) -> bytes:
    """Strip Docker multiplex frames and keep stdout. Plain JSON is returned as-is."""
    if len(raw) < 8:
        return raw
    stream, _size = struct.unpack(">BxxxL", raw[:8])
    if stream not in (_DOCKER_STDOUT, _DOCKER_STDERR):
        return raw
    stdout = bytearray()
    offset = 0
    while offset + 8 <= len(raw):
        stream, size = struct.unpack(">BxxxL", raw[offset:offset + 8])
        offset += 8
        if stream not in (0, _DOCKER_STDOUT, _DOCKER_STDERR) or offset + size > len(raw):
            raise ValueError("runner_exec_frame_invalid")
        if stream == _DOCKER_STDOUT:
            stdout.extend(raw[offset:offset + size])
        offset += size
    return bytes(stdout)


def _helper_stdio_exchange(sock, payload: bytes) -> bytes:
    """Write one helper request to exec stdin and read demuxed stdout."""
    transport = _exec_transport_socket(sock)
    try:
        transport.sendall(payload)
        shutdown = getattr(transport, "shutdown", None)
        if callable(shutdown):
            try:
                shutdown(socket.SHUT_WR)
            except (OSError, AttributeError):
                pass
        chunks: list[bytes] = []
        while True:
            chunk = transport.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
        return _demux_docker_stdout(b"".join(chunks))
    finally:
        close = getattr(sock, "close", None)
        if callable(close):
            close()


class RunnerPolicyError(ValueError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class RunnerUnavailableError(RuntimeError):
    def __init__(self, reason: str = "runner_unavailable", *, detail: str = ""):
        self.reason = reason
        self.detail = detail
        super().__init__(reason)


class RunnerCommandTimeout(TimeoutError):
    def __init__(self, reason: str = "runner_command_timed_out"):
        self.reason = reason
        super().__init__(reason)


class RunnerResourceLimitError(RuntimeError):
    """A bounded runner resource (memory/pids/disk/output) was exceeded."""

    def __init__(self, reason: str = "resource_limit_exceeded"):
        self.reason = reason
        super().__init__(reason)


# SIGKILL (128 + 9) or a negative signal are how the Docker OOM killer and pids
# cgroup surface a killed subprocess through the helper's reported exit code.
_OOM_EXIT_CODES = frozenset({137, -9})


def _clamp_int(value: object, default: int, lo: int, hi: int) -> int:
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        number = default
    return max(lo, min(number, hi))


def _image_reference(spec: RunnerSpec) -> str:
    """Return the digest-pinned image reference to launch (name@sha256:...)."""
    if "@" in spec.image_digest:
        return spec.image_digest
    if spec.image:
        return f"{spec.image}@{spec.image_digest}"
    return spec.image_digest


def _run_uses_claude_code(run) -> bool:
    try:
        contract = json.loads(getattr(run, "task_contract", "") or "{}")
    except (TypeError, json.JSONDecodeError):
        contract = {}
    if isinstance(contract, dict) and contract.get("coding_runtime") == "claude_code":
        return True
    try:
        policy = json.loads(getattr(run, "effective_policy", "") or "{}")
    except (TypeError, json.JSONDecodeError):
        policy = {}
    return isinstance(policy, dict) and policy.get("coding_runtime") == "claude_code"


def _decode_exec_output(output: object) -> str:
    if isinstance(output, bytes):
        return output.decode("utf-8", errors="replace")
    return str(output or "")


def _probe_claude_code_version(container) -> str:
    try:
        observed = container.exec_run(CLAUDE_CODE_VERSION_COMMAND)
    except Exception as exc:
        raise RunnerUnavailableError("runner_claude_code_unavailable") from exc
    exit_code = int(getattr(observed, "exit_code", 1))
    output = _decode_exec_output(getattr(observed, "output", ""))
    if exit_code != 0 or not output:
        raise RunnerUnavailableError("runner_claude_code_unavailable")
    return output


def spec_for_run(run, *, host_path: str) -> RunnerSpec:
    """Build the immutable RunnerSpec for a frozen run and validated host path.

    Numeric budgets come from the merged effective policy, clamped to their safe
    platform bounds. Deserializing through ``RunnerSpec.from_dict`` re-validates
    the digest, security options and every budget, raising ``RunnerProtocolError``
    on a malformed value.
    """
    policy = json.loads(run.effective_policy or "{}")
    budgets_raw = policy.get("budgets") if isinstance(policy, dict) else {}
    if not isinstance(budgets_raw, dict):
        budgets_raw = {}
    budgets = RunnerBudgets(
        cpu_count=_clamp_int(budgets_raw.get("cpu_count"), 2, 1, 8),
        memory_mb=_clamp_int(budgets_raw.get("memory_mb"), 512, 128, 8192),
        pids_limit=_clamp_int(budgets_raw.get("pids_limit"), 256, 16, 4096),
        tmpfs_mb=_clamp_int(budgets_raw.get("tmpfs_mb"), 64, 32, 4096),
        disk_mb=_clamp_int(budgets_raw.get("disk_mb"), 1024, 128, 10240),
        timeout_seconds=_clamp_int(budgets_raw.get("timeout_seconds"), 1800, 1, 86400),
        output_limit_bytes=_clamp_int(budgets_raw.get("output_limit_bytes"), 100_000, 1024, 10_000_000),
    )
    return RunnerSpec.from_dict({
        "run_id": str(getattr(run, "id", "")),
        "image": str(getattr(run, "image", "") or ""),
        "image_digest": str(getattr(run, "image_digest", "") or ""),
        "budgets": budgets.to_dict(),
        "workspace_mount_source": host_path,
        "workspace_mount_target": "/workspace",
        "network_mode": "bridge" if _run_uses_claude_code(run) else "none",
        "network_targets": [],
        "privileged": False,
        "cap_drop": [] if _run_uses_claude_code(run) else ["ALL"],
        "read_only_rootfs": True,
        "dependency_bootstrap": False,
        "run_as_root": _run_uses_claude_code(run),
        "no_new_privileges": True,
        "helper_version": RUNNER_HELPER_VERSION,
        "schema_version": RUNNER_PROTOCOL_VERSION,
    })


def _parse_size_mb(value: str) -> int:
    """Parse a Docker size string like ``512m``/``1g`` into MiB (best effort)."""
    raw = str(value or "").strip().lower()
    if not raw:
        return 0
    try:
        if raw.endswith("g"):
            return int(float(raw[:-1]) * 1024)
        if raw.endswith("m"):
            return int(float(raw[:-1]))
        if raw.endswith("k"):
            return int(float(raw[:-1]) // 1024)
        return int(float(raw))
    except ValueError:
        return 0


def inspect_facts(attrs: dict) -> dict:
    """Extract the comparable security/limit facts from a Docker inspect payload."""
    host = attrs.get("HostConfig") or {}
    config = attrs.get("Config") or {}
    mounts = attrs.get("Mounts") or []
    return {
        "image": config.get("Image", ""),
        "image_id": attrs.get("Image", ""),
        "network_mode": host.get("NetworkMode", ""),
        "privileged": bool(host.get("Privileged")),
        "read_only_rootfs": bool(host.get("ReadonlyRootfs")),
        "cap_drop": list(host.get("CapDrop") or []),
        "no_new_privileges": "no-new-privileges" in (host.get("SecurityOpt") or []),
        "user": config.get("User", ""),
        "pids_limit": int(host.get("PidsLimit") or 0),
        "cpu_count": int((host.get("NanoCpus") or 0) // 1_000_000_000),
        "memory_mb": int((host.get("Memory") or 0) // (1024 * 1024)),
        "tmpfs": host.get("Tmpfs") or {},
        "storage_opt": host.get("StorageOpt") or {},
        "mounts": [
            (str(m.get("Source", "")), str(m.get("Destination", "")), bool(m.get("RW")))
            for m in mounts
        ],
    }


def verify_inspect_matches(spec: RunnerSpec, image_id: str, attrs: dict) -> None:
    """Fail closed if Docker inspect facts do not exactly match the RunnerSpec."""
    facts = inspect_facts(attrs)
    if facts["image_id"] != image_id:
        raise RunnerPolicyError("runner_facts_mismatch")
    if facts["network_mode"] != spec.network_mode:
        raise RunnerPolicyError("runner_facts_mismatch")
    if facts["privileged"] is not spec.privileged:
        raise RunnerPolicyError("runner_facts_mismatch")
    if facts["read_only_rootfs"] is not spec.read_only_rootfs:
        raise RunnerPolicyError("runner_facts_mismatch")
    if not spec.run_as_root and "ALL" not in facts["cap_drop"]:
        raise RunnerPolicyError("runner_facts_mismatch")
    if facts["no_new_privileges"] is not spec.no_new_privileges:
        raise RunnerPolicyError("runner_facts_mismatch")
    if not spec.run_as_root and facts["user"] in ("", "root", "0"):
        raise RunnerPolicyError("runner_facts_mismatch")
    if facts["pids_limit"] != spec.budgets.pids_limit:
        raise RunnerPolicyError("runner_facts_mismatch")
    if facts["cpu_count"] != spec.budgets.cpu_count:
        raise RunnerPolicyError("runner_facts_mismatch")
    if facts["memory_mb"] != spec.budgets.memory_mb:
        raise RunnerPolicyError("runner_facts_mismatch")
    tmpfs = facts["tmpfs"].get("/tmp", "")
    if _parse_size_mb(_size_option(tmpfs)) != spec.budgets.tmpfs_mb:
        raise RunnerPolicyError("runner_facts_mismatch")
    if _parse_size_mb(str(facts["storage_opt"].get("size", ""))) != spec.budgets.disk_mb:
        raise RunnerPolicyError("runner_facts_mismatch")
    expected_mount = (spec.workspace_mount_source, spec.workspace_mount_target, True)
    if expected_mount not in facts["mounts"] or len(facts["mounts"]) != 1:
        raise RunnerPolicyError("runner_facts_mismatch")


def _size_option(tmpfs_spec: str) -> str:
    """Extract the ``size=...`` value from a tmpfs mount specification."""
    for token in str(tmpfs_spec or "").split(","):
        if token.startswith("size="):
            return token[len("size="):]
    return ""


@dataclass(frozen=True)
class RunnerFacts:
    container_id: str
    image: str
    image_id: str
    network_mode: str
    cpu_count: int
    memory_mb: int
    pids_limit: int = 256
    tmpfs_mb: int = 64
    disk_mb: int = 1024
    timeout_seconds: int = 1800
    output_limit_bytes: int = 100_000
    user: str = RUNNER_USER
    workspace_mount: str = "/workspace"
    privileged: bool = False
    read_only_rootfs: bool = True
    cap_drop: tuple = ("ALL",)
    no_new_privileges: bool = True
    nested_container: bool = False
    claude_code_version: str = ""

    def to_dict(self) -> dict:
        return {
            "container_id": self.container_id,
            "image": self.image,
            "image_id": self.image_id,
            "network_mode": self.network_mode,
            "cpu_count": self.cpu_count,
            "memory_mb": self.memory_mb,
            "pids_limit": self.pids_limit,
            "tmpfs_mb": self.tmpfs_mb,
            "disk_mb": self.disk_mb,
            "timeout_seconds": self.timeout_seconds,
            "output_limit_bytes": self.output_limit_bytes,
            "user": self.user,
            "workspace_mount": self.workspace_mount,
            "privileged": self.privileged,
            "read_only_rootfs": self.read_only_rootfs,
            "cap_drop": list(self.cap_drop),
            "no_new_privileges": self.no_new_privileges,
            "nested_container": self.nested_container,
            "claude_code_version": self.claude_code_version,
        }


class CodeContainerRunner:
    def __init__(self, client=None):
        if client is None:
            from app.services.docker_service import get_docker_client
            client = get_docker_client()
        if client is None:
            raise RunnerUnavailableError("runner_unavailable")
        self.client = client
        self._deadlines: dict[str, float] = {}
        # container_id -> run_id, populated at start() and used by the per-call
        # preflight to reject a run reusing a container bound to another run.
        self._bindings: dict[str, str] = {}
        self._workdirs: dict[str, str] = {}

    def start(
        self,
        run,
        workspace,
        *,
        sandbox=None,
        network: bool = False,
        privileged: bool = False,
        nested_container: bool = False,
        extra_mounts: dict | None = None,
    ) -> RunnerFacts:
        if network:
            raise RunnerPolicyError("runner_network_not_allowed")
        if privileged:
            raise RunnerPolicyError("runner_privileged_not_allowed")
        if nested_container:
            raise RunnerPolicyError("runner_nested_container_not_allowed")
        if extra_mounts:
            raise RunnerPolicyError("runner_mount_not_allowed")
        if getattr(run, "workspace_state", "prepared") != "prepared":
            raise RunnerPolicyError("runner_workspace_unavailable")
        workspace_path = Path(workspace.path).resolve()
        if not workspace_path.is_dir():
            raise RunnerPolicyError("runner_workspace_unavailable")
        if sandbox is None:
            sandbox = getattr(workspace, "sandbox", None)
        if sandbox is None:
            raise RunnerUnavailableError("sandbox_not_configured")
        sandbox_container_id = str(getattr(sandbox, "container_id", "") or "").strip()
        if not sandbox_container_id:
            raise RunnerUnavailableError("sandbox_not_running")
        try:
            container = self.client.containers.get(sandbox_container_id)
            container.reload()
        except Exception as exc:
            raise RunnerUnavailableError("sandbox_not_running") from exc
        if str(getattr(container, "status", "") or "") != "running":
            raise RunnerUnavailableError("sandbox_not_running")
        try:
            from app.services.workplace import workplace_root

            sandbox_workplace = workplace_root(str(getattr(sandbox, "id", "") or "")).resolve()
            relative = workspace_path.relative_to(sandbox_workplace).as_posix()
        except (OSError, ValueError) as exc:
            raise RunnerPolicyError("workspace_mount_invalid") from exc
        workspace_mount = "/workplace" if not relative else f"/workplace/{relative}"
        try:
            quoted = json.dumps(workspace_mount)
            observed = container.exec_run(
                ["/bin/sh", "-lc", f"test -d {quoted} && test -r {quoted} && test -w {quoted}"],
                workdir="/workplace",
            )
            if int(getattr(observed, "exit_code", 1)) != 0:
                raise RunnerPolicyError("workspace_mount_invalid")
        except RunnerPolicyError:
            raise
        except RunnerUnavailableError:
            raise
        except Exception as exc:
            raise RunnerPolicyError("workspace_mount_invalid") from exc
        inspected = inspect_facts(getattr(container, "attrs", {}))
        image_reference = str(inspected.get("image") or getattr(sandbox, "image", "") or "")
        try:
            policy = json.loads(getattr(run, "effective_policy", "") or "{}")
        except (TypeError, json.JSONDecodeError):
            policy = {}
        budgets = policy.get("budgets") if isinstance(policy, dict) else {}
        facts = RunnerFacts(
            container_id=container.id,
            image=image_reference,
            image_id=inspected.get("image_id", ""),
            network_mode=inspected.get("network_mode", ""),
            cpu_count=inspected.get("cpu_count", 0),
            memory_mb=inspected.get("memory_mb", 0),
            pids_limit=inspected.get("pids_limit", 0),
            tmpfs_mb=0,
            disk_mb=0,
            timeout_seconds=_clamp_int((budgets or {}).get("timeout_seconds"), 1800, 1, 86400),
            output_limit_bytes=_clamp_int((budgets or {}).get("output_limit_bytes"), 100_000, 1024, 10_000_000),
            user=inspected.get("user", ""),
            workspace_mount=workspace_mount,
            privileged=bool(inspected.get("privileged")),
            read_only_rootfs=bool(inspected.get("read_only_rootfs")),
            cap_drop=tuple(inspected.get("cap_drop") or []),
            no_new_privileges=bool(inspected.get("no_new_privileges")),
            claude_code_version="",
        )
        run.container_id = container.id
        run.runner_facts = json.dumps(facts.to_dict(), sort_keys=True)
        self._deadlines[container.id] = time.monotonic() + facts.timeout_seconds
        self._bindings[container.id] = str(run.id)
        self._workdirs[container.id] = workspace_mount
        return facts

    def exec(
        self,
        container_id: str,
        command: str,
        *,
        timeout_seconds: int,
        environment: dict[str, str] | None = None,
        on_output: Callable[[str], None] | None = None,
    ) -> tuple[int, str]:
        try:
            container = self.client.containers.get(container_id)
        except Exception as exc:
            logger.warning("runner container lookup failed", exc_info=True)
            raise RunnerUnavailableError(
                "runner_exec_failed", detail=type(exc).__name__
            ) from exc

        requested_timeout = max(0.0, float(timeout_seconds))
        deadline = self._deadlines.get(container_id)
        remaining = requested_timeout
        if deadline is not None:
            remaining = min(remaining, max(0.0, deadline - time.monotonic()))
        if remaining <= 0:
            self._terminate_timed_out_container(container)
            raise RunnerCommandTimeout()

        completed = threading.Event()
        result_holder: dict[str, object] = {}

        def _execute() -> None:
            try:
                exec_env = {
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "PYTEST_ADDOPTS": "-p no:cacheprovider",
                    "npm_config_cache": "/tmp/npm-cache",
                }
                if environment:
                    for key, value in environment.items():
                        name = str(key or "").strip()
                        if name:
                            exec_env[name] = str(value)
                if on_output is None:
                    result_holder["result"] = container.exec_run(
                        ["/bin/sh", "-lc", command],
                        workdir=self._workdirs.get(container_id, "/workspace"),
                        environment=exec_env,
                    )
                else:
                    stream_started = False
                    try:
                        exec_id = self.client.api.exec_create(
                            container.id,
                            ["/bin/sh", "-lc", command],
                            stdout=True,
                            stderr=True,
                            workdir=self._workdirs.get(container_id, "/workspace"),
                            environment=exec_env,
                        )["Id"]
                        stream = self.client.api.exec_start(exec_id, stream=True, demux=True)
                        stream_started = True
                        chunks: list[str] = []
                        stream_error: BaseException | None = None
                        try:
                            try:
                                for chunk in stream:
                                    stderr_text = ""
                                    if isinstance(chunk, tuple):
                                        stdout_chunk = chunk[0] if len(chunk) > 0 else b""
                                        stderr_chunk = chunk[1] if len(chunk) > 1 else b""
                                        chunk = stdout_chunk
                                        if isinstance(stderr_chunk, bytes):
                                            stderr_text = stderr_chunk.decode("utf-8", errors="replace")
                                    if isinstance(chunk, bytes):
                                        text = chunk.decode("utf-8", errors="replace")
                                    else:
                                        text = str(chunk or "")
                                    if text:
                                        chunks.append(text)
                                        # Output observers (for example the live
                                        # UI event publisher) must never be able
                                        # to abort the actual container command.
                                        # A disconnected websocket or a closed
                                        # event loop is an observer failure, not
                                        # a runner failure.
                                        try:
                                            on_output(text)
                                        except BaseException:
                                            logger.warning(
                                                "stream output observer failed; continuing command",
                                                exc_info=True,
                                            )
                                    if stderr_text:
                                        chunks.append(stderr_text)
                            except BaseException as exc:
                                # Docker daemon/proxy versions occasionally
                                # close the HTTP stream after the exec has
                                # already completed. Keep the captured output
                                # and use exec_inspect as the source of truth
                                # for the command's exit status.
                                stream_error = exc
                                logger.warning(
                                    "stream closed before EOF; inspecting exec status",
                                    exc_info=True,
                                )
                        finally:
                            close = getattr(stream, "close", None)
                            if callable(close):
                                try:
                                    close()
                                except BaseException:
                                    # Closing a generator backed by a broken
                                    # Docker socket can itself raise OSError.
                                    # The process result is determined below by
                                    # exec_inspect, so cleanup errors must not
                                    # replace that result with runner_exec_failed.
                                    logger.warning(
                                        "stream close failed; continuing to inspect exec status",
                                        exc_info=True,
                                    )
                        # A few Docker API versions briefly report a null
                        # ExitCode after the streaming generator closes. Poll
                        # for the terminal value within a small bounded window
                        # instead of turning that transient state into the
                        # opaque runner_exec_failed result.
                        inspected = {}
                        inspect_error: BaseException | None = None
                        # Allow the daemon a short grace period to publish the
                        # exec status when the stream connection is lost while
                        # the child process is still finishing. This is bounded
                        # by 30s and therefore cannot bypass the command's
                        # overall deadline.
                        inspect_deadline = time.monotonic() + min(
                            30.0, max(1.0, remaining)
                        )
                        while time.monotonic() < inspect_deadline:
                            try:
                                inspected = self.client.api.exec_inspect(exec_id)
                                inspect_error = None
                            except BaseException as exc:
                                inspect_error = exc
                                time.sleep(0.01)
                                continue
                            if inspected.get("ExitCode") is not None:
                                break
                            time.sleep(0.01)
                        exit_code = inspected.get("ExitCode")
                        if exit_code is None:
                            if stream_error is not None:
                                raise stream_error
                            if inspect_error is not None:
                                raise inspect_error
                            exit_code = 1
                        result_holder["result"] = (
                            int(exit_code),
                            "".join(chunks),
                        )
                    except Exception:
                        if stream_started:
                            raise
                        # Some older Docker daemons reject the streaming exec
                        # negotiation. The command is still safe to complete
                        # synchronously; Claude's final stream-json payload is
                        # parsed after return, but live events are unavailable.
                        logger.warning("streaming exec unavailable; falling back to sync exec", exc_info=True)
                        result_holder["result"] = container.exec_run(
                            ["/bin/sh", "-lc", command],
                            workdir=self._workdirs.get(container_id, "/workspace"),
                            environment=exec_env,
                        )
            except BaseException as exc:
                result_holder["error"] = exc
            finally:
                completed.set()

        threading.Thread(
            target=_execute,
            name=f"code-runner-exec-{container_id[:12]}",
            daemon=True,
        ).start()
        if not completed.wait(remaining):
            self._terminate_timed_out_container(container)
            raise RunnerCommandTimeout()
        if "error" in result_holder:
            error = result_holder["error"]
            if isinstance(error, BaseException):
                detail = type(error).__name__
                if isinstance(error, OSError) and error.errno is not None:
                    detail = f"{detail}[errno={error.errno}]"
                raise RunnerUnavailableError(
                    "runner_exec_failed", detail=detail
                ) from error
            raise RunnerUnavailableError("runner_exec_failed")
        result = result_holder["result"]
        if isinstance(result, tuple):
            return int(result[0]), _decode_exec_output(result[1])[:100000]
        exit_code = int(getattr(result, "exit_code", 1))
        output = getattr(result, "output", b"")
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        return exit_code, str(output)[:100000]

    def exec_argv(
        self,
        container_id: str,
        argv: list[str] | tuple[str, ...],
        *,
        timeout_seconds: int,
        environment: dict[str, str] | None = None,
    ) -> tuple[int, str]:
        """Execute a pre-approved argv directly, without invoking a shell."""
        values = [str(item) for item in argv]
        if not values or any(not item.strip() for item in values):
            raise RunnerPolicyError("runner_command_invalid")
        if any(any(token in item for token in (";", "&&", "||", "`", "$(")) for item in values):
            raise RunnerPolicyError("runner_shell_not_allowed")
        try:
            container = self.client.containers.get(container_id)
        except Exception as exc:
            raise RunnerUnavailableError("runner_exec_failed") from exc
        requested_timeout = max(0.0, float(timeout_seconds))
        deadline = self._deadlines.get(container_id)
        remaining = requested_timeout if deadline is None else min(
            requested_timeout, max(0.0, deadline - time.monotonic())
        )
        if remaining <= 0:
            self._terminate_timed_out_container(container)
            raise RunnerCommandTimeout()
        result_holder: dict[str, object] = {}
        completed = threading.Event()

        def _execute() -> None:
            try:
                exec_env = {
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "PYTEST_ADDOPTS": "-p no:cacheprovider",
                }
                if environment:
                    exec_env.update({str(key): str(value) for key, value in environment.items() if str(key).strip()})
                result_holder["result"] = container.exec_run(
                    values,
                    workdir=self._workdirs.get(container_id, "/workspace"),
                    environment=exec_env,
                )
            except BaseException as exc:
                result_holder["error"] = exc
            finally:
                completed.set()

        threading.Thread(target=_execute, name=f"code-runner-argv-{container_id[:12]}", daemon=True).start()
        if not completed.wait(remaining):
            self._terminate_timed_out_container(container)
            raise RunnerCommandTimeout()
        if "error" in result_holder:
            raise RunnerUnavailableError("runner_exec_failed") from result_holder["error"]
        observed = result_holder["result"]
        output = _decode_exec_output(getattr(observed, "output", ""))
        return int(getattr(observed, "exit_code", 1)), output[:100000]

    def _preflight(self, run, container_id: str) -> None:
        """Per-call active-lifecycle and run/container binding guard.

        Called on every tool execution so a run that is no longer active, a
        container_id that does not match the run's frozen binding, or a container
        already bound to a different run fails closed before any exec.
        """
        run_id = str(getattr(run, "id", "") or "")
        if str(getattr(run, "status", "")) not in {"pending", "running"}:
            raise RunnerUnavailableError("runner_not_active")
        if getattr(run, "execution_eligible", True) is not True:
            raise RunnerUnavailableError("runner_not_active")
        if getattr(run, "runner_state", "active") != "active":
            raise RunnerUnavailableError("runner_not_active")
        if not run_id or not container_id:
            raise RunnerUnavailableError("runner_exec_failed")
        if str(getattr(run, "container_id", "") or "") != container_id:
            raise RunnerPolicyError("runner_binding_mismatch")
        bound_run_id = self._bindings.get(container_id)
        if bound_run_id is not None and bound_run_id != run_id:
            raise RunnerPolicyError("runner_binding_mismatch")

    def run_tool(
        self,
        run,
        tool: str,
        parameters: dict,
        *,
        output_limit_bytes: int,
        timeout_seconds: int,
    ) -> RunnerToolResponse:
        """Route one approved Code Tool through the bound runner helper.

        Builds a typed ``RunnerToolRequest`` and executes the baked helper with the
        JSON request on stdin and the JSON response on stdout (no shell
        concatenation), enforcing the per-container deadline. The response is
        re-validated via ``RunnerToolResponse.from_dict`` so a malformed or
        over-limit reply fails closed.
        """
        container_id = str(getattr(run, "container_id", "") or "")
        self._preflight(run, container_id)
        request = RunnerToolRequest.from_dict({
            "run_id": str(getattr(run, "id", "") or ""),
            "container_id": container_id,
            "tool": tool,
            "parameters": parameters,
            "request_id": "",
            "output_limit_bytes": output_limit_bytes,
            "helper_version": RUNNER_HELPER_VERSION,
            "schema_version": RUNNER_PROTOCOL_VERSION,
        })
        payload = (json.dumps(request.to_dict()) + "\n").encode("utf-8")
        try:
            container = self.client.containers.get(container_id)
        except Exception as exc:
            raise RunnerUnavailableError("runner_exec_failed") from exc

        remaining = float(timeout_seconds)
        deadline = self._deadlines.get(container_id)
        if deadline is not None:
            remaining = min(remaining, max(0.0, deadline - time.monotonic()))
        if remaining <= 0:
            self._terminate_timed_out_container(container)
            raise RunnerCommandTimeout()

        completed = threading.Event()
        result_holder: dict[str, object] = {}

        def _execute() -> None:
            try:
                exec_id = self.client.api.exec_create(
                    container_id,
                    RUNNER_HELPER_COMMAND,
                    stdout=True,
                    stderr=True,
                    stdin=True,
                    workdir=self._workdirs.get(container_id, RUNNER_WORKSPACE),
                )["Id"]
                sock = self.client.api.exec_start(exec_id, socket=True)
                output = _helper_stdio_exchange(sock, payload)
                result_holder["result"] = (
                    int(self.client.api.exec_inspect(exec_id)["ExitCode"]),
                    output,
                )
            except BaseException as exc:
                result_holder["error"] = exc
            finally:
                completed.set()

        threading.Thread(
            target=_execute,
            name=f"code-runner-tool-{container_id[:12]}",
            daemon=True,
        ).start()
        if not completed.wait(remaining):
            self._terminate_timed_out_container(container)
            raise RunnerCommandTimeout()
        if "error" in result_holder:
            raise RunnerUnavailableError("runner_exec_failed") from result_holder["error"]
        _exit_code, output = result_holder["result"]
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        try:
            response = json.loads(str(output).strip())
        except (TypeError, ValueError) as exc:
            raise RunnerUnavailableError("runner_exec_failed") from exc
        response = RunnerToolResponse.from_dict(response)
        if response.error == "runner_command_timed_out":
            raise RunnerCommandTimeout()
        if response.exit_code in _OOM_EXIT_CODES:
            raise RunnerResourceLimitError("runner_oom_killed")
        if response.error == "runner_exec_failed":
            # The helper could not spawn the frozen command (pids/exec resource
            # exhaustion); tool-level rejections keep their own stable reasons.
            raise RunnerResourceLimitError("runner_resource_limit")
        return response

    @staticmethod
    def _terminate_timed_out_container(container) -> None:
        try:
            container.kill()
        except Exception:
            try:
                container.stop(timeout=0)
            except Exception:
                pass

    def freeze(self, container_id: str) -> None:
        self._deadlines.pop(container_id, None)
        self._bindings.pop(container_id, None)
        self._workdirs.pop(container_id, None)

    def start_stopped(self, container_id: str) -> None:
        try:
            container = self.client.containers.get(container_id)
            container.reload()
        except Exception as exc:
            raise RunnerUnavailableError("runner_exec_failed") from exc
        if str(getattr(container, "status", "") or "") != "running":
            raise RunnerUnavailableError("sandbox_not_running")
