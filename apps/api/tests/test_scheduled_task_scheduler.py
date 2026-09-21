from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.database import Base
from app.models import ScheduledTask, ScheduledTaskNotificationDelivery, ScheduledTaskRun
from app.services.scheduled_tasks.scheduler import (
    _as_utc, claim_next_run, create_due_runs, create_manual_run, fail_expired_pending_runs,
    release_session_slot,
)
from app.services.scheduled_tasks.lifecycle import finish_run_failure, finish_run_success
from app.services.scheduled_tasks.tasks import soft_delete_task

def test_due_occurrence_is_idempotent_and_old_misfire_is_not_replayed():
    engine=create_engine("sqlite:///:memory:"); Base.metadata.create_all(engine); db=sessionmaker(bind=engine)()
    try:
        now=datetime.now(timezone.utc)
        db.add_all([
            ScheduledTask(id="due", agent_id="a", session_id="s", owner_username="o", schedule_type="interval", interval_seconds=300, timezone="Asia/Shanghai", next_run_at=now),
            ScheduledTask(id="old", agent_id="a", session_id="s", owner_username="o", schedule_type="interval", interval_seconds=300, timezone="Asia/Shanghai", next_run_at=now-timedelta(hours=25)),
        ]); db.commit()
        assert len(create_due_runs(db, now)) == 1
        assert len(create_due_runs(db, now)) == 0
        assert _as_utc(db.get(ScheduledTask, "old").next_run_at) > now
    finally: db.close()


def test_one_window_catch_up_is_created_once_after_sqlite_datetime_roundtrip():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        now = datetime.now(timezone.utc)
        db.add(ScheduledTask(
            id="late", agent_id="a", session_id="s", owner_username="o",
            schedule_type="interval", interval_seconds=300, timezone="Asia/Shanghai",
            next_run_at=now - timedelta(minutes=30),
        ))
        db.commit()

        created = create_due_runs(db, now)

        assert len(created) == 1
        assert created[0].source == "catch_up"
        assert create_due_runs(db, now) == []
    finally:
        db.close()


def test_conditional_lease_allows_exactly_one_claimant_and_recovers_expiry():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    now = datetime.now(timezone.utc)
    setup = factory()
    try:
        setup.add_all([
            ScheduledTask(id="task", agent_id="a", session_id="s", owner_username="o"),
            ScheduledTaskRun(id="run", task_id="task", occurrence_key="scheduled:one", scheduled_for=now, available_at=now),
        ])
        setup.commit()
    finally:
        setup.close()

    first, second = factory(), factory()
    try:
        claimed = claim_next_run(first, "worker-a", now)
        assert claimed is not None and claimed.lease_owner == "worker-a"
        assert _as_utc(claimed.started_at) == now
        assert claim_next_run(second, "worker-b", now) is None
        reclaimed = claim_next_run(second, "worker-b", now + timedelta(minutes=6))
        assert reclaimed is not None and reclaimed.lease_owner == "worker-b"
    finally:
        first.close()
        second.close()


def test_concurrent_sqlite_workers_have_one_lease_claimant(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'scheduled-tasks.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    now = datetime.now(timezone.utc)
    setup = factory()
    try:
        setup.add_all([
            ScheduledTask(id="task", agent_id="a", session_id="s", owner_username="o"),
            ScheduledTaskRun(id="concurrent", task_id="task", occurrence_key="scheduled:concurrent", scheduled_for=now, available_at=now),
        ])
        setup.commit()
    finally:
        setup.close()

    barrier = Barrier(2)

    def claim(worker_id):
        db = factory()
        try:
            barrier.wait()
            run = claim_next_run(db, worker_id, now)
            return run.lease_owner if run else None
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(claim, ("worker-a", "worker-b")))
    assert sorted(owner for owner in results if owner) in (["worker-a"], ["worker-b"])


def test_same_session_runs_are_serialized_by_schedule_and_old_queue_fails():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    now = datetime.now(timezone.utc)
    try:
        db.add_all([
            ScheduledTask(id="task-a", agent_id="a", session_id="session", owner_username="o"),
            ScheduledTask(id="task-b", agent_id="a", session_id="session", owner_username="o"),
            ScheduledTaskRun(id="first", task_id="task-a", occurrence_key="one", scheduled_for=now - timedelta(minutes=2), available_at=now),
            ScheduledTaskRun(id="second", task_id="task-b", occurrence_key="two", scheduled_for=now - timedelta(minutes=1), available_at=now),
            ScheduledTaskRun(id="stale", task_id="task-b", occurrence_key="three", scheduled_for=now, available_at=now, queued_at=now - timedelta(hours=2)),
        ])
        db.commit()

        assert claim_next_run(db, "worker", now).id == "first"
        assert claim_next_run(db, "worker", now) is None
        assert release_session_slot(db, "first", now)
        assert claim_next_run(db, "worker", now).id == "second"
        assert fail_expired_pending_runs(db, now) == 1
        assert db.get(ScheduledTaskRun, "stale").state == "failed"
    finally:
        db.close()


def test_transient_failures_back_off_three_times_and_delete_cancels_only_pending():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    now = datetime.now(timezone.utc)
    try:
        task = ScheduledTask(id="task", agent_id="a", session_id="s", owner_username="o")
        retry = ScheduledTaskRun(id="retry", task_id="task", occurrence_key="retry", state="running", scheduled_for=now, available_at=now)
        pending = ScheduledTaskRun(id="pending", task_id="task", occurrence_key="pending", scheduled_for=now, available_at=now)
        running = ScheduledTaskRun(id="running", task_id="task", occurrence_key="running", state="running", scheduled_for=now, available_at=now)
        db.add_all((task, retry, pending, running)); db.commit()

        finish_run_failure(db, retry, TimeoutError("timeout"), now)
        assert retry.state == "pending" and retry.attempt == 1
        finish_run_failure(db, retry, TimeoutError("timeout"), now)
        finish_run_failure(db, retry, TimeoutError("timeout"), now)
        assert retry.state == "failed" and retry.attempt == 3
        business = ScheduledTaskRun(id="business", task_id="task", occurrence_key="business", state="running", scheduled_for=now, available_at=now)
        db.add(business); db.commit()
        finish_run_failure(db, business, ValueError("invalid request"), now)
        assert business.state == "failed" and business.attempt == 1
        soft_delete_task(db, task)
        assert db.get(ScheduledTaskRun, "pending").state == "cancelled"
        assert db.get(ScheduledTaskRun, "running").state == "running"
        finish_run_success(db, running, now)
        assert running.state == "succeeded"
    finally:
        db.close()


def test_success_creates_notification_outbox_without_reexecuting_run():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    now = datetime.now(timezone.utc)
    try:
        task = ScheduledTask(id="notify", agent_id="a", session_id="s", owner_username="o", notification_enabled=True, notification_channel_id="channel", notification_chat_id="chat")
        run = ScheduledTaskRun(id="notify-run", task_id="notify", occurrence_key="one", state="running", scheduled_for=now, available_at=now)
        db.add_all((task, run)); db.commit()
        finish_run_success(db, run, now)
        assert db.query(ScheduledTaskNotificationDelivery).filter_by(run_id="notify-run").count() == 1
        assert run.state == "succeeded"
    finally:
        db.close()


def test_immediate_run_uses_the_durable_manual_queue():
    engine = create_engine("sqlite:///:memory:"); Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        task = ScheduledTask(id="manual-task", agent_id="a", session_id="s", owner_username="o")
        db.add(task); db.commit()
        run = create_manual_run(db, task)
        assert run.source == "manual" and run.state == "pending" and run.task_id == task.id
    finally:
        db.close()
