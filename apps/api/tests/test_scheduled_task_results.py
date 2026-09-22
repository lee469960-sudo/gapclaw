import json
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import (
    ChatMessage,
    ScheduledTask,
    ScheduledTaskNotificationDelivery,
    ScheduledTaskProgress,
    ScheduledTaskRun,
)
from app.services.scheduled_tasks.results import serialize_scheduled_run_result
from app.services.scheduled_tasks.runtime import SCHEDULED_NO_PROGRESS_REPLY


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _run(run_id: str, task_id: str, state: str, **values) -> ScheduledTaskRun:
    now = datetime.now(timezone.utc)
    return ScheduledTaskRun(
        id=run_id,
        task_id=task_id,
        occurrence_key=run_id,
        state=state,
        scheduled_for=now,
        available_at=now,
        started_at=now,
        finished_at=now,
        **values,
    )


def test_terminal_result_serializer_covers_success_and_redacts_preview():
    db = _db()
    try:
        secret = "sk-" + "z" * 20
        db.add(ScheduledTask(id="task", agent_id="agent", session_id="session", owner_username="owner"))
        db.add(ChatMessage(
            id=1,
            agent_id="agent",
            session_id="session",
            role="assistant",
            content=f"持仓结果 api_key={secret}",
            meta='{"source":"scheduled_task","scheduled_task_run_id":"run","scheduled_task_source":"scheduled","step_count":2}',
        ))
        db.add(_run("run", "task", "succeeded", chat_message_id=1, agent_run_id="scheduled:run"))
        db.add(ScheduledTaskProgress(
            run_id="run",
            steps=json.dumps([
                {"type": "tool", "title": "older", "content": "old"},
                {"type": "tool", "title": "positions", "status": "done", "content": f"token={secret}"},
            ], ensure_ascii=False),
        ))
        db.commit()

        result = serialize_scheduled_run_result(db, db.get(ScheduledTaskRun, "run"))

        assert result["state"] == "succeeded"
        assert result["terminal"] is True
        assert result["message"]["meta"]["scheduled_task_run_id"] == "run"
        assert secret not in result["content"]
        assert "[REDACTED:SECRET]" in result["content"]
        assert result["content_preview"] == result["content"][:500]
        assert secret not in json.dumps(result["steps"], ensure_ascii=False)
        assert result["step_count"] == 2
        assert result["notification_state"] == "none"
    finally:
        db.close()


def test_terminal_result_serializer_covers_failed_cancelled_no_progress_and_notification_failed():
    db = _db()
    try:
        now = datetime.now(timezone.utc)
        db.add(ScheduledTask(id="task", agent_id="agent", session_id="session", owner_username="owner"))
        db.add_all([
            ChatMessage(
                id=1,
                agent_id="agent",
                session_id="session",
                role="assistant",
                content="任务未完成，已自动结束（连续输出重复且没有新的工具、文件或计划进展，已停止自动推理）",
                meta='{"source":"scheduled_task","scheduled_task_run_id":"noprog"}',
            ),
            ChatMessage(
                id=2,
                agent_id="agent",
                session_id="session",
                role="assistant",
                content=SCHEDULED_NO_PROGRESS_REPLY,
                meta='{"source":"scheduled_task","scheduled_task_run_id":"quality"}',
            ),
            _run("failed", "task", "failed", error_summary="tool_error"),
            _run("cancel", "task", "cancelled", error_summary="scheduled_task_cancelled"),
            _run("noprog", "task", "failed", chat_message_id=1, error_summary="scheduled_task_no_progress"),
            _run("quality", "task", "failed", chat_message_id=2, error_summary="scheduled_task_no_progress"),
            _run("notify", "task", "succeeded", chat_message_id=1),
            ScheduledTaskNotificationDelivery(
                id="delivery",
                run_id="notify",
                channel_id="tg",
                destination="chat",
                state="failed",
                attempts=3,
                error_summary="All connection attempts failed",
                attempted_at=now,
            ),
        ])
        db.commit()

        failed = serialize_scheduled_run_result(db, db.get(ScheduledTaskRun, "failed"))
        cancelled = serialize_scheduled_run_result(db, db.get(ScheduledTaskRun, "cancel"))
        no_progress = serialize_scheduled_run_result(db, db.get(ScheduledTaskRun, "noprog"))
        quality_no_progress = serialize_scheduled_run_result(db, db.get(ScheduledTaskRun, "quality"))
        notification_failed = serialize_scheduled_run_result(db, db.get(ScheduledTaskRun, "notify"))

        assert failed["state"] == "failed" and failed["error_summary"] == "tool_error"
        assert cancelled["state"] == "cancelled" and cancelled["terminal"] is True
        assert no_progress["outcome"] == "no_progress" and no_progress["forced_stop"] is True
        assert no_progress["content_preview"] == ""
        assert "任务未完成" not in no_progress["content"]
        assert quality_no_progress["outcome"] == "no_progress"
        assert quality_no_progress["content_preview"].startswith("本次定时任务没有产生可用")
        assert notification_failed["state"] == "succeeded"
        assert notification_failed["notification_state"] == "failed"
        assert notification_failed["notification_warning"] == "All connection attempts failed"
        assert notification_failed["notifications"][0]["attempts"] == 3
    finally:
        db.close()
