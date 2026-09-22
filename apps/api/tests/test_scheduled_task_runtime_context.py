import json
from datetime import datetime
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Agent, ChatMessage
from app.services.agent_runtime.runtime import _load_recent_history


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _ctx(db, *, source: str = "scheduled_task"):
    return SimpleNamespace(
        db=db,
        agent=SimpleNamespace(id="agent", history_length=3),
        session_id="session",
        message_meta={"source": source} if source else {},
    )


def _add_message(db, role: str, content: str, meta: dict | None = None):
    db.add(ChatMessage(
        agent_id="agent",
        session_id="session",
        role=role,
        content=content,
        meta=json.dumps(meta or {}, ensure_ascii=False),
        created_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
    ))


def test_scheduled_task_context_excludes_repeated_scheduled_transcripts_and_adds_bounded_summary():
    db = _db()
    try:
        _add_message(db, "user", "manual question")
        _add_message(db, "assistant", "manual answer")
        for index in range(12):
            run_id = f"run-{index}"
            _add_message(
                db,
                "user",
                "scheduled trigger should not be repeated",
                {"source": "scheduled_task", "scheduled_task_run_id": run_id},
            )
            _add_message(
                db,
                "assistant",
                f"scheduled full result {index} " + ("POSITION " * 200),
                {"source": "scheduled_task", "scheduled_task_run_id": run_id},
            )
        _add_message(
            db,
            "assistant",
            "定时任务结果通知发送失败：All connection attempts failed。请在此会话查看执行结果。",
            {"source": "scheduled_task_notification"},
        )
        _add_message(
            db,
            "user",
            "current scheduled trigger",
            {"source": "scheduled_task", "scheduled_task_run_id": "current"},
        )
        db.commit()

        history = _load_recent_history(_ctx(db))
        text = "\n".join(content for _role, content in history)

        assert ("user", "manual question") in history
        assert ("assistant", "manual answer") in history
        assert "scheduled trigger should not be repeated" not in text
        assert "定时任务结果通知发送失败" not in text
        assert "【最近定时任务结果摘要】" in text
        assert "run run-11:" in text
        assert len(text) < 2000
    finally:
        db.close()


def test_scheduled_task_context_filter_is_gated_and_manual_history_is_unchanged():
    db = _db()
    try:
        _add_message(db, "user", "manual question")
        _add_message(
            db,
            "assistant",
            "prior scheduled result visible to ordinary conversations",
            {"source": "scheduled_task", "scheduled_task_run_id": "run"},
        )
        db.commit()

        history = _load_recent_history(_ctx(db, source=""))

        assert ("user", "manual question") in history
        assert ("assistant", "prior scheduled result visible to ordinary conversations") in history
    finally:
        db.close()
