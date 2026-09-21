import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Agent, ScheduledTask, ScheduledTaskRun, ScheduledTaskProgress
from app.routers.agent_chat import ChatBody, chat_post
from app.services.agent_runtime.hub import hub
from app.services.scheduled_tasks.runtime import execute_and_finalize_claimed_run, execute_claimed_run, ScheduledTaskRuntimeError
from app.services.scheduled_tasks.tasks import create_task, update_task, ScheduledTaskValidationError


def test_worker_progress_is_visible_to_another_connection_and_authorized(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'progress.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    now = datetime.now(timezone.utc)
    with factory() as db:
        db.add_all([
            Agent(id="a", name="A", creator="owner"),
            ScheduledTask(id="t", agent_id="a", session_id="a", owner_username="owner", message="check"),
            ScheduledTaskRun(id="r", task_id="t", occurrence_key="one", state="running", scheduled_for=now, available_at=now),
        ])
        db.commit()

        async def runtime(*args, **kwargs):
            await hub.publish("a:a", {"type": "step", "op": "append", "index": 0, "step": {"type": "tool", "title": "Get positions", "status": "running"}})
            # Independent session reads while the Worker is still executing.
            with factory() as reader:
                body = ChatBody(action="scheduled_task_progress", agent_id="a", session_id="a")
                result = await chat_post(body, BackgroundTasks(), action=None, user=SimpleNamespace(username="owner", roles='[]'), db=reader)
                assert result["data"]["steps"][0]["status"] == "running"
                denied = await chat_post(body, BackgroundTasks(), action=None, user=SimpleNamespace(username="other", roles='[]'), db=reader)
                assert denied["msg"] == "scheduled_task_unauthorized"
            await hub.publish("a:a", {"type": "step", "op": "patch", "index": 0, "step": {"status": "done"}})
            return "done"

        monkeypatch.setattr("app.services.scheduled_tasks.runtime.run_agent", runtime)
        assert execute_claimed_run(db, db.get(ScheduledTaskRun, "r")) == "done"
        with factory() as reader:
            assert json.loads(reader.get(ScheduledTaskProgress, "r").steps)[0]["status"] == "done"
        assert not hub._subs.get("a:a")


def test_empty_trigger_and_output_are_not_executed_or_successful(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        with pytest.raises(ScheduledTaskValidationError, match="message_required"):
            create_task(db, agent_id="a", session_id="a", owner="owner", message=" \n", schedule_type="interval", interval_seconds=300)
        task = create_task(db, agent_id="a", session_id="a", owner="owner", message="check", schedule_type="interval", interval_seconds=300)
        with pytest.raises(ScheduledTaskValidationError, match="message_required"):
            update_task(db, task, message=" ")
        now = datetime.now(timezone.utc)
        run = ScheduledTaskRun(id="r", task_id=task.id, occurrence_key="one", state="running", scheduled_for=now, available_at=now)
        db.add_all([Agent(id="a", name="A"), run])
        task.message = " "
        db.commit()
        with pytest.raises(ScheduledTaskRuntimeError, match="message_empty"):
            execute_claimed_run(db, run)
        task.message = "check"
        db.commit()
        async def empty(*args, **kwargs):
            return " "
        monkeypatch.setattr("app.services.scheduled_tasks.runtime.run_agent", empty)
        with pytest.raises(ScheduledTaskRuntimeError, match="output_empty"):
            execute_and_finalize_claimed_run(db, run)
        assert run.state != "succeeded"
