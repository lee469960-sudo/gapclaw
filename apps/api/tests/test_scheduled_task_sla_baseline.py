import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi import BackgroundTasks
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.database import Base
from app.models import (
    Agent,
    ChatMessage,
    ImChannel,
    ImSession,
    ScheduledTask,
    ScheduledTaskNotificationDelivery,
    ScheduledTaskRun,
)
from app.routers.agent_chat import ChatBody, chat_post
from app.services.channels.mock import MockAdapter
from app.services.scheduled_tasks.notifications import deliver_pending
from app.services.scheduled_tasks.scheduler import claim_next_run, finalize_stale_running_runs
from app.workers import scheduled_tasks


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _user(username: str, roles: str = '["user"]'):
    return SimpleNamespace(username=username, roles=roles)


def test_scheduler_pass_does_not_execute_claimed_agent_run(monkeypatch):
    settings = Settings(
        scheduled_tasks_worker_enabled=True,
        scheduled_tasks_shadow_mode=False,
        scheduled_tasks_single_executor=True,
    )
    executed: list[str] = []

    class _Session:
        def close(self):
            pass

    monkeypatch.setattr(scheduled_tasks, "get_settings", lambda: settings)
    monkeypatch.setattr(scheduled_tasks, "SessionLocal", _Session)
    monkeypatch.setattr(scheduled_tasks, "run_scheduler_pass", lambda *_args, **_kwargs: {"created": 1, "expired": 0})
    monkeypatch.setattr(
        scheduled_tasks,
        "execute_and_finalize_claimed_run",
        lambda _db, _run: executed.append("executor_entered"),
    )

    assert scheduled_tasks.run_scheduler_once() == {"created": 1, "expired": 0}

    assert executed == []


def test_expired_running_run_is_marked_stale_before_compensation_not_reclaimed():
    db = _db()
    now = datetime.now(timezone.utc)
    try:
        db.add_all(
            (
                ScheduledTask(id="task", agent_id="agent", session_id="session", owner_username="owner"),
                ScheduledTaskRun(
                    id="stale-run",
                    task_id="task",
                    occurrence_key="scheduled:stale",
                    state="running",
                    scheduled_for=now - timedelta(minutes=20),
                    available_at=now - timedelta(minutes=20),
                    started_at=now - timedelta(minutes=20),
                    lease_owner="dead-worker",
                    lease_expires_at=now - timedelta(minutes=10),
                ),
            )
        )
        db.commit()

        claimed = claim_next_run(db, "healthy-worker", now)
        finalized = finalize_stale_running_runs(db, now)
        stored = db.get(ScheduledTaskRun, "stale-run")

        assert claimed is None
        assert finalized == 1
        assert stored.state == "failed"
        assert "stale" in (stored.error_summary or "")
    finally:
        db.close()


def test_task_list_exposes_worker_health_and_timing_diagnostics():
    db = _db()
    now = datetime.now(timezone.utc)
    try:
        db.add_all(
            (
                Agent(
                    id="agent",
                    name="Agent",
                    creator="owner",
                    session_list='[{"session_id":"session"}]',
                ),
                ScheduledTask(
                    id="task",
                    agent_id="agent",
                    session_id="session",
                    owner_username="owner",
                    next_run_at=now + timedelta(minutes=10),
                    enabled=True,
                ),
            )
        )
        db.commit()

        body = ChatBody(action="list_scheduled_tasks", agent_id="agent", session_id="session")
        result = asyncio.run(chat_post(body, BackgroundTasks(), action=None, user=_user("owner"), db=db))
        row = result["data"][0]

        assert row["worker_health"]["scheduler"]["status"] in {"healthy", "unhealthy", "missing", "disabled"}
        assert "last_scheduled" in row
        assert "scheduling_delay_seconds" in row
        assert "executor_delay_seconds" in row
        assert "runtime_duration_seconds" in row
        assert "last_status" in row
        assert "skipped_missed_summary" in row
    finally:
        db.close()


def test_notification_failure_stays_separate_from_successful_run_result():
    db = _db()
    now = datetime.now(timezone.utc)
    try:
        db.add_all(
            (
                ImChannel(id="mock", provider="mock", agent_id="agent"),
                ScheduledTask(id="task", agent_id="agent", session_id="session", owner_username="owner"),
                ScheduledTaskRun(
                    id="run",
                    task_id="task",
                    occurrence_key="scheduled:one",
                    state="succeeded",
                    scheduled_for=now,
                    available_at=now,
                    chat_message_id=1,
                ),
                ChatMessage(id=1, agent_id="agent", session_id="session", role="assistant", content="定时任务真实结果"),
                ImSession(channel_id="mock", external_chat_id="chat", agent_id="agent", agent_session_id="session"),
                ScheduledTaskNotificationDelivery(
                    id="delivery",
                    run_id="run",
                    channel_id="missing-channel",
                    destination="chat",
                    attempts=2,
                    next_attempt_at=now,
                ),
            )
        )
        db.commit()
        MockAdapter.outbox.clear()

        assert deliver_pending(db, now) == 0

        run = db.get(ScheduledTaskRun, "run")
        delivery = db.get(ScheduledTaskNotificationDelivery, "delivery")
        assert run.state == "succeeded"
        assert delivery.state == "failed"
        warning = (
            db.query(ChatMessage)
            .filter(
                ChatMessage.agent_id == "agent",
                ChatMessage.session_id == "session",
                ChatMessage.content.like("定时任务结果通知发送失败%"),
            )
            .first()
        )
        assert warning is not None
    finally:
        db.close()
