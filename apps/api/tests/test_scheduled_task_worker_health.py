from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import ScheduledTaskWorkerHeartbeat
from app.services.scheduled_tasks.health import heartbeat_worker, worker_health


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_worker_heartbeat_reports_healthy_role_state():
    db = _db()
    now = datetime.now(timezone.utc)
    try:
        heartbeat_worker(db, role="scheduler", worker_id="worker-a", now=now, hostname="host-a")

        health = worker_health(db, now=now)

        assert health["scheduler"]["status"] == "healthy"
        assert health["scheduler"]["healthy"] is True
        assert health["scheduler"]["worker_id"] == "worker-a"
        assert health["scheduler"]["seconds_since_heartbeat"] == 0
        assert db.get(ScheduledTaskWorkerHeartbeat, "scheduler:worker-a").hostname == "host-a"
    finally:
        db.close()


def test_worker_health_marks_stale_and_missing_roles():
    db = _db()
    now = datetime.now(timezone.utc)
    try:
        heartbeat_worker(db, role="executor", worker_id="worker-b", now=now - timedelta(seconds=61))

        health = worker_health(db, now=now)

        assert health["executor"]["status"] == "unhealthy"
        assert health["executor"]["healthy"] is False
        assert health["executor"]["seconds_since_heartbeat"] >= 61
        assert health["scheduler"]["status"] == "missing"
        assert health["notification"]["status"] == "missing"
    finally:
        db.close()


def test_worker_health_reports_disabled_roles_without_requiring_heartbeat():
    db = _db()
    now = datetime.now(timezone.utc)
    try:
        health = worker_health(
            db,
            now=now,
            enabled_roles={"scheduler": False, "executor": True, "notification": False},
        )

        assert health["scheduler"]["status"] == "disabled"
        assert health["notification"]["status"] == "disabled"
        assert health["executor"]["status"] == "missing"
    finally:
        db.close()


def test_worker_heartbeat_rejects_unknown_roles_and_missing_worker_ids():
    db = _db()
    try:
        with pytest.raises(ValueError, match="role_invalid"):
            heartbeat_worker(db, role="api", worker_id="worker")
        with pytest.raises(ValueError, match="worker_id_required"):
            heartbeat_worker(db, role="scheduler", worker_id="")
    finally:
        db.close()
