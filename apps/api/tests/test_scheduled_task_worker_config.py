from app.config import Settings
from app.workers import scheduled_task_notifications, scheduled_tasks


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
    assert scheduled_task_notifications.run_once() == 0


def test_worker_module_wires_claimed_runs_to_formal_finalizer():
    assert callable(scheduled_tasks.claim_next_run)
    assert callable(scheduled_tasks.execute_and_finalize_claimed_run)


def test_worker_entrypoints_initialize_schema_with_a_database_session(monkeypatch):
    settings = Settings(scheduled_tasks_single_executor=True)
    for module in (scheduled_tasks, scheduled_task_notifications):
        initialized = []
        monkeypatch.setattr(module, "get_settings", lambda: settings)
        monkeypatch.setattr(module, "SessionLocal", _Db)
        monkeypatch.setattr(module, "init_db", lambda db: initialized.append(db))
        monkeypatch.setattr(module, "run_once", lambda: None)
        monkeypatch.setattr(module, "_install_signal_handlers", lambda: None)
        monkeypatch.setattr(module, "_SHUTDOWN", _StopEvent())
        try:
            module.main()
        except RuntimeError as exc:
            assert str(exc) == "stop"
        assert len(initialized) == 1
