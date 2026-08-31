"""Persistent, retryable cleanup for CodeAgent runner and Workspace resources."""

from __future__ import annotations

import logging
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler

from app.models import CodeAgentRun
from app.services.code_agent.failures import FailureReason, record_code_failure
from app.services.code_agent.workspace import WorkspaceManager


logger = logging.getLogger(__name__)
_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
_scheduler: BackgroundScheduler | None = None


@dataclass(frozen=True)
class LifecycleCleanupResult:
    run_id: str
    outcome: str
    cleanup_attempts: int


def _format_time(value: datetime) -> str:
    return value.strftime(_TIME_FORMAT)


def _parse_time(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, _TIME_FORMAT) if value else None
    except ValueError:
        return None


class CodeLifecycleJanitor:
    def __init__(self, db, *, workspace_manager=None, docker_client=None):
        self.db = db
        self.workspace_manager = workspace_manager or WorkspaceManager()
        if docker_client is None:
            from app.services.docker_service import get_docker_client

            docker_client = get_docker_client()
        self.docker_client = docker_client

    @staticmethod
    def _is_missing(exc: Exception) -> bool:
        return type(exc).__name__ in {"NotFound", "DockerNotFound"}

    @staticmethod
    def _owned_resource(resource, run_id: str) -> bool:
        attrs = getattr(resource, "attrs", {}) or {}
        labels = ((attrs.get("Config") or {}).get("Labels") or {})
        labels = labels or attrs.get("Labels") or {}
        expected_name = f"code-agent-{run_id}"
        bound_run_id = str(labels.get("code_agent.run_id") or "")
        if bound_run_id:
            return bound_run_id == run_id
        return str(getattr(resource, "name", "") or "").lstrip("/") == expected_name

    @staticmethod
    def _is_persistent_sandbox(run) -> bool:
        try:
            facts = json.loads(getattr(run, "source_facts", "") or "{}")
        except (TypeError, json.JSONDecodeError):
            return False
        return isinstance(facts, dict) and facts.get("workspace_mode") == "persistent_sandbox"

    def _remove_container(self, run) -> None:
        container_id = str(run.container_id or "")
        if not container_id:
            return
        if self.docker_client is None:
            raise RuntimeError("docker_unavailable")
        try:
            container = self.docker_client.containers.get(container_id)
        except Exception as exc:
            if self._is_missing(exc):
                return
            raise
        if not self._owned_resource(container, run.id):
            raise RuntimeError("container_binding_mismatch")
        container.remove(force=True)

    def _remove_network(self, run) -> None:
        network_id = str(run.runner_network_id or "")
        if not network_id:
            return
        if self.docker_client is None:
            raise RuntimeError("docker_unavailable")
        try:
            network = self.docker_client.networks.get(network_id)
        except Exception as exc:
            if self._is_missing(exc):
                return
            raise
        if not self._owned_resource(network, run.id):
            raise RuntimeError("network_binding_mismatch")
        network.remove()

    def _cleanup_workspace(self, run, current: datetime) -> None:
        if run.workspace_state == "prepared":
            workspace = Path(run.workspace_path).resolve() if run.workspace_path else None
            if workspace is None or not workspace.is_dir():
                run.workspace_state = "deleted"
                run.workspace_path = ""
                run.retained_until = ""
                run.workspace_downloadable = False
                return
            self.workspace_manager.retain_after_run(run)
            return
        if run.workspace_state == "retained_read_only":
            due = _parse_time(run.retained_until)
            if due is not None and due <= current:
                self.workspace_manager.purge_expired(run, now=current)

    def _schedule_retry(self, run, current: datetime, resource_type: str) -> None:
        run.cleanup_attempts = int(run.cleanup_attempts or 0) + 1
        run.cleanup_state = "failed"
        run.cleanup_error = (
            "workspace_cleanup_failed"
            if resource_type == "workspace"
            else FailureReason.SANDBOX_CLEANUP_FAILED.value
        )
        delay = min(3600, 60 * (2 ** min(run.cleanup_attempts - 1, 6)))
        run.cleanup_next_attempt = _format_time(current + timedelta(seconds=delay))
        run.execution_eligible = False
        if resource_type == "runner":
            run.runner_state = "cleanup_failed"
        record_code_failure(
            self.db,
            actor="lifecycle-janitor",
            reason=FailureReason.SANDBOX_CLEANUP_FAILED,
            project_id=run.project_id,
            run_id=run.id,
            policy_hash=run.effective_policy_hash,
            details={
                "resource_type": resource_type,
                "retry_count": run.cleanup_attempts,
                "cleanup_state": run.cleanup_state,
            },
            commit=False,
        )
        self.db.commit()

    @staticmethod
    def _append_profile_event(run, *, phase: str, status: str, reason: str = "") -> None:
        """Persist a terminal profile event when no live runtime is available."""
        try:
            facts = json.loads(getattr(run, "runner_facts", "") or "{}")
        except (TypeError, json.JSONDecodeError):
            facts = {}
        if not isinstance(facts, dict):
            facts = {}
        events = facts.get("code_profile_events")
        if not isinstance(events, list):
            events = []
        sequence = len(events)
        if events and isinstance(events[-1], dict):
            try:
                sequence = int(events[-1].get("sequence", sequence - 1)) + 1
            except (TypeError, ValueError):
                pass
        events.append({
            "version": 1,
            "profile": "code",
            "phase": phase,
            "status": status,
            "reason": reason,
            "run_id": run.id,
            "manifest_version": run.manifest_version,
            "sequence": sequence,
        })
        facts["code_profile_events"] = events[-500:]
        run.runner_facts = json.dumps(facts, ensure_ascii=False, sort_keys=True)

    def cleanup_run(
        self,
        run: CodeAgentRun,
        *,
        now: datetime | None = None,
        startup_recovery: bool = False,
    ) -> LifecycleCleanupResult:
        current = now or datetime.now()
        due = _parse_time(run.cleanup_next_attempt)
        if due is not None and due > current:
            return LifecycleCleanupResult(run.id, "not_due", run.cleanup_attempts)
        persistent = self._is_persistent_sandbox(run)
        recover_active = startup_recovery and run.status in {"pending", "running"}
        terminal_or_failed = (
            run.status not in {"pending", "running"}
            and (
                (not persistent and (bool(run.container_id) or bool(run.runner_network_id)))
                or run.execution_eligible
                or run.cleanup_state in {"running", "failed"}
            )
        )
        expired_workspace = (
            run.workspace_state == "retained_read_only"
            and (_parse_time(run.retained_until) or datetime.max) <= current
        )
        if not (recover_active or terminal_or_failed or expired_workspace):
            return LifecycleCleanupResult(run.id, "not_required", run.cleanup_attempts)

        if recover_active:
            run.status = "infrastructure_error"
            run.failure_reason = "startup_recovery"
            self._append_profile_event(
                run,
                phase="runtime_result",
                status="failed",
                reason="CodeAgent 服务重启，运行已终止",
            )
        if persistent and (recover_active or terminal_or_failed):
            run.execution_eligible = False
            run.runner_state = "released"
            run.cleanup_state = "completed"
            run.cleanup_error = ""
            run.cleanup_next_attempt = ""
            run.workspace_state = "prepared"
            run.retained_until = ""
            run.workspace_downloadable = False
            if recover_active:
                self._append_profile_event(
                    run,
                    phase="cleanup",
                    status="completed",
                    reason="startup_recovery",
                )
            self.db.commit()
            return LifecycleCleanupResult(run.id, "completed", run.cleanup_attempts)
        run.execution_eligible = False
        run.cleanup_state = "running"
        if run.container_id or run.runner_network_id:
            run.runner_state = "revoking"
        self.db.commit()

        try:
            self._remove_container(run)
            self._remove_network(run)
        except Exception:
            self._schedule_retry(run, current, "runner")
            return LifecycleCleanupResult(run.id, "retry_scheduled", run.cleanup_attempts)
        run.container_id = ""
        run.runner_network_id = ""
        run.runner_state = "removed"
        self.db.commit()

        try:
            self._cleanup_workspace(run, current)
        except Exception:
            self._schedule_retry(run, current, "workspace")
            return LifecycleCleanupResult(run.id, "retry_scheduled", run.cleanup_attempts)

        run.cleanup_state = "completed"
        run.cleanup_error = ""
        run.cleanup_next_attempt = ""
        if recover_active:
            self._append_profile_event(
                run,
                phase="cleanup",
                status="completed",
                reason="startup_recovery",
            )
        self.db.commit()
        return LifecycleCleanupResult(run.id, "completed", run.cleanup_attempts)

    def run_due(
        self,
        *,
        now: datetime | None = None,
        startup_recovery: bool = False,
    ) -> tuple[LifecycleCleanupResult, ...]:
        current = now or datetime.now()
        results = []
        for run in self.db.query(CodeAgentRun).order_by(CodeAgentRun.id).all():
            result = self.cleanup_run(
                run, now=current, startup_recovery=startup_recovery
            )
            if result.outcome != "not_required":
                results.append(result)
        return tuple(results)


def _run_lifecycle_pass(*, startup_recovery: bool = False) -> None:
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        janitor = CodeLifecycleJanitor(db)
        janitor.run_due(startup_recovery=startup_recovery)
        from app.services.code_agent.storage_capacity import StorageCapacityService

        capacity = StorageCapacityService(db, lifecycle_janitor=janitor)
        if not capacity.metrics().admission_allowed:
            capacity.reclaim()
    except Exception:
        db.rollback()
        logger.exception("CodeAgent lifecycle janitor pass failed")
    finally:
        db.close()


def start_lifecycle_janitor() -> None:
    global _scheduler
    if _scheduler is not None:
        return
    _run_lifecycle_pass(startup_recovery=True)
    _scheduler = BackgroundScheduler()
    _scheduler.add_job(
        _run_lifecycle_pass,
        "interval",
        seconds=60,
        id="code_agent_lifecycle_janitor",
        replace_existing=True,
    )
    _scheduler.start()


def stop_lifecycle_janitor() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
