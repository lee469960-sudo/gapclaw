import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.database import Base
from app.models import Agent
from datetime import datetime, timezone
from app.services.scheduled_tasks.tasks import ScheduledTaskValidationError, create_task, next_run_at, soft_delete_task, update_task

def test_task_schedule_validation_and_quota():
    engine=create_engine("sqlite:///:memory:"); Base.metadata.create_all(engine); db=sessionmaker(bind=engine)()
    try:
        db.add(Agent(id="a", name="A", creator="owner", session_list='[{"session_id":"s"}]')); db.commit()
        row=create_task(db, agent_id="a", session_id="s", owner="owner", message="x", schedule_type="cron", cron="0 9 * * *")
        assert row.timezone == "Asia/Shanghai"
        with pytest.raises(ScheduledTaskValidationError, match="interval_too_short"):
            create_task(db, agent_id="a", session_id="s", owner="owner", message="x", schedule_type="interval", interval_seconds=1)
        with pytest.raises(ScheduledTaskValidationError, match="timezone_invalid"):
            create_task(db, agent_id="a", session_id="s", owner="owner", message="x", schedule_type="cron", cron="0 9 * * *", timezone="Bad/Zone")
    finally: db.close()

def test_next_run_is_timezone_aware_and_handles_dst():
    now=datetime(2026, 3, 8, 6, 0, tzinfo=timezone.utc)
    run=next_run_at("cron", "0 9 * * *", 0, None, "America/New_York", now)
    assert run.tzinfo is not None and run > now
    assert next_run_at("once", "", 0, now, "Asia/Shanghai", now) is None

def test_cron_next_run_is_persisted_as_utc_for_timezone_comparison():
    now=datetime(2026, 9, 21, 7, 47, tzinfo=timezone.utc)
    run=next_run_at("cron", "*/10 * * * *", 0, None, "Asia/Shanghai", now)
    assert run == datetime(2026, 9, 21, 7, 50, tzinfo=timezone.utc)

def test_task_update_toggle_and_soft_delete():
    engine=create_engine("sqlite:///:memory:"); Base.metadata.create_all(engine); db=sessionmaker(bind=engine)()
    try:
        db.add(Agent(id="a", name="A", creator="owner")); db.commit()
        row=create_task(db, agent_id="a", session_id="a", owner="owner", message="x", schedule_type="cron", cron="0 9 * * *")
        update_task(db, row, schedule_type="interval", cron="", interval_seconds=300, run_at="", timezone="Asia/Shanghai", enabled=False)
        assert row.schedule_type == "interval" and row.enabled is False
        soft_delete_task(db, row)
        assert row.deleted_at is not None and row.enabled is False
    finally: db.close()
