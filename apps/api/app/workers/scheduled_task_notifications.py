"""Feature-gated notification outbox Worker entry point.

Delivery processing is added with the notification outbox in OpenSpec task 4.4.
Keeping the process separately gated prevents it from becoming an Agent executor.
"""
from __future__ import annotations

import logging
import time

from app.config import get_settings
from app.startup import init_db
from app.database import SessionLocal
from app.services.scheduled_tasks.notifications import deliver_pending

logger = logging.getLogger("app.workers.scheduled_task_notifications")


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
    db = SessionLocal()
    try:
        init_db(db)
    finally:
        db.close()
    settings = get_settings()
    while True:
        run_once()
        time.sleep(max(1, settings.scheduled_tasks_poll_seconds))


if __name__ == "__main__":
    main()
