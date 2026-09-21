"""Persistence contract for session scheduled-task tables."""

import os
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import CreateTable

import app.database
import app.startup
from app.database import Base
from app.models import (
    Agent,
    AgentTick,
    ScheduledTask,
    ScheduledTaskNotificationDelivery,
    ScheduledTaskRun,
    ScheduledTaskProgress,
)
from app.startup import migrate_legacy_agent_ticks, normalize_scheduled_task_next_runs


SCHEDULED_TABLES = {
    "scheduled_tasks",
    "scheduled_task_runs",
    "scheduled_task_notification_deliveries",
    "scheduled_task_progress",
}


def test_startup_upgrade_adds_scheduled_task_tables_to_existing_sqlite_database(monkeypatch, tmp_path):
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE agent_ticks (
                id INTEGER PRIMARY KEY,
                tick_id VARCHAR(16),
                agent_id VARCHAR(16),
                session_id VARCHAR(16),
                cron VARCHAR(64),
                message TEXT,
                enabled BOOLEAN,
                creator VARCHAR(64)
            )
        """))

    monkeypatch.setattr(app.database, "engine", engine)
    monkeypatch.setattr(
        app.startup,
        "get_settings",
        lambda: SimpleNamespace(
            ensure_dirs=lambda: None,
            admin_username="admin",
            admin_password="password",
            data_dir=str(tmp_path / "data"),
        ),
    )
    db = sessionmaker(bind=engine)()
    try:
        app.startup.init_db(db)
        assert SCHEDULED_TABLES <= set(inspect(engine).get_table_names())
    finally:
        db.close()


def test_startup_upgrade_adds_cancel_request_to_existing_scheduled_runs(monkeypatch, tmp_path):
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE scheduled_task_runs (id VARCHAR(16) PRIMARY KEY)"))
    monkeypatch.setattr(app.database, "engine", engine)
    monkeypatch.setattr(
        app.startup,
        "get_settings",
        lambda: SimpleNamespace(
            ensure_dirs=lambda: None,
            admin_username="admin",
            admin_password="password",
            data_dir=str(tmp_path / "data"),
        ),
    )
    db = sessionmaker(bind=engine)()
    try:
        app.startup.init_db(db)
        assert "cancel_requested_at" in {
            column["name"] for column in inspect(engine).get_columns("scheduled_task_runs")
        }
    finally:
        db.close()


def test_scheduled_task_tables_upgrade_an_existing_sqlite_database():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)

    inspector = inspect(engine)
    assert SCHEDULED_TABLES <= set(inspector.get_table_names())
    assert {
        "agent_id", "session_id", "owner_username", "schedule_type", "cron",
        "interval_seconds", "run_at", "timezone", "next_run_at", "enabled",
        "config_snapshot", "deleted_at",
    } <= {column["name"] for column in inspector.get_columns("scheduled_tasks")}
    assert {
        "task_id", "occurrence_key", "state", "scheduled_for", "available_at",
        "lease_owner", "lease_expires_at", "agent_run_id", "chat_message_id", "cancel_requested_at",
    } <= {column["name"] for column in inspector.get_columns("scheduled_task_runs")}


def test_scheduled_task_run_occurrence_is_unique_on_sqlite():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine)()
    now = datetime.now(timezone.utc)
    try:
        db.add(ScheduledTask(
            id="task0001", agent_id="agent001", session_id="session1",
            owner_username="owner", message="check", next_run_at=now,
        ))
        db.add(ScheduledTaskRun(
            id="run00001", task_id="task0001", occurrence_key="scheduled:1",
            scheduled_for=now, available_at=now,
        ))
        db.commit()

        db.add(ScheduledTaskNotificationDelivery(
            id="delivery1", run_id="run00001", channel_id="channel1",
        ))
        db.commit()
        assert db.get(ScheduledTaskRun, "run00001").state == "pending"
    finally:
        db.close()


def test_legacy_tick_migration_preserves_attributable_rows_once_and_disables_invalid_rows():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine)()
    try:
        db.add(Agent(
            id="agent001", name="Agent", creator="owner",
            session_list='[{"session_id":"session1"}]',
        ))
        db.add_all([
            AgentTick(
                tick_id="valid001", agent_id="agent001", session_id="session1",
                cron="0 9 * * *", message="run", enabled=True, creator="owner",
            ),
            AgentTick(
                tick_id="invalid1", agent_id="agent001", session_id="missing",
                cron="0 9 * * *", message="do not run", enabled=True, creator="owner",
            ),
        ])
        db.commit()

        migrate_legacy_agent_ticks(db)
        migrate_legacy_agent_ticks(db)

        migrated = db.query(ScheduledTask).order_by(ScheduledTask.message).all()
        assert len(migrated) == 2
        assert migrated[0].enabled is False
        assert migrated[0].migration_reason == "legacy_tick_session_not_owned"
        assert migrated[1].enabled is True
        assert migrated[1].timezone == "Asia/Shanghai"
        assert migrated[1].migration_reason == "migrated_from_agent_tick"
        assert len({row.legacy_agent_tick_id for row in migrated}) == 2
    finally:
        db.close()


def test_startup_normalizes_existing_timezone_local_next_runs():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine)()
    try:
        db.add(ScheduledTask(
            id="local-next", agent_id="agent001", session_id="session1",
            owner_username="owner", message="run", schedule_type="cron",
            cron="*/10 * * * *", timezone="Asia/Shanghai",
            next_run_at=datetime(2026, 9, 21, 15, 50, tzinfo=timezone.utc),
        ))
        db.commit()

        normalize_scheduled_task_next_runs(db, datetime(2026, 9, 21, 7, 47, tzinfo=timezone.utc))

        normalized = db.get(ScheduledTask, "local-next").next_run_at
        assert normalized == datetime(2026, 9, 21, 7, 50)
    finally:
        db.close()


def test_scheduled_task_tables_compile_for_postgresql():
    dialect = postgresql.dialect()
    for model in (ScheduledTask, ScheduledTaskRun, ScheduledTaskNotificationDelivery, ScheduledTaskProgress):
        statement = str(CreateTable(model.__table__).compile(dialect=dialect))
        assert "CREATE TABLE" in statement
        assert model.__tablename__ in statement


def test_scheduled_task_tables_create_on_postgresql_when_configured():
    database_url = os.getenv("SCHEDULED_TASK_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("SCHEDULED_TASK_TEST_DATABASE_URL is not configured")

    engine = create_engine(database_url)
    tables = tuple(
        model.__table__
        for model in (ScheduledTask, ScheduledTaskRun, ScheduledTaskNotificationDelivery, ScheduledTaskProgress)
    )
    try:
        for table in tables:
            table.create(bind=engine, checkfirst=True)
        assert SCHEDULED_TABLES <= set(inspect(engine).get_table_names())
    finally:
        for table in reversed(tables):
            table.drop(bind=engine, checkfirst=True)
        engine.dispose()
