"""Validated persistence operations for session scheduled tasks."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone as utc_timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models import ScheduledTask, ScheduledTaskRun
from app.security import new_id

MIN_INTERVAL_SECONDS = 300
MAX_ENABLED_PER_SESSION = 20
MAX_ENABLED_PER_OWNER = 100


class ScheduledTaskValidationError(ValueError):
    pass


def validate_schedule(kind: str, cron: str = "", interval_seconds: int = 0,
                      run_at: str = "", timezone: str = "Asia/Shanghai") -> tuple[str, datetime | None]:
    if kind not in {"once", "interval", "cron"}:
        raise ScheduledTaskValidationError("scheduled_task_schedule_type_invalid")
    try:
        ZoneInfo(timezone or "Asia/Shanghai")
    except ZoneInfoNotFoundError:
        raise ScheduledTaskValidationError("scheduled_task_timezone_invalid")
    if kind == "cron":
        try:
            CronTrigger.from_crontab(cron.strip())
        except Exception:
            raise ScheduledTaskValidationError("scheduled_task_cron_invalid")
    if kind == "interval" and int(interval_seconds or 0) < MIN_INTERVAL_SECONDS:
        raise ScheduledTaskValidationError("scheduled_task_interval_too_short")
    if kind == "once":
        try:
            value = datetime.fromisoformat(run_at.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            raise ScheduledTaskValidationError("scheduled_task_run_at_invalid")
        if value.tzinfo is None:
            raise ScheduledTaskValidationError("scheduled_task_run_at_timezone_required")
        return timezone or "Asia/Shanghai", value
    return timezone or "Asia/Shanghai", None


def next_run_at(kind: str, cron: str, interval_seconds: int, run_at: datetime | None,
                timezone: str, now: datetime | None = None) -> datetime | None:
    now = now or datetime.now(utc_timezone.utc)
    if kind == "once":
        return run_at.astimezone(utc_timezone.utc) if run_at and run_at > now else None
    if kind == "interval":
        return (now + timedelta(seconds=interval_seconds)).astimezone(utc_timezone.utc)
    trigger = CronTrigger.from_crontab(cron, timezone=ZoneInfo(timezone))
    next_fire = trigger.get_next_fire_time(None, now)
    return next_fire.astimezone(utc_timezone.utc) if next_fire else None


def create_task(db: Session, *, agent_id: str, session_id: str, owner: str, message: str,
                schedule_type: str, cron: str = "", interval_seconds: int = 0,
                run_at: str = "", timezone: str = "Asia/Shanghai", enabled: bool = True, **options) -> ScheduledTask:
    zone, when = validate_schedule(schedule_type, cron, interval_seconds, run_at, timezone)
    if not (message or "").strip():
        raise ScheduledTaskValidationError("scheduled_task_message_required")
    if enabled:
        session_count = db.query(ScheduledTask).filter_by(agent_id=agent_id, session_id=session_id, enabled=True, deleted_at=None).count()
        owner_count = db.query(ScheduledTask).filter_by(owner_username=owner, enabled=True, deleted_at=None).count()
        if session_count >= MAX_ENABLED_PER_SESSION or owner_count >= MAX_ENABLED_PER_OWNER:
            raise ScheduledTaskValidationError("scheduled_task_quota_exceeded")
    task = ScheduledTask(id=new_id(), agent_id=agent_id, session_id=session_id, owner_username=owner,
                         message=message, schedule_type=schedule_type, cron=cron, interval_seconds=interval_seconds,
                         run_at=when, timezone=zone, next_run_at=next_run_at(schedule_type, cron, interval_seconds, when, zone) if enabled else None, enabled=enabled)
    for key in ("snapshot_enabled", "notification_enabled", "notification_channel_id", "notification_chat_id"):
        if key in options: setattr(task, key, options[key])
    db.add(task); db.commit(); return task


def soft_delete_task(db: Session, task: ScheduledTask) -> None:
    task.enabled = False; task.deleted_at = datetime.now().astimezone()
    cancel_unstarted_runs(db, task.id)
    db.commit()


def update_task(db: Session, task: ScheduledTask, **values) -> ScheduledTask:
    if "message" in values and not (values["message"] or "").strip():
        raise ScheduledTaskValidationError("scheduled_task_message_required")
    kind = values.get("schedule_type", task.schedule_type)
    cron = values.get("cron", task.cron)
    interval = values.get("interval_seconds", task.interval_seconds)
    run_at = values.get("run_at", task.run_at.isoformat() if task.run_at else "")
    zone, when = validate_schedule(kind, cron, interval, run_at, values.get("timezone", task.timezone))
    task.schedule_type, task.cron, task.interval_seconds = kind, cron, interval
    task.run_at, task.timezone = when, zone
    if "message" in values: task.message = values["message"]
    for key in ("snapshot_enabled", "notification_enabled", "notification_channel_id", "notification_chat_id"):
        if key in values: setattr(task, key, values[key])
    if "enabled" in values: task.enabled = bool(values["enabled"])
    task.next_run_at = next_run_at(kind, cron, interval, when, zone) if task.enabled else None
    if not task.enabled:
        cancel_unstarted_runs(db, task.id)
    db.commit(); return task


def cancel_unstarted_runs(db: Session, task_id: str) -> int:
    """Cancellation is intentionally limited to work no Worker has started."""
    return db.execute(update(ScheduledTaskRun).where(
        ScheduledTaskRun.task_id == task_id,
        ScheduledTaskRun.state == "pending",
    ).values(state="cancelled", error_summary="scheduled_task_cancelled").execution_options(
        synchronize_session=False,
    )).rowcount


def request_stop_task(db: Session, task: ScheduledTask) -> list[str]:
    """Persist a stop request that a separate Worker can observe."""
    now = datetime.now(utc_timezone.utc)
    task.enabled, task.next_run_at = False, None
    cancel_unstarted_runs(db, task.id)
    active_runs = db.query(ScheduledTaskRun).filter_by(task_id=task.id, state="running").all()
    for run in active_runs:
        run.cancel_requested_at = now
        run.error_summary = "scheduled_task_cancel_requested"
    db.commit()
    return [run.id for run in active_runs]


def payload(task: ScheduledTask) -> dict:
    return {"id": task.id, "agent_id": task.agent_id, "session_id": task.session_id,
            "message": task.message, "schedule_type": task.schedule_type, "cron": task.cron,
            "interval_seconds": task.interval_seconds, "run_at": task.run_at.isoformat() if task.run_at else "",
            "timezone": task.timezone, "next_run_at": task.next_run_at.isoformat() if task.next_run_at else "", "enabled": task.enabled,
            "snapshot_enabled": task.snapshot_enabled, "notification_enabled": task.notification_enabled,
            "notification_channel_id": task.notification_channel_id, "notification_chat_id": task.notification_chat_id}
