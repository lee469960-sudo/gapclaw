from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Agent, ChatMessage, ScheduledTask, ScheduledTaskNotificationDelivery, ScheduledTaskRun
from app.services.scheduled_tasks.runtime import ScheduledTaskRuntimeError, bind_run_message, execute_and_finalize_claimed_run, execute_claimed_run
from app.services.scheduled_tasks.lifecycle import finish_run_success


def test_claimed_run_uses_formal_session_runtime_with_source_metadata(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        agent = Agent(id="agent", name="Agent")
        task = ScheduledTask(id="task", agent_id="agent", session_id="session", owner_username="owner", message="scheduled message")
        run = ScheduledTaskRun(id="run", task_id="task", occurrence_key="one", scheduled_for=datetime.now(timezone.utc), available_at=datetime.now(timezone.utc))
        db.add_all((agent, task, run)); db.commit()
        observed = {}

        async def fake_run_agent(received_db, received_agent, session_id, message, **kwargs):
            observed.update(db=received_db, agent=received_agent.id, session_id=session_id, message=message, meta=kwargs["message_meta"])
            return "result"

        monkeypatch.setattr("app.services.scheduled_tasks.runtime.run_agent", fake_run_agent)
        assert execute_claimed_run(db, run) == "result"
        assert observed == {
            "db": db, "agent": "agent", "session_id": "session", "message": "scheduled message",
            "meta": {"source": "scheduled_task", "scheduled_task_run_id": "run", "scheduled_task_source": "scheduled"},
        }
    finally:
        db.close()


def test_run_message_binding_requires_the_durable_scheduled_output():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        task = ScheduledTask(id="task", agent_id="agent", session_id="session", owner_username="owner")
        run = ScheduledTaskRun(id="run", task_id="task", occurrence_key="one", scheduled_for=datetime.now(timezone.utc), available_at=datetime.now(timezone.utc))
        db.add_all((task, run, ChatMessage(agent_id="agent", session_id="session", role="assistant", content="done", meta='{"scheduled_task_run_id":"run"}')))
        db.commit()
        assert bind_run_message(db, run).content == "done"
        assert run.chat_message_id is not None and run.agent_run_id == "scheduled:run"
        run.id = "missing"
        with __import__("pytest").raises(ScheduledTaskRuntimeError, match="message_write_missing"):
            bind_run_message(db, run)
    finally:
        db.close()


def test_bound_or_terminal_run_cannot_trigger_agent_again(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        task = ScheduledTask(id="task", agent_id="agent", session_id="session", owner_username="owner")
        run = ScheduledTaskRun(id="run", task_id="task", occurrence_key="one", state="running", chat_message_id=1, scheduled_for=datetime.now(timezone.utc), available_at=datetime.now(timezone.utc))
        db.add_all((task, run)); db.commit()
        with __import__("pytest").raises(ScheduledTaskRuntimeError, match="already_finalized"):
            execute_claimed_run(db, run)
    finally:
        db.close()


def test_persisted_cancel_request_ends_run_without_success_or_notification(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        now = datetime.now(timezone.utc)
        agent = Agent(id="agent", name="Agent")
        task = ScheduledTask(
            id="task", agent_id="agent", session_id="session", owner_username="owner", message="scheduled message",
            notification_enabled=True, notification_channel_id="channel",
        )
        run = ScheduledTaskRun(id="run", task_id="task", occurrence_key="one", state="running", scheduled_for=now, available_at=now)
        db.add_all((agent, task, run)); db.commit()

        async def fake_run_agent(*_args, **_kwargs):
            run.cancel_requested_at = datetime.now(timezone.utc)
            db.commit()
            await __import__("asyncio").sleep(0.3)
            return "result after cancellation"

        monkeypatch.setattr("app.services.scheduled_tasks.runtime.run_agent", fake_run_agent)
        assert execute_and_finalize_claimed_run(db, run) == ""
        assert db.get(ScheduledTaskRun, "run").state == "cancelled"
        assert db.query(ScheduledTaskNotificationDelivery).count() == 0
    finally:
        db.close()


def test_success_transition_checks_a_late_persisted_cancel_request():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        now = datetime.now(timezone.utc)
        task = ScheduledTask(
            id="task", agent_id="agent", session_id="session", owner_username="owner",
            notification_enabled=True, notification_channel_id="channel",
        )
        run = ScheduledTaskRun(
            id="run", task_id="task", occurrence_key="one", state="running",
            scheduled_for=now, available_at=now, cancel_requested_at=now,
        )
        db.add_all((task, run)); db.commit()

        finish_run_success(db, run)
        assert db.get(ScheduledTaskRun, "run").state == "cancelled"
        assert db.query(ScheduledTaskNotificationDelivery).count() == 0
    finally:
        db.close()
