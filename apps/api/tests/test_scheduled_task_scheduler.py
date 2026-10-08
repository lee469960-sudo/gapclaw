from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier
from threading import Event, Thread
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.database import Base
from app.models import (
    ScheduledTask,
    ScheduledTaskNotificationDelivery,
    ScheduledTaskRun,
    ScheduledTaskSessionSlot,
    ScheduledTaskWorkerHeartbeat,
)
from app.services.scheduled_tasks.scheduler import (
    _as_utc, claim_next_run, create_due_runs, create_manual_run, fail_expired_pending_runs,
    finalize_stale_running_runs, release_session_slot, run_scheduler_pass,
)
from app.config import Settings
from app.workers import scheduled_tasks
from app.services.scheduled_tasks.lifecycle import finish_run_failure, finish_run_success
from app.services.scheduled_tasks.tasks import soft_delete_task

def test_due_occurrence_is_idempotent_and_old_misfire_is_compensated_once():
    engine=create_engine("sqlite:///:memory:"); Base.metadata.create_all(engine); db=sessionmaker(bind=engine)()
    try:
        now=datetime.now(timezone.utc)
        db.add_all([
            ScheduledTask(id="due", agent_id="a", session_id="s", owner_username="o", schedule_type="interval", interval_seconds=300, timezone="Asia/Shanghai", next_run_at=now),
            ScheduledTask(id="old", agent_id="a", session_id="s2", owner_username="o", schedule_type="interval", interval_seconds=300, timezone="Asia/Shanghai", next_run_at=now-timedelta(hours=25)),
        ]); db.commit()
        assert len(create_due_runs(db, now)) == 2
        assert len(create_due_runs(db, now)) == 0
        assert _as_utc(db.get(ScheduledTask, "old").next_run_at) > now
        old_runs = db.query(ScheduledTaskRun).filter_by(task_id="old").all()
        assert len([run for run in old_runs if run.state == "pending"]) == 1
        assert len([run for run in old_runs if run.state == "skipped"]) == 1
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


def test_multi_window_downtime_creates_latest_compensation_and_skipped_summary():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        first_due = now - timedelta(minutes=30)
        db.add(ScheduledTask(
            id="multi-late",
            agent_id="a",
            session_id="s",
            owner_username="o",
            schedule_type="interval",
            interval_seconds=300,
            timezone="Asia/Shanghai",
            next_run_at=first_due,
        ))
        db.commit()

        created = create_due_runs(db, now)

        runs = db.query(ScheduledTaskRun).filter_by(task_id="multi-late").order_by(ScheduledTaskRun.state).all()
        pending = [run for run in runs if run.state == "pending"]
        missed = [run for run in runs if run.state == "skipped" and run.source == "missed"]
        assert len(created) == 1
        assert len(pending) == 1
        assert len(missed) == 1
        assert pending[0].source == "catch_up"
        assert _as_utc(pending[0].scheduled_for) == now
        assert missed[0].source == "missed"
        assert "scheduled_task_missed_windows:6" in missed[0].error_summary
        assert _as_utc(db.get(ScheduledTask, "multi-late").next_run_at) > now
    finally:
        db.close()


def test_conditional_lease_allows_exactly_one_claimant_and_stale_cleanup_finalizes_expiry():
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
        assert claim_next_run(second, "worker-b", now + timedelta(minutes=6)) is None
        assert finalize_stale_running_runs(second, now + timedelta(minutes=6)) == 1
        stale = second.get(ScheduledTaskRun, "run")
        assert stale.state == "failed"
        assert stale.error_summary == "scheduled_task_stale_worker"
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


def test_concurrent_due_scans_create_one_compensation_and_one_missed_summary(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'scheduled-due.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    setup = factory()
    try:
        setup.add(ScheduledTask(
            id="task",
            agent_id="agent",
            session_id="session",
            owner_username="owner",
            schedule_type="interval",
            interval_seconds=300,
            timezone="Asia/Shanghai",
            next_run_at=now - timedelta(minutes=30),
            enabled=True,
        ))
        setup.commit()
    finally:
        setup.close()

    barrier = Barrier(2)

    def scan():
        db = factory()
        try:
            barrier.wait()
            return len(create_due_runs(db, now))
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _idx: scan(), range(2)))

    verify = factory()
    try:
        runs = verify.query(ScheduledTaskRun).filter_by(task_id="task").all()
        pending = [run for run in runs if run.state == "pending"]
        missed = [run for run in runs if run.state == "skipped" and run.source == "missed"]
        assert sum(results) == 1
        assert len(pending) == 1
        assert len(missed) == 1
        assert _as_utc(verify.get(ScheduledTask, "task").next_run_at) > now
    finally:
        verify.close()


def test_scheduler_entry_continues_creating_due_runs_while_executor_is_blocked(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'scheduled-isolation.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    setup = factory()
    try:
        setup.add_all([
            ScheduledTask(id="running-task", agent_id="agent", session_id="session", owner_username="owner"),
            ScheduledTaskRun(
                id="pending-run",
                task_id="running-task",
                occurrence_key="manual:one",
                scheduled_for=now,
                available_at=now,
            ),
            ScheduledTask(
                id="due-task",
                agent_id="agent",
                session_id="other-session",
                owner_username="owner",
                schedule_type="interval",
                interval_seconds=300,
                timezone="Asia/Shanghai",
                next_run_at=now,
                enabled=True,
            ),
        ])
        setup.commit()
    finally:
        setup.close()

    settings = Settings(scheduled_tasks_worker_enabled=True, scheduled_tasks_shadow_mode=False)
    started = Event()
    release = Event()

    def blocked_executor(_db, _run):
        started.set()
        release.wait(2)

    monkeypatch.setattr(scheduled_tasks, "get_settings", lambda: settings)
    monkeypatch.setattr(scheduled_tasks, "SessionLocal", factory)
    monkeypatch.setattr(scheduled_tasks, "execute_and_finalize_claimed_run", blocked_executor)

    thread = Thread(target=scheduled_tasks.run_executor_once)
    thread.start()
    assert started.wait(2)

    result = scheduled_tasks.run_scheduler_once()

    release.set()
    thread.join(2)
    verify = factory()
    try:
        due_run = verify.query(ScheduledTaskRun).filter_by(task_id="due-task", state="pending").first()
        assert result["created"] == 1
        assert due_run is not None
    finally:
        verify.close()


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


def test_stale_running_cleanup_releases_session_slot_before_latest_compensation():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    try:
        db.add_all([
            ScheduledTask(
                id="task",
                agent_id="agent",
                session_id="session",
                owner_username="owner",
                schedule_type="interval",
                interval_seconds=300,
                timezone="Asia/Shanghai",
                next_run_at=now - timedelta(minutes=15),
                enabled=True,
            ),
            ScheduledTaskRun(
                id="stale-run",
                task_id="task",
                occurrence_key="scheduled:old",
                state="running",
                scheduled_for=now - timedelta(minutes=20),
                available_at=now - timedelta(minutes=20),
                started_at=now - timedelta(minutes=20),
                lease_owner="dead-worker",
                lease_expires_at=now - timedelta(minutes=10),
            ),
            ScheduledTaskSessionSlot(
                session_id="session",
                active_run_id="stale-run",
                lease_owner="dead-worker",
                lease_expires_at=now - timedelta(minutes=10),
            ),
        ])
        db.commit()

        result = run_scheduler_pass(db, worker_id="scheduler", now=now)

        stale = db.get(ScheduledTaskRun, "stale-run")
        slot = db.get(ScheduledTaskSessionSlot, "session")
        compensation = db.query(ScheduledTaskRun).filter(
            ScheduledTaskRun.task_id == "task",
            ScheduledTaskRun.state == "pending",
        ).one()
        assert stale.state == "failed"
        assert stale.error_summary == "scheduled_task_stale_worker"
        assert slot.active_run_id == ""
        assert result["created"] == 1
        assert compensation.id != "stale-run"
        assert compensation.source == "catch_up"
    finally:
        db.close()


def test_stale_cleanup_keeps_run_owned_by_healthy_worker():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    try:
        db.add_all([
            ScheduledTask(id="task", agent_id="agent", session_id="session", owner_username="owner"),
            ScheduledTaskWorkerHeartbeat(
                id="executor:worker",
                role="executor",
                worker_id="worker",
                enabled=True,
                status="healthy",
                last_heartbeat_at=now - timedelta(seconds=10),
            ),
            ScheduledTaskRun(
                id="run",
                task_id="task",
                occurrence_key="scheduled:old",
                state="running",
                scheduled_for=now - timedelta(minutes=20),
                available_at=now - timedelta(minutes=20),
                started_at=now - timedelta(minutes=20),
                lease_owner="worker",
                lease_expires_at=now - timedelta(minutes=1),
            ),
        ])
        db.commit()

        assert finalize_stale_running_runs(db, now) == 0
        assert db.get(ScheduledTaskRun, "run").state == "running"
    finally:
        db.close()


def test_active_same_task_overlap_is_coalesced_instead_of_enqueued():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    try:
        db.add_all([
            ScheduledTask(
                id="task",
                agent_id="agent",
                session_id="session",
                owner_username="owner",
                schedule_type="interval",
                interval_seconds=300,
                timezone="Asia/Shanghai",
                next_run_at=now,
                enabled=True,
            ),
            ScheduledTaskRun(
                id="running",
                task_id="task",
                occurrence_key="scheduled:previous",
                state="running",
                scheduled_for=now - timedelta(minutes=5),
                available_at=now - timedelta(minutes=5),
                started_at=now - timedelta(minutes=5),
            ),
        ])
        db.commit()

        created = create_due_runs(db, now)

        rows = db.query(ScheduledTaskRun).filter_by(task_id="task").all()
        skipped = [run for run in rows if run.state == "skipped"]
        pending = [run for run in rows if run.state == "pending"]
        assert created == []
        assert pending == []
        assert len(skipped) == 1
        assert skipped[0].source == "coalesced"
        assert skipped[0].error_summary == "scheduled_task_overlap_coalesced"
        assert _as_utc(db.get(ScheduledTask, "task").next_run_at) > now
    finally:
        db.close()


def test_active_same_session_overlap_is_coalesced_for_later_task():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    try:
        db.add_all([
            ScheduledTask(id="active-task", agent_id="agent", session_id="session", owner_username="owner"),
            ScheduledTask(
                id="due-task",
                agent_id="agent",
                session_id="session",
                owner_username="owner",
                schedule_type="interval",
                interval_seconds=300,
                timezone="Asia/Shanghai",
                next_run_at=now,
                enabled=True,
            ),
            ScheduledTaskRun(
                id="pending",
                task_id="active-task",
                occurrence_key="manual:previous",
                state="pending",
                scheduled_for=now - timedelta(minutes=1),
                available_at=now - timedelta(minutes=1),
            ),
        ])
        db.commit()

        created = create_due_runs(db, now)

        due_rows = db.query(ScheduledTaskRun).filter_by(task_id="due-task").all()
        assert created == []
        assert len(due_rows) == 1
        assert due_rows[0].state == "skipped"
        assert due_rows[0].source == "coalesced"
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
        no_progress = ScheduledTaskRun(id="noprog", task_id="task", occurrence_key="noprog", state="running", scheduled_for=now, available_at=now)
        db.add(no_progress); db.commit()
        finish_run_failure(db, no_progress, "scheduled_task_no_progress", now)
        assert no_progress.state == "failed" and no_progress.attempt == 1
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


def test_scheduler_pass_creates_due_runs_within_sla_without_claiming_executor_work():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    now = datetime.now(timezone.utc)
    scheduled_for = now - timedelta(seconds=10)
    try:
        db.add(ScheduledTask(
            id="sla-task",
            agent_id="agent",
            session_id="session",
            owner_username="owner",
            schedule_type="interval",
            interval_seconds=300,
            timezone="Asia/Shanghai",
            next_run_at=scheduled_for,
            enabled=True,
        ))
        db.commit()

        result = run_scheduler_pass(db, worker_id="scheduler-a", now=now)

        run = db.query(ScheduledTaskRun).filter_by(task_id="sla-task").one()
        assert result == {"created": 1, "expired": 0}
        assert _as_utc(run.scheduled_for) == scheduled_for
        assert (_as_utc(run.available_at) - scheduled_for).total_seconds() <= 15
        assert run.state == "pending"
    finally:
        db.close()
