import json
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Agent, ScheduledTask, ScheduledTaskNotificationDelivery, ScheduledTaskRun
from app.routers.agent_chat import ChatBody, chat_post
from app.services.scheduled_tasks.authorization import (
    ScheduledTaskAuthorizationError,
    require_session_task_manager,
)


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _user(username, roles='["user"]'):
    return SimpleNamespace(username=username, roles=roles)


def test_session_task_manager_allows_owner_editor_and_admin_only():
    db = _db()
    try:
        agent = Agent(
            id="agent1", name="Agent", creator="owner", allowed_users='["editor"]',
            visibility="public", session_list='[{"session_id":"session1"}]',
        )
        task = ScheduledTask(
            id="task1", agent_id="agent1", session_id="session1", owner_username="owner",
        )
        db.add_all([agent, task])
        db.commit()

        for user in (_user("owner"), _user("editor"), _user("root", '["admin"]')):
            assert require_session_task_manager(db, user, "agent1", "session1", task) is agent
        with pytest.raises(ScheduledTaskAuthorizationError, match="scheduled_task_unauthorized"):
            require_session_task_manager(db, _user("viewer"), "agent1", "session1", task)
    finally:
        db.close()


def test_session_task_manager_hides_cross_session_and_cross_task_details():
    db = _db()
    try:
        db.add(Agent(id="agent1", name="Agent", creator="owner"))
        db.add(ScheduledTask(id="task1", agent_id="agent1", session_id="other", owner_username="owner"))
        db.commit()
        with pytest.raises(ScheduledTaskAuthorizationError, match="scheduled_task_not_found"):
            require_session_task_manager(db, _user("owner"), "agent1", "agent1", db.get(ScheduledTask, "task1"))
    finally:
        db.close()


def test_run_history_requires_in_scope_task_manager():
    db = _db()
    try:
        db.add_all([
            Agent(id="agent1", name="Agent", creator="owner", session_list='[{"session_id":"session1"}]'),
            ScheduledTask(id="task1", agent_id="agent1", session_id="session1", owner_username="owner"),
            ScheduledTaskRun(id="run1", task_id="task1", occurrence_key="manual:1", scheduled_for=datetime.now(timezone.utc), available_at=datetime.now(timezone.utc)),
            ScheduledTaskNotificationDelivery(id="delivery1", run_id="run1", channel_id="channel1", state="failed", error_summary="safe error"),
        ])
        db.commit()
        body = ChatBody(action="list_scheduled_task_runs", task_id="task1", agent_id="agent1", session_id="session1")
        allowed = asyncio.run(chat_post(body, BackgroundTasks(), action=None, user=_user("owner"), db=db))
        denied = asyncio.run(chat_post(body, BackgroundTasks(), action=None, user=_user("intruder"), db=db))
        assert allowed["data"][0]["notifications"][0]["state"] == "failed"
        assert denied["msg"] == "scheduled_task_unauthorized"
    finally:
        db.close()


def test_task_list_returns_execution_count_not_retry_attempt_count():
    db = _db()
    try:
        now = datetime.now(timezone.utc)
        db.add_all([
            Agent(id="agent1", name="Agent", creator="owner", session_list='[{"session_id":"session1"}]'),
            ScheduledTask(id="task1", agent_id="agent1", session_id="session1", owner_username="owner"),
            ScheduledTaskRun(id="done", task_id="task1", occurrence_key="one", state="succeeded", scheduled_for=now, available_at=now),
            ScheduledTaskRun(id="failed", task_id="task1", occurrence_key="two", state="failed", attempt=1, scheduled_for=now, available_at=now),
            ScheduledTaskRun(id="queued", task_id="task1", occurrence_key="three", state="pending", scheduled_for=now, available_at=now),
        ])
        db.commit()
        body = ChatBody(action="list_scheduled_tasks", agent_id="agent1", session_id="session1")
        result = asyncio.run(chat_post(body, BackgroundTasks(), action=None, user=_user("owner"), db=db))
        assert result["data"][0]["execution_count"] == 2
    finally:
        db.close()


def test_stop_scheduled_task_requires_session_access_and_persists_worker_signal():
    db = _db()
    try:
        now = datetime.now(timezone.utc)
        db.add_all([
            Agent(id="agent1", name="Agent", creator="owner", session_list='[{"session_id":"session1"}]'),
            ScheduledTask(id="task1", agent_id="agent1", session_id="session1", owner_username="owner", enabled=True),
            ScheduledTaskRun(id="running", task_id="task1", occurrence_key="running", state="running", scheduled_for=now, available_at=now),
            ScheduledTaskRun(id="pending", task_id="task1", occurrence_key="pending", state="pending", scheduled_for=now, available_at=now),
        ])
        db.commit()
        body = ChatBody(action="stop_scheduled_task", task_id="task1", agent_id="agent1", session_id="session1")
        denied = asyncio.run(chat_post(body, BackgroundTasks(), action=None, user=_user("intruder"), db=db))
        assert denied["msg"] == "scheduled_task_unauthorized"

        allowed = asyncio.run(chat_post(body, BackgroundTasks(), action=None, user=_user("owner"), db=db))
        assert allowed["data"] == {"run_ids": ["running"], "state": "cancelling"}
        assert db.get(ScheduledTask, "task1").enabled is False
        assert db.get(ScheduledTaskRun, "running").cancel_requested_at is not None
        assert db.get(ScheduledTaskRun, "pending").state == "cancelled"
    finally:
        db.close()
