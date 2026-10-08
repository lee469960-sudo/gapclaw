"""Feature-gated durable scheduled-task scanner.

Execution stays disabled until the session-runtime integration is complete.  This
Worker can run in shadow mode to verify due-instance calculations safely.
"""
from __future__ import annotations

import logging
import signal
import socket
import threading

from app.config import get_settings
from app.database import SessionLocal
from app.services.scheduled_tasks.scheduler import claim_next_run, run_scheduler_pass
from app.services.scheduled_tasks.runtime import execute_and_finalize_claimed_run
from app.services.scheduled_tasks.lifecycle import finish_run_failure
from app.services.scheduled_tasks.health import heartbeat_worker
from app.startup import init_db

logger = logging.getLogger("app.workers.scheduled_tasks")
_SHUTDOWN = threading.Event()


def _request_shutdown(signum, _frame) -> None:
    logger.info("scheduled_task_worker_shutdown_requested signal=%s", signum)
    _SHUTDOWN.set()


def _install_signal_handlers() -> None:
    signal.signal(signal.SIGTERM, _request_shutdown)
    signal.signal(signal.SIGINT, _request_shutdown)


def run_scheduler_once() -> dict[str, int]:
    settings = get_settings()
    if not settings.scheduled_tasks_worker_enabled:
        logger.info("scheduled_task_worker_disabled")
        return {"created": 0, "expired": 0}
    db = SessionLocal()
    try:
        result = run_scheduler_pass(
            db,
            worker_id=socket.gethostname(),
            shadow_mode=settings.scheduled_tasks_shadow_mode,
            poll_seconds=settings.scheduled_tasks_poll_seconds,
        )
        if settings.scheduled_tasks_shadow_mode:
            logger.info("scheduled_task_worker_shadow_mode")
        logger.info("scheduled_task_scheduler_scan created=%d expired=%d", result["created"], result["expired"])
        return result
    finally:
        db.close()


def run_executor_once() -> dict[str, int]:
    settings = get_settings()
    if not settings.scheduled_tasks_worker_enabled or settings.scheduled_tasks_shadow_mode:
        logger.info("scheduled_task_executor_disabled")
        return {"claimed": 0}
    db = SessionLocal()
    try:
        worker_id = socket.gethostname()
        heartbeat_worker(db, role="executor", worker_id=worker_id, enabled=True, config_summary={
            "single_executor": settings.scheduled_tasks_single_executor,
            "poll_seconds": settings.scheduled_tasks_poll_seconds,
        })
        claimed = claim_next_run(db, worker_id)
        if claimed:
            try:
                execute_and_finalize_claimed_run(db, claimed)
            except Exception as exc:
                finish_run_failure(db, claimed, exc)
            return {"claimed": 1}
        return {"claimed": 0}
    finally:
        db.close()


def run_once() -> dict[str, int]:
    scheduled = run_scheduler_once()
    run_executor_once()
    return scheduled


def main() -> None:
    _install_signal_handlers()
    settings = get_settings()
    if not settings.scheduled_tasks_single_executor:
        raise RuntimeError("scheduled_task_single_executor_required")
    db = SessionLocal()
    try:
        init_db(db)
    finally:
        db.close()
    role = settings.scheduled_tasks_worker_role
    if role == "scheduler":
        _run_loop(run_scheduler_once, settings.scheduled_tasks_poll_seconds)
        return
    if role == "executor":
        _run_loop(run_executor_once, settings.scheduled_tasks_poll_seconds)
        return
    if role != "combined":
        raise RuntimeError("scheduled_task_worker_role_invalid")
    executor = threading.Thread(target=_run_loop, args=(run_executor_once, settings.scheduled_tasks_poll_seconds), daemon=True)
    executor.start()
    _run_loop(run_scheduler_once, settings.scheduled_tasks_poll_seconds)
    executor.join(timeout=5)


def _run_loop(callback, poll_seconds: int) -> None:
    while not _SHUTDOWN.is_set():
        callback()
        _SHUTDOWN.wait(max(1, poll_seconds))


if __name__ == "__main__":
    main()
