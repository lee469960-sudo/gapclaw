"""Feature-gated notification outbox Worker entry point.

Delivery processing is added with the notification outbox in OpenSpec task 4.4.
Keeping the process separately gated prevents it from becoming an Agent executor.
"""
from __future__ import annotations

import logging
import signal
import threading

from app.config import get_settings
from app.startup import init_db
from app.database import SessionLocal
from app.services.scheduled_tasks.notifications import deliver_pending

logger = logging.getLogger("app.workers.scheduled_task_notifications")
_SHUTDOWN = threading.Event()


def _request_shutdown(signum, _frame) -> None:
    logger.info("scheduled_task_notification_worker_shutdown_requested signal=%s", signum)
    _SHUTDOWN.set()


def _install_signal_handlers() -> None:
    signal.signal(signal.SIGTERM, _request_shutdown)
    signal.signal(signal.SIGINT, _request_shutdown)


def run_once() -> int:
    if not get_settings().scheduled_task_notifications_worker_enabled:
        logger.info("scheduled_task_notification_worker_disabled")
        return 0
    db = SessionLocal()
    try:
        return deliver_pending(db)
    finally:
        db.close()


def main() -> None:
    _install_signal_handlers()
    db = SessionLocal()
    try:
        init_db(db)
    finally:
        db.close()
    settings = get_settings()
    while not _SHUTDOWN.is_set():
        run_once()
        _SHUTDOWN.wait(max(1, settings.scheduled_tasks_poll_seconds))


if __name__ == "__main__":
    main()
