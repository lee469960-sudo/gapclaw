"""Feature-gated durable scheduled-task scanner.

Execution stays disabled until the session-runtime integration is complete.  This
Worker can run in shadow mode to verify due-instance calculations safely.
"""
from __future__ import annotations

import logging
import socket
import time

from app.config import get_settings
from app.database import SessionLocal
from app.services.scheduled_tasks.scheduler import claim_next_run, create_due_runs, fail_expired_pending_runs
from app.services.scheduled_tasks.runtime import execute_and_finalize_claimed_run
from app.services.scheduled_tasks.lifecycle import finish_run_failure
from app.startup import init_db

logger = logging.getLogger("app.workers.scheduled_tasks")


def run_once() -> dict[str, int]:
    settings = get_settings()
    if not settings.scheduled_tasks_worker_enabled:
        logger.info("scheduled_task_worker_disabled")
        return {"created": 0, "expired": 0}
    db = SessionLocal()
    try:
        if settings.scheduled_tasks_shadow_mode:
            logger.info("scheduled_task_worker_shadow_mode")
            return {"created": 0, "expired": 0}
        created = create_due_runs(db)
        expired = fail_expired_pending_runs(db)
        claimed = claim_next_run(db, socket.gethostname())
        if claimed:
            try:
                execute_and_finalize_claimed_run(db, claimed)
            except Exception as exc:
                finish_run_failure(db, claimed, exc)
        logger.info("scheduled_task_worker_scan created=%d expired=%d", len(created), expired)
        return {"created": len(created), "expired": expired}
    finally:
        db.close()


def main() -> None:
    settings = get_settings()
    if not settings.scheduled_tasks_single_executor:
        raise RuntimeError("scheduled_task_single_executor_required")
    db = SessionLocal()
    try:
        init_db(db)
    finally:
        db.close()
    while True:
        run_once()
        time.sleep(max(1, settings.scheduled_tasks_poll_seconds))


if __name__ == "__main__":
    main()
