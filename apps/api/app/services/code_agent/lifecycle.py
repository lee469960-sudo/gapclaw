"""Code run termination and resource cleanup hooks for the unified runtime."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable


CleanupHook = Callable[[], Awaitable[None] | None]


class CodeCleanupError(RuntimeError):
    def __init__(self, failures: list[str]):
        self.failures = failures
        super().__init__("cleanup_failed")


_chat_runs: dict[str, str] = {}
_termination_reasons: dict[str, str] = {}
_cleanup_hooks: dict[str, list[CleanupHook]] = {}


def bind_code_run(chat_key: str, run_id: str) -> None:
    _chat_runs[chat_key] = run_id


def active_code_run(chat_key: str) -> str:
    return _chat_runs.get(chat_key, "")


def request_code_termination(chat_key: str, reason: str) -> None:
    run_id = _chat_runs.get(chat_key)
    if run_id:
        _termination_reasons[run_id] = reason


def code_termination_reason(run_id: str) -> str:
    return _termination_reasons.get(run_id, "")


def register_code_cleanup(run_id: str, hook: CleanupHook) -> None:
    _cleanup_hooks.setdefault(run_id, []).append(hook)


async def cleanup_code_resources(
    chat_key: str,
    run_id: str,
    *,
    db=None,
    terminal_status: str = "",
    failure_reason: str = "",
) -> None:
    run = None
    if db is not None:
        from app.models import CodeAgentRun

        run = db.get(CodeAgentRun, run_id)
    _chat_runs.pop(chat_key, None)
    if run is not None:
        if terminal_status:
            run.status = terminal_status
            run.failure_reason = failure_reason
        run.execution_eligible = False
        run.runner_state = "revoking"
        run.cleanup_state = "running"
        db.commit()
    failures: list[str] = []
    for hook in reversed(_cleanup_hooks.pop(run_id, [])):
        try:
            result = hook()
            if inspect.isawaitable(result):
                await result
        except Exception as exc:
            failures.append(f"{type(exc).__name__}: {exc}"[:300])
    _termination_reasons.pop(run_id, None)
    if failures:
        if run is not None:
            run.runner_state = "cleanup_failed"
            run.cleanup_state = "failed"
            db.commit()
        raise CodeCleanupError(failures)
    if run is not None:
        persistent = False
        try:
            import json

            facts = json.loads(run.source_facts or "{}")
            persistent = isinstance(facts, dict) and facts.get("workspace_mode") == "persistent_sandbox"
        except Exception:
            persistent = False
        if not persistent:
            run.container_id = ""
            run.runner_network_id = ""
            run.runner_state = "removed"
        else:
            run.runner_state = "released"
        run.cleanup_state = "completed"
        db.commit()
