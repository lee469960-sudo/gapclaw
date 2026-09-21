from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.database import Base
from app.models import ChatMessage, ImChannel, ImSession, ScheduledTask, ScheduledTaskNotificationDelivery, ScheduledTaskRun
from app.services.channels.mock import MockAdapter
from app.services.scheduled_tasks.notifications import deliver_pending
from app.services.scheduled_tasks.scheduler import _as_utc


def test_notification_outbox_delivers_or_retries_without_agent_execution():
    engine = create_engine("sqlite:///:memory:"); Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)(); now = datetime.now(timezone.utc)
    try:
        db.add_all((
            ImChannel(id="mock", provider="mock", agent_id="agent"),
            ScheduledTask(id="task", agent_id="agent", session_id="session", owner_username="owner"),
            ScheduledTaskRun(id="run", task_id="task", occurrence_key="one", scheduled_for=now, available_at=now, chat_message_id=1),
            ScheduledTaskRun(id="run2", task_id="task", occurrence_key="two", scheduled_for=now, available_at=now),
            ChatMessage(id=1, agent_id="agent", session_id="session", role="assistant", content="真实会话结果"),
            ImSession(channel_id="mock", external_chat_id="chat", agent_id="agent", agent_session_id="session"),
            ScheduledTaskNotificationDelivery(id="ok", run_id="run", channel_id="mock", destination="chat"),
            ScheduledTaskNotificationDelivery(id="bad", run_id="run2", channel_id="missing", destination="chat"),
        )); db.commit(); MockAdapter.outbox.clear()
        assert deliver_pending(db, now) == 1
        assert db.get(ScheduledTaskNotificationDelivery, "ok").state == "delivered"
        bad = db.get(ScheduledTaskNotificationDelivery, "bad")
        assert bad.state == "pending" and bad.attempts == 1 and _as_utc(bad.next_attempt_at) > now
        for attempt in range(2, 4):
            bad.next_attempt_at = now; db.commit(); deliver_pending(db, now)
        assert bad.state == "failed" and bad.attempts == 3
        assert len(MockAdapter.outbox) == 1
        assert MockAdapter.outbox[0]["text"] == "真实会话结果"
        warning = db.query(ChatMessage).filter(
                ChatMessage.agent_id == "agent",
                ChatMessage.session_id == "session",
                ChatMessage.content.like("定时任务结果通知发送失败%"),
            ).first()
        assert warning is not None
    finally: db.close()


def test_notification_resolves_single_channel_chat_after_agent_rebind():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    now = datetime.now(timezone.utc)
    try:
        db.add_all((
            ImChannel(id="mock", provider="mock", agent_id="new-agent"),
            ScheduledTask(id="task", agent_id="new-agent", session_id="new-session", owner_username="owner"),
            ScheduledTaskRun(id="run", task_id="task", occurrence_key="one", scheduled_for=now, available_at=now, chat_message_id=1),
            ChatMessage(id=1, agent_id="new-agent", session_id="new-session", role="assistant", content="换绑后的真实结果"),
            ImSession(channel_id="mock", external_chat_id="real-chat", agent_id="old-agent", agent_session_id="old-session"),
            ScheduledTaskNotificationDelivery(id="delivery", run_id="run", channel_id="mock", destination="new-session"),
        ))
        db.commit()
        MockAdapter.outbox.clear()

        assert deliver_pending(db, now) == 1

        assert len(MockAdapter.outbox) == 1
        assert MockAdapter.outbox[0]["chat_id"] == "real-chat"
        assert MockAdapter.outbox[0]["text"] == "换绑后的真实结果"
        assert db.get(ScheduledTaskNotificationDelivery, "delivery").state == "delivered"
    finally:
        db.close()
