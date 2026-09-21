"""Independent retrying delivery for scheduled-task notification outbox."""
from __future__ import annotations
import asyncio
from datetime import datetime, timedelta, timezone
from sqlalchemy import or_
from sqlalchemy.orm import Session
from app.models import ChatMessage, ImChannel, ImSession, ScheduledTask, ScheduledTaskNotificationDelivery, ScheduledTaskRun
from app.security import now_str
from app.services.channels.base import create_adapter

MAX_DELIVERY_ATTEMPTS = 3


def _result_text(db: Session, run: ScheduledTaskRun) -> str:
    message = db.get(ChatMessage, run.chat_message_id) if run.chat_message_id else None
    text = (message.content if message else "").strip()
    if not text:
        raise RuntimeError("scheduled_task_notification_result_missing")
    return text


def _resolve_destination(
    db: Session,
    channel: ImChannel,
    row: ScheduledTaskNotificationDelivery,
    task: ScheduledTask,
) -> str:
    session = (
        db.query(ImSession)
        .filter_by(channel_id=row.channel_id, agent_session_id=row.destination)
        .first()
    )
    if session and session.external_chat_id:
        return session.external_chat_id

    agent_sessions = (
        db.query(ImSession)
        .filter_by(channel_id=row.channel_id, agent_id=task.agent_id)
        .order_by(ImSession.updated_at.desc())
        .all()
    )
    agent_chat_ids = {item.external_chat_id for item in agent_sessions if item.external_chat_id}
    if len(agent_chat_ids) == 1:
        return next(iter(agent_chat_ids))

    if channel.agent_id == task.agent_id:
        channel_sessions = db.query(ImSession).filter_by(channel_id=row.channel_id).all()
        channel_chat_ids = {item.external_chat_id for item in channel_sessions if item.external_chat_id}
        if len(channel_chat_ids) == 1:
            return next(iter(channel_chat_ids))

    raise RuntimeError("scheduled_task_notification_destination_missing")


def _failure_notice(reason: str) -> str:
    reason = (reason or "未知原因").strip()
    return f"定时任务结果通知发送失败：{reason[:180]}。请在此会话查看执行结果。"


def deliver_pending(db: Session, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    rows = db.query(ScheduledTaskNotificationDelivery).filter(
        ScheduledTaskNotificationDelivery.state == "pending",
        or_(
            ScheduledTaskNotificationDelivery.next_attempt_at == None,
            ScheduledTaskNotificationDelivery.next_attempt_at <= now,
        ),
    ).all()
    delivered = 0
    for row in rows:
        channel = db.get(ImChannel, row.channel_id)
        try:
            if not channel:
                raise RuntimeError("scheduled_task_notification_channel_missing")
            run = db.get(ScheduledTaskRun, row.run_id)
            task = db.get(ScheduledTask, run.task_id) if run else None
            if not run or not task:
                raise RuntimeError("scheduled_task_notification_run_missing")
            destination = _resolve_destination(db, channel, row, task)
            asyncio.run(create_adapter(channel.provider, channel.id, channel.get_config()).send_text(destination, _result_text(db, run)))
            row.state, row.delivered_at, row.error_summary = "delivered", now, ""
            delivered += 1
        except Exception as exc:
            row.attempts += 1
            row.error_summary = str(exc)[:500]
            if row.attempts >= MAX_DELIVERY_ATTEMPTS:
                row.state = "failed"
                run = db.get(ScheduledTaskRun, row.run_id)
                task = db.get(ScheduledTask, run.task_id) if run else None
                if task:
                    db.add(ChatMessage(
                        agent_id=task.agent_id, session_id=task.session_id, role="assistant",
                        content=_failure_notice(row.error_summary),
                        meta='{"source":"scheduled_task_notification"}', created_at=now_str(),
                    ))
            else:
                row.next_attempt_at = now + timedelta(minutes=2 ** (row.attempts - 1))
        row.attempted_at = now
    db.commit()
    return delivered
