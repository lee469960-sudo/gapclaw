from app.config import Settings
from app.workers import scheduled_task_notifications, scheduled_tasks
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


class _Db:
    def close(self):
        pass


class _StopEvent:
    def is_set(self):
        return False

    def wait(self, _timeout):
        raise RuntimeError("stop")


def test_scheduled_task_workers_default_to_safe_disabled(monkeypatch):
    settings = Settings()
    assert settings.scheduled_tasks_worker_enabled is False
    assert settings.scheduled_task_notifications_worker_enabled is False
    monkeypatch.setattr(scheduled_tasks, "get_settings", lambda: settings)
    monkeypatch.setattr(scheduled_task_notifications, "get_settings", lambda: settings)
    assert scheduled_tasks.run_once() == {"created": 0, "expired": 0}
    assert scheduled_tasks.run_scheduler_once() == {"created": 0, "expired": 0}
    assert scheduled_tasks.run_executor_once() == {"claimed": 0}
    assert scheduled_task_notifications.run_once() == 0


def test_worker_module_wires_claimed_runs_to_formal_finalizer():
    assert callable(scheduled_tasks.claim_next_run)
    assert callable(scheduled_tasks.execute_and_finalize_claimed_run)


def test_worker_entrypoints_initialize_schema_with_a_database_session(monkeypatch):
    settings = Settings(scheduled_tasks_single_executor=True)
    initialized = []
    scheduler_settings = settings.model_copy(update={"scheduled_tasks_worker_role": "scheduler"})
    monkeypatch.setattr(scheduled_tasks, "get_settings", lambda: scheduler_settings)
    monkeypatch.setattr(scheduled_tasks, "SessionLocal", _Db)
    monkeypatch.setattr(scheduled_tasks, "init_db", lambda db: initialized.append(db))
    monkeypatch.setattr(scheduled_tasks, "run_scheduler_once", lambda: None)
    monkeypatch.setattr(scheduled_tasks, "run_executor_once", lambda: None)
    monkeypatch.setattr(scheduled_tasks, "_install_signal_handlers", lambda: None)
    monkeypatch.setattr(scheduled_tasks, "_SHUTDOWN", _StopEvent())
    try:
        scheduled_tasks.main()
    except RuntimeError as exc:
        assert str(exc) == "stop"
    assert len(initialized) == 1

    initialized = []
    monkeypatch.setattr(scheduled_task_notifications, "get_settings", lambda: settings)
    monkeypatch.setattr(scheduled_task_notifications, "SessionLocal", _Db)
    monkeypatch.setattr(scheduled_task_notifications, "init_db", lambda db: initialized.append(db))
    monkeypatch.setattr(scheduled_task_notifications, "run_once", lambda: None)
    monkeypatch.setattr(scheduled_task_notifications, "_install_signal_handlers", lambda: None)
    monkeypatch.setattr(scheduled_task_notifications, "_SHUTDOWN", _StopEvent())
    try:
        scheduled_task_notifications.main()
    except RuntimeError as exc:
        assert str(exc) == "stop"
    assert len(initialized) == 1


def test_scheduled_task_worker_rejects_unknown_role(monkeypatch):
    settings = Settings(scheduled_tasks_single_executor=True, scheduled_tasks_worker_role="bad")
    monkeypatch.setattr(scheduled_tasks, "get_settings", lambda: settings)
    monkeypatch.setattr(scheduled_tasks, "SessionLocal", _Db)
    monkeypatch.setattr(scheduled_tasks, "init_db", lambda _db: None)
    monkeypatch.setattr(scheduled_tasks, "_install_signal_handlers", lambda: None)
    with __import__("pytest").raises(RuntimeError, match="worker_role_invalid"):
        scheduled_tasks.main()


def test_deployment_configs_make_scheduled_worker_roles_explicit():
    compose = (ROOT / "deploy/docker-compose.yml").read_text(encoding="utf-8")
    prod = (ROOT / "deploy/docker-compose.prod.yml").read_text(encoding="utf-8")
    local = (ROOT / "scripts/start-local.sh").read_text(encoding="utf-8")
    stop = (ROOT / "scripts/stop-local.sh").read_text(encoding="utf-8")

    for source in (compose, prod):
        assert "scheduled-task-scheduler:" in source
        assert "scheduled-task-executor:" in source
        assert "scheduled-task-notification-worker:" in source
        assert "SCHEDULED_TASKS_WORKER_ROLE: scheduler" in source
        assert "SCHEDULED_TASKS_WORKER_ROLE: executor" in source
        assert "SCHEDULED_TASK_EXECUTION_TIMEOUT_SECONDS" in source

    assert "SCHEDULED_TASKS_WORKER_ROLE=scheduler" in local
    assert "SCHEDULED_TASKS_WORKER_ROLE=executor" in local
    assert "scheduled-task-scheduler.pid" in local
    assert "scheduled-task-executor.pid" in local
    assert "stop_pid scheduled-task-scheduler" in stop
    assert "stop_pid scheduled-task-executor" in stop
