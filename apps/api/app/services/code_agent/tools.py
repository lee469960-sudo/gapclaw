"""Typed CodeAgent V1 tools bound to one frozen run and Workspace."""

from __future__ import annotations

import json
import re
import shlex
from datetime import datetime
from pathlib import Path, PurePosixPath

from app.services.code_agent.output_security import redact_code_output
from app.services.code_agent.budget import update_budget_usage
from app.services.code_agent.runner import (
    RunnerCommandTimeout,
    RunnerResourceLimitError,
    RunnerUnavailableError,
)
from app.services.code_agent.runner_protocol import RunnerProtocolError
from app.services.code_agent.workspace import WorkspaceIntegrityGuard


class CodeToolError(ValueError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


_ACTION_TO_CAPABILITY = {
    "code_read": "read",
    "code_search": "search",
    "code_edit": "edit",
    "code_test": "test",
    "code_shell": "shell",
    "code_git": "git_read",
}

_SHELL_META = re.compile(r"[;&|<>`$(){}\[\]*?~\n\r]")

_SAFE_REJECTION_MESSAGES = {
    "code_tool_unknown": "This tool is not available in the frozen Code Profile.",
    "code_tool_not_allowed": "The frozen Code Profile does not allow this capability.",
    "code_tool_budget_exhausted": "The frozen tool-call budget has been exhausted.",
    "code_tool_invalid_input": "Provide valid structured input for this Code tool.",
    "code_runner_not_active": "The managed Code runner is not active.",
    "code_workspace_not_ready": "The managed Workspace is not available for tool execution.",
    "code_path_not_allowed": "Choose a path inside the frozen allowed scope.",
    "code_file_not_found": "The requested file is not available in the allowed scope.",
    "code_test_not_frozen": "Choose a test from the frozen validation plan.",
    "code_shell_escape_rejected": "Use one exact command from the frozen shell allowlist.",
    "code_shell_command_rejected": "Use one exact command from the frozen shell allowlist.",
    "code_git_write_rejected": "Only frozen read-only Git operations are available.",
    "code_git_query_failed": "The managed read-only Git query could not be completed.",
}


class CodeToolExecutor:
    def __init__(self, db, run, runner):
        self.db = db
        self.run = run
        self.runner = runner
        self.workspace = Path(run.workspace_path).resolve()
        self.policy = json.loads(run.effective_policy or "{}")
        self.contract = json.loads(run.task_contract or "{}")
        self.integrity = WorkspaceIntegrityGuard(run, db)

    def _payload(self, normalized: str) -> dict:
        try:
            payload = json.loads(normalized.split(":", 1)[1].strip())
        except (IndexError, json.JSONDecodeError) as exc:
            raise CodeToolError("code_tool_invalid_input") from exc
        if not isinstance(payload, dict):
            raise CodeToolError("code_tool_invalid_input")
        return payload

    def _consume(self, action: str) -> None:
        if action not in _ACTION_TO_CAPABILITY:
            raise CodeToolError("code_tool_unknown")
        allowed = set(self.policy.get("allowed_tools") or [])
        if _ACTION_TO_CAPABILITY[action] not in allowed:
            raise CodeToolError("code_tool_not_allowed")
        budgets = self.policy.get("budgets") or {}
        limit = int(budgets.get("max_tool_calls") or 200)
        if int(self.run.tool_calls_used or 0) >= limit:
            self.run.status = "budget_exhausted"
            self.run.failure_reason = "tool_call_budget_exhausted"
            update_budget_usage(
                self.db, self.run,
                tool_calls=int(self.run.tool_calls_used or 0),
                max_tool_calls=limit,
            )
            raise CodeToolError("code_tool_budget_exhausted")
        self.run.tool_calls_used = int(self.run.tool_calls_used or 0) + 1
        update_budget_usage(
            self.db, self.run,
            tool_calls=self.run.tool_calls_used,
            max_tool_calls=limit,
        )

    def mark_iteration_budget_exhausted(self) -> None:
        self.run.status = "budget_exhausted"
        self.run.failure_reason = "iteration_budget_exhausted"
        limit = int((self.policy.get("budgets") or {}).get("max_iterations") or 40)
        update_budget_usage(self.db, self.run, iterations=limit, max_iterations=limit)

    def record_iteration(self, iteration: int) -> None:
        update_budget_usage(
            self.db,
            self.run,
            iterations=iteration,
            max_iterations=int((self.policy.get("budgets") or {}).get("max_iterations") or 40),
        )

    def _preflight(self, action: str, normalized: str) -> tuple[dict, str]:
        """Validate frozen policy and runner inputs before any tool side effect."""
        if getattr(self.run, "status", "") not in {"pending", "running"}:
            raise CodeToolError("code_runner_not_active")
        if getattr(self.run, "execution_eligible", True) is not True:
            raise CodeToolError("code_runner_not_active")
        if getattr(self.run, "runner_state", "active") != "active":
            raise CodeToolError("code_runner_not_active")
        if getattr(self.run, "workspace_state", "") != "prepared" or not self.workspace.is_dir():
            raise CodeToolError("code_workspace_not_ready")
        self._consume(action)
        payload = self._payload(normalized)
        path = ""
        if action in {"code_read", "code_edit"}:
            path = self._relative(payload.get("path"))
        elif action == "code_search":
            if not str(payload.get("query") or ""):
                raise CodeToolError("code_tool_invalid_input")
            path = self._relative(
                payload.get("path") or next(iter(self.policy.get("allowed_paths") or []), "")
            )
        elif action == "code_test":
            index = payload.get("test_index", 0)
            plan = self.contract.get("validation_plan") or []
            if not isinstance(index, int) or index < 0 or index >= len(plan) or not isinstance(plan[index], dict):
                raise CodeToolError("code_test_not_frozen")
            if not str(plan[index].get("command") or "").strip():
                raise CodeToolError("code_test_not_frozen")
            if not getattr(self.run, "container_id", ""):
                raise CodeToolError("code_runner_not_active")
        elif action == "code_shell":
            command = str(payload.get("command") or "").strip()
            if not command or _SHELL_META.search(command):
                raise CodeToolError("code_shell_escape_rejected")
            try:
                argv = shlex.split(command)
            except ValueError as exc:
                raise CodeToolError("code_shell_escape_rejected") from exc
            if len(argv) >= 2 and argv[0] == "git" and argv[1] == "commit":
                raise CodeToolError("code_git_write_rejected")
            if command not in (self.policy.get("shell_commands") or []):
                raise CodeToolError("code_shell_command_rejected")
            if not getattr(self.run, "container_id", ""):
                raise CodeToolError("code_runner_not_active")
        elif action == "code_git":
            if str(payload.get("operation") or "").strip() not in {"status", "diff", "log"}:
                raise CodeToolError("code_git_write_rejected")
        return payload, path

    def _relative(self, raw: str) -> str:
        path = PurePosixPath(str(raw or "").replace("\\", "/"))
        if path.is_absolute() or not path.parts or any(part in ("", ".", "..") for part in path.parts):
            raise CodeToolError("code_path_not_allowed")
        rel = path.as_posix()
        allowed_paths = self.policy.get("allowed_paths") or []
        if not any(
            parent in (".", "**")
            or rel == str(parent).strip("/")
            or rel.startswith(str(parent).strip("/") + "/")
            for parent in allowed_paths
        ):
            raise CodeToolError("code_path_not_allowed")
        target = (self.workspace / rel).resolve()
        try:
            target.relative_to(self.workspace)
        except ValueError as exc:
            raise CodeToolError("code_path_not_allowed") from exc
        return rel

    def _audit(
        self,
        action: str,
        status: str,
        path: str = "",
        *,
        classification: str = "none",
        redaction_count: int = 0,
    ) -> None:
        try:
            rows = json.loads(self.run.tool_audit or "[]")
        except json.JSONDecodeError:
            rows = []
        rows.append({
            "run_id": str(getattr(self.run, "id", "") or ""),
            "container_id": str(getattr(self.run, "container_id", "") or ""),
            "policy_hash": str(
                getattr(self.run, "effective_policy_hash", "") or ""
            ),
            "policy_version": int(
                getattr(self.run, "security_schema_version", 0) or 0
            ),
            "action": action if action in _ACTION_TO_CAPABILITY else "unknown_code_tool",
            "status": status,
            "path": path,
            "classification": classification,
            "redaction_count": redaction_count,
            "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })
        self.run.tool_audit = json.dumps(rows[-1000:], sort_keys=True)
        self.db.commit()

    def _output_limit(self) -> int:
        return int((self.policy.get("budgets") or {}).get("output_limit_bytes") or 100_000)

    def _timeout_seconds(self) -> int:
        return int((self.policy.get("budgets") or {}).get("timeout_seconds") or 1800)

    def _tool_request(self, action: str, payload: dict, path: str) -> tuple[str, dict]:
        """Translate an approved action into its typed runner-protocol request.

        The preflight already validated scope, allowlist and input shape; here the
        payload is narrowed to the exact frozen request parameters the runner helper
        accepts (no host Path/subprocess is ever touched).
        """
        if action == "code_read":
            return "read", {"path": path}
        if action == "code_search":
            return "search", {"query": str(payload.get("query") or ""), "path": path}
        if action == "code_edit":
            content = payload.get("content")
            if not isinstance(content, str):
                raise CodeToolError("code_tool_invalid_input")
            return "edit", {"path": path, "content": content}
        if action == "code_git":
            return "git", {"operation": str(payload.get("operation") or "").strip()}
        if action == "code_test":
            index = payload.get("test_index", 0)
            plan = self.contract.get("validation_plan") or []
            command = str(plan[index].get("command") or "").strip()
            return "test", {"command": command}
        if action == "code_shell":
            command = str(payload.get("command") or "").strip()
            return "shell", {"command": command}
        raise CodeToolError("code_tool_unknown")

    @staticmethod
    def _format_result(tool: str, response) -> str:
        """Render a validated runner response into the tool's stable result shape."""
        if tool in ("test", "shell"):
            output = response.stdout
            if response.stderr:
                output = output + ("\n" if output else "") + response.stderr
            return json.dumps({"exit_code": response.exit_code, "output": output})
        return response.stdout

    async def __call__(self, action: str, normalized: str) -> str:
        path = ""
        try:
            payload, path = self._preflight(action, normalized)
            tool, parameters = self._tool_request(action, payload, path)
            if action == "code_edit":
                self.integrity.check_before_write(path)
            elif action in ("code_read", "code_search", "code_test", "code_shell"):
                self.integrity.check_before_seal()
            response = self.runner.run_tool(
                self.run,
                tool,
                parameters,
                output_limit_bytes=self._output_limit(),
                timeout_seconds=self._timeout_seconds(),
            )
            if response.error:
                if response.error == "runner_command_timed_out":
                    raise RunnerCommandTimeout()
                raise CodeToolError(response.error)
            result = self._format_result(tool, response)
            classified = redact_code_output(result)
            self._audit(
                action,
                "completed",
                path,
                classification=classified.classification,
                redaction_count=classified.redaction_count,
            )
            return classified.text
        except RunnerCommandTimeout:
            self.run.status = "timed_out"
            self.run.failure_reason = "runner_command_timed_out"
            self._audit(action, "timed_out", path)
            raise
        except RunnerResourceLimitError as exc:
            self.run.status = "resource_limit_exceeded"
            self.run.failure_reason = exc.reason
            self._audit(action, "resource_limit_exceeded", path)
            raise
        except RunnerUnavailableError:
            self.run.status = "infrastructure_error"
            self.run.failure_reason = "runner_unavailable"
            self._audit(action, "infrastructure_error", path)
            raise
        except RunnerProtocolError as exc:
            self.run.status = "infrastructure_error"
            self.run.failure_reason = exc.reason
            self._audit(action, "infrastructure_error", path)
            raise
        except CodeToolError as exc:
            self._audit(action, "rejected", path)
            return json.dumps({
                "ok": False,
                "reason": exc.reason,
                "message": _SAFE_REJECTION_MESSAGES.get(
                    exc.reason, "The Code tool request was rejected before execution."
                ),
            })
