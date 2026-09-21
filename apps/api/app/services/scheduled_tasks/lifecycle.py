"""State transitions for scheduled-task runs."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models import ScheduledTask, ScheduledTaskNotificationDelivery, ScheduledTaskRun, ScheduledTaskSessionSlot
from app.security import new_id
from app.services.scheduled_tasks.scheduler import _as_utc
from app.services.scheduled_tasks.observability import emit

MAX_ATTEMPTS = 3
RETRY_BASE_DELAY = timedelta(minutes=1)


def is_transient_error(error: Exception | str) -> bool:
    if isinstance(error, (ConnectionError, TimeoutError)):
        return True
    text = str(error).lower()
    return any(token in text for token in ("timeout", "temporar", "connection", "unavailable", "rate limit", "429", "503"))


def finish_run_failure(db: Session, run: ScheduledTaskRun, error: Exception | str, now: datetime | None = None) -> ScheduledTaskRun:
    now = _as_utc(now or datetime.now(timezone.utc))
    attempt = run.attempt + 1
    run.attempt, run.error_summary = attempt, str(error)[:500]
    if is_transient_error(error) and attempt < MAX_ATTEMPTS:
        run.state = "pending"
        run.available_at = now + RETRY_BASE_DELAY * (2 ** (attempt - 1))
        run.lease_owner, run.lease_expires_at = "", None
    else:
        run.state, run.finished_at = "failed", now
        run.lease_owner, run.lease_expires_at = "", None
    _release_slot_in_transaction(db, run.id, now)
    db.commit()
    emit("run_retry" if run.state == "pending" else "run_failed", run_id=run.id, attempt=attempt, state=run.state)
    return run


def finish_run_success(db: Session, run: ScheduledTaskRun, now: datetime | None = None) -> ScheduledTaskRun:
    # The stop request may arrive after the runtime's last cancellation poll
    # but before its success transition. The database is authoritative here.
    db.refresh(run, attribute_names=["cancel_requested_at"])
    if run.cancel_requested_at is not None:
        return finish_run_cancelled(db, run, now)
    now = _as_utc(now or datetime.now(timezone.utc))
    run.state, run.finished_at = "succeeded", now
    run.lease_owner, run.lease_expires_at = "", None
    task = db.get(ScheduledTask, run.task_id)
    if task and task.notification_enabled and task.notification_channel_id:
        db.add(ScheduledTaskNotificationDelivery(
            id=new_id(), run_id=run.id, channel_id=task.notification_channel_id,
            destination=task.notification_chat_id, payload='{"type":"scheduled_task_result"}',
        ))
    _release_slot_in_transaction(db, run.id, now)
    db.commit()
    emit("run_succeeded", run_id=run.id, state="succeeded")
    return run


def finish_run_cancelled(db: Session, run: ScheduledTaskRun, now: datetime | None = None) -> ScheduledTaskRun:
    """End a user-stopped run without treating it as a result delivery."""
    now = _as_utc(now or datetime.now(timezone.utc))
    run.state, run.finished_at = "cancelled", now
    run.error_summary = "scheduled_task_cancelled"
    run.lease_owner, run.lease_expires_at = "", None
    _release_slot_in_transaction(db, run.id, now)
    db.commit()
    emit("run_cancelled", run_id=run.id, state="cancelled")
    return run


def _release_slot_in_transaction(db: Session, run_id: str, now: datetime) -> None:
    db.execute(update(ScheduledTaskSessionSlot).where(
        ScheduledTaskSessionSlot.active_run_id == run_id,
    ).values(active_run_id="", lease_owner="", lease_expires_at=now).execution_options(synchronize_session=False))
