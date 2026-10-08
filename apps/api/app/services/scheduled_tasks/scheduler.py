"""Durable occurrence creation for scheduled tasks."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from sqlalchemy import func, or_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.models import ScheduledTask, ScheduledTaskRun, ScheduledTaskSessionSlot, ScheduledTaskWorkerHeartbeat
from app.security import new_id
from app.services.scheduled_tasks.tasks import next_run_at
from app.services.scheduled_tasks.observability import emit
from app.services.scheduled_tasks.health import HEALTH_STALE_AFTER, heartbeat_worker

DEFAULT_LEASE_DURATION = timedelta(minutes=5)
MAX_QUEUE_AGE = timedelta(hours=1)
MAX_CRON_MISSED_SCAN = 1000


def _as_utc(value: datetime) -> datetime:
    """Normalize SQLite's naive datetime results to the persisted UTC contract."""
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _latest_due_window(task: ScheduledTask, scheduled_for: datetime, now: datetime) -> tuple[datetime, int]:
    """Return the latest due scheduled time and older missed-window count."""
    scheduled_for = _as_utc(scheduled_for)
    now = _as_utc(now)
    if scheduled_for >= now:
        return scheduled_for, 0
    if task.schedule_type == "interval" and int(task.interval_seconds or 0) > 0:
        interval = int(task.interval_seconds)
        due_count = int((now - scheduled_for).total_seconds() // interval) + 1
        latest = scheduled_for + timedelta(seconds=interval * max(0, due_count - 1))
        return _as_utc(latest), max(0, due_count - 1)
    if task.schedule_type == "cron" and task.cron:
        latest = scheduled_for
        missed = 0
        cursor = scheduled_for + timedelta(seconds=1)
        for _ in range(MAX_CRON_MISSED_SCAN):
            candidate = next_run_at(task.schedule_type, task.cron, task.interval_seconds, task.run_at, task.timezone, cursor)
            if candidate is None or _as_utc(candidate) > now:
                break
            latest = _as_utc(candidate)
            missed += 1
            cursor = latest + timedelta(seconds=1)
        return latest, missed
    return scheduled_for, 0


def _record_missed_summary(
    db: Session,
    task: ScheduledTask,
    first_missed: datetime,
    latest_due: datetime,
    skipped_count: int,
    now: datetime,
) -> None:
    if skipped_count <= 0:
        return
    key = f"missed:{first_missed.isoformat()}:{skipped_count}"
    run = ScheduledTaskRun(
        id=new_id(),
        task_id=task.id,
        occurrence_key=key[:96],
        source="missed",
        state="skipped",
        scheduled_for=first_missed,
        available_at=now,
        finished_at=now,
        error_summary=f"scheduled_task_missed_windows:{skipped_count}; latest={latest_due.isoformat()}",
    )
    try:
        with db.begin_nested():
            db.add(run)
            db.flush()
        emit("occurrence_misfire_skipped", task_id=task.id, run_id=run.id, session_id=task.session_id, count=skipped_count)
    except IntegrityError:
        pass


def _has_active_overlap(db: Session, task: ScheduledTask) -> bool:
    return db.query(ScheduledTaskRun).join(
        ScheduledTask, ScheduledTask.id == ScheduledTaskRun.task_id,
    ).filter(
        ScheduledTask.session_id == task.session_id,
        ScheduledTaskRun.state.in_(("pending", "running")),
    ).first() is not None


def _record_overlap_skip(db: Session, task: ScheduledTask, scheduled_for: datetime, now: datetime) -> None:
    key = f"coalesced:{scheduled_for.isoformat()}"
    run = ScheduledTaskRun(
        id=new_id(),
        task_id=task.id,
        occurrence_key=key[:96],
        source="coalesced",
        state="skipped",
        scheduled_for=scheduled_for,
        available_at=now,
        finished_at=now,
        error_summary="scheduled_task_overlap_coalesced",
    )
    try:
        with db.begin_nested():
            db.add(run)
            db.flush()
        emit("occurrence_misfire_skipped", task_id=task.id, run_id=run.id, session_id=task.session_id, source="coalesced")
    except IntegrityError:
        pass


def create_due_runs(db: Session, now: datetime | None = None) -> list[ScheduledTaskRun]:
    now = _as_utc(now or datetime.now(timezone.utc))
    created = []
    tasks = db.query(ScheduledTask).filter(ScheduledTask.enabled == True, ScheduledTask.deleted_at == None, ScheduledTask.next_run_at != None, ScheduledTask.next_run_at <= now).all()
    for task in tasks:
        scheduled_for = _as_utc(task.next_run_at) if task.next_run_at else None
        if not scheduled_for: continue
        latest_due, skipped_count = _latest_due_window(task, scheduled_for, now)
        _record_missed_summary(db, task, scheduled_for, latest_due, skipped_count, now)
        if _has_active_overlap(db, task):
            _record_overlap_skip(db, task, latest_due, now)
            task.next_run_at = next_run_at(task.schedule_type, task.cron, task.interval_seconds, task.run_at, task.timezone, now)
            continue
        source = "catch_up" if now > scheduled_for else "scheduled"
        key = f"{source}:{latest_due.isoformat()}"
        run = ScheduledTaskRun(id=new_id(), task_id=task.id, occurrence_key=key, source=source,
                               scheduled_for=latest_due, available_at=now)
        try:
            with db.begin_nested(): db.add(run); db.flush()
            created.append(run)
            emit("occurrence_created", task_id=task.id, run_id=run.id, session_id=task.session_id, source=source)
        except IntegrityError:
            pass
        task.next_run_at = next_run_at(task.schedule_type, task.cron, task.interval_seconds, task.run_at, task.timezone, now)
    db.commit(); return created


def run_scheduler_pass(
    db: Session,
    *,
    worker_id: str,
    now: datetime | None = None,
    shadow_mode: bool = False,
    poll_seconds: int | None = None,
) -> dict[str, int]:
    """Lightweight scheduler pass: heartbeat, create due rows, and return.

    This function must not claim runs, execute Agents, call MCP/LLM providers, or
    deliver notifications. Execution belongs to the executor pass.
    """
    now = _as_utc(now or datetime.now(timezone.utc))
    heartbeat_worker(db, role="scheduler", worker_id=worker_id, enabled=True, now=now, config_summary={
        "shadow_mode": bool(shadow_mode),
        "poll_seconds": poll_seconds,
    })
    if shadow_mode:
        return {"created": 0, "expired": 0}
    finalize_stale_running_runs(db, now)
    created = create_due_runs(db, now)
    expired = fail_expired_pending_runs(db, now)
    return {"created": len(created), "expired": expired}


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
    """Atomically lease one pending due run."""
    if not worker_id:
        raise ValueError("scheduled_task_worker_id_required")
    now = _as_utc(now or datetime.now(timezone.utc))
    expires_at = now + lease_duration
    claimable = ScheduledTaskRun.state == "pending"
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


def _worker_is_unhealthy(db: Session, worker_id: str, now: datetime) -> bool:
    if not worker_id:
        return True
    row = db.get(ScheduledTaskWorkerHeartbeat, f"executor:{worker_id}")
    if row is None or not row.enabled:
        return True
    return now - _as_utc(row.last_heartbeat_at) > HEALTH_STALE_AFTER


def finalize_stale_running_runs(db: Session, now: datetime | None = None) -> int:
    """Fail expired running work owned by missing or stale executor Workers."""
    now = _as_utc(now or datetime.now(timezone.utc))
    rows = db.query(ScheduledTaskRun).filter(
        ScheduledTaskRun.state == "running",
        ScheduledTaskRun.lease_expires_at != None,
        ScheduledTaskRun.lease_expires_at <= now,
    ).all()
    finalized = 0
    for run in rows:
        if not _worker_is_unhealthy(db, run.lease_owner, now):
            continue
        run.state = "failed"
        run.finished_at = now
        run.error_summary = "scheduled_task_stale_worker"
        run.lease_owner = ""
        run.lease_expires_at = None
        db.execute(update(ScheduledTaskSessionSlot).where(
            ScheduledTaskSessionSlot.active_run_id == run.id,
        ).values(active_run_id="", lease_owner="", lease_expires_at=now).execution_options(synchronize_session=False))
        finalized += 1
        emit("run_failed", run_id=run.id, state="failed")
    db.commit()
    return finalized
