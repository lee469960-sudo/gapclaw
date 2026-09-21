"""Durable occurrence creation for scheduled tasks."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from sqlalchemy import and_, func, or_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.models import ScheduledTask, ScheduledTaskRun, ScheduledTaskSessionSlot
from app.security import new_id
from app.services.scheduled_tasks.tasks import next_run_at
from app.services.scheduled_tasks.observability import emit

CATCH_UP_WINDOW = timedelta(hours=24)
DEFAULT_LEASE_DURATION = timedelta(minutes=5)
MAX_QUEUE_AGE = timedelta(hours=1)


def _as_utc(value: datetime) -> datetime:
    """Normalize SQLite's naive datetime results to the persisted UTC contract."""
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def create_due_runs(db: Session, now: datetime | None = None) -> list[ScheduledTaskRun]:
    now = _as_utc(now or datetime.now(timezone.utc))
    created = []
    tasks = db.query(ScheduledTask).filter(ScheduledTask.enabled == True, ScheduledTask.deleted_at == None, ScheduledTask.next_run_at != None, ScheduledTask.next_run_at <= now).all()
    for task in tasks:
        scheduled_for = _as_utc(task.next_run_at) if task.next_run_at else None
        if not scheduled_for: continue
        if now - scheduled_for > CATCH_UP_WINDOW:
            emit("occurrence_misfire_skipped", task_id=task.id, session_id=task.session_id)
            task.next_run_at = next_run_at(task.schedule_type, task.cron, task.interval_seconds, task.run_at, task.timezone, now)
            continue
        source = "catch_up" if now > scheduled_for else "scheduled"
        key = f"{source}:{scheduled_for.isoformat()}"
        run = ScheduledTaskRun(id=new_id(), task_id=task.id, occurrence_key=key, source=source,
                               scheduled_for=scheduled_for, available_at=now)
        try:
            with db.begin_nested(): db.add(run); db.flush()
            created.append(run)
            emit("occurrence_created", task_id=task.id, run_id=run.id, session_id=task.session_id, source=source)
        except IntegrityError:
            pass
        task.next_run_at = next_run_at(task.schedule_type, task.cron, task.interval_seconds, task.run_at, task.timezone, now)
    db.commit(); return created


def create_manual_run(db: Session, task: ScheduledTask, now: datetime | None = None) -> ScheduledTaskRun:
    """Immediate execution enters the same durable queue as scheduled work."""
    now = _as_utc(now or datetime.now(timezone.utc))
    run = ScheduledTaskRun(id=new_id(), task_id=task.id, occurrence_key=f"manual:{new_id()}", source="manual", scheduled_for=now, available_at=now)
    db.add(run)
    db.commit()
    return run


def retry_failed_run(db: Session, run: ScheduledTaskRun, now: datetime | None = None) -> ScheduledTaskRun:
    if run.state != "failed":
        raise ValueError("scheduled_task_run_not_retryable")
    task = db.get(ScheduledTask, run.task_id)
    if task is None:
        raise ValueError("scheduled_task_missing")
    return create_manual_run(db, task, now)


def claim_next_run(
    db: Session,
    worker_id: str,
    now: datetime | None = None,
    lease_duration: timedelta = DEFAULT_LEASE_DURATION,
) -> ScheduledTaskRun | None:
    """Atomically lease one due run, reclaiming a lease that has expired."""
    if not worker_id:
        raise ValueError("scheduled_task_worker_id_required")
    now = _as_utc(now or datetime.now(timezone.utc))
    expires_at = now + lease_duration
    claimable = or_(
        ScheduledTaskRun.state == "pending",
        and_(ScheduledTaskRun.state == "running", ScheduledTaskRun.lease_expires_at <= now),
    )
    query = db.query(ScheduledTaskRun).filter(
        claimable,
        ScheduledTaskRun.available_at <= now,
    ).order_by(ScheduledTaskRun.scheduled_for, ScheduledTaskRun.id)
    if db.get_bind().dialect.name == "postgresql":
        query = query.with_for_update(skip_locked=True)
    candidate = query.first()
    if candidate is None:
        return None
    task = db.get(ScheduledTask, candidate.task_id)
    if task is None or not _claim_session_slot(db, task.session_id, candidate.id, worker_id, expires_at, now):
        db.rollback()
        return None

    statement = update(ScheduledTaskRun).where(
            ScheduledTaskRun.id == candidate.id,
            claimable,
    ).values(
        state="running",
        started_at=func.coalesce(ScheduledTaskRun.started_at, now),
        lease_owner=worker_id,
        lease_expires_at=expires_at,
    )
    claimed = db.execute(statement.execution_options(synchronize_session=False)).rowcount
    if claimed != 1:
        db.rollback()
        return None
    db.commit()
    emit("lease_claimed", task_id=task.id, run_id=candidate.id, session_id=task.session_id, state="running")
    return db.get(ScheduledTaskRun, candidate.id)


def _claim_session_slot(
    db: Session,
    session_id: str,
    run_id: str,
    worker_id: str,
    expires_at: datetime,
    now: datetime,
) -> bool:
    slot = db.get(ScheduledTaskSessionSlot, session_id)
    if slot is None:
        try:
            with db.begin_nested():
                db.add(ScheduledTaskSessionSlot(
                    session_id=session_id,
                    active_run_id=run_id,
                    lease_owner=worker_id,
                    lease_expires_at=expires_at,
                ))
                db.flush()
            return True
        except IntegrityError:
            slot = db.get(ScheduledTaskSessionSlot, session_id)
    if slot is None:
        return False
    statement = update(ScheduledTaskSessionSlot).where(
        ScheduledTaskSessionSlot.session_id == session_id,
        or_(
            ScheduledTaskSessionSlot.active_run_id == run_id,
            ScheduledTaskSessionSlot.lease_expires_at == None,
            ScheduledTaskSessionSlot.lease_expires_at <= now,
        ),
    ).values(active_run_id=run_id, lease_owner=worker_id, lease_expires_at=expires_at)
    return db.execute(statement.execution_options(synchronize_session=False)).rowcount == 1


def release_session_slot(db: Session, run_id: str, now: datetime | None = None) -> bool:
    """Release a completed run's slot without disturbing a reclaimed owner."""
    now = _as_utc(now or datetime.now(timezone.utc))
    released = db.execute(
        update(ScheduledTaskSessionSlot).where(
            ScheduledTaskSessionSlot.active_run_id == run_id,
        ).values(active_run_id="", lease_owner="", lease_expires_at=now)
        .execution_options(synchronize_session=False)
    ).rowcount
    db.commit()
    return released == 1


def fail_expired_pending_runs(db: Session, now: datetime | None = None) -> int:
    """Make queue starvation terminal and visible instead of silently skipping it."""
    now = _as_utc(now or datetime.now(timezone.utc))
    expired = db.execute(
        update(ScheduledTaskRun).where(
            ScheduledTaskRun.state == "pending",
            ScheduledTaskRun.queued_at < now - MAX_QUEUE_AGE,
        ).values(
            state="failed",
            finished_at=now,
            error_summary="scheduled_task_queue_age_exceeded",
        ).execution_options(synchronize_session=False)
    ).rowcount
    db.commit()
    if expired:
        emit("queue_age_failed", count=expired, state="failed")
    return expired
