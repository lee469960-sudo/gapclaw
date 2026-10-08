"""Durable scheduled-task Worker heartbeat and health derivation."""
from __future__ import annotations

import json
import socket
from datetime import datetime, timedelta, timezone
from typing import Mapping

from sqlalchemy.orm import Session

from app.models import ScheduledTaskWorkerHeartbeat


HEALTH_STALE_AFTER = timedelta(seconds=60)
WORKER_ROLES = ("scheduler", "executor", "notification")


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _iso(value: datetime | None) -> str:
    return _as_utc(value).isoformat() if value else ""


def _safe_config_summary(value: Mapping[str, object] | None) -> str:
    if not value:
        return "{}"
    safe = {
        str(key): item
        for key, item in value.items()
        if isinstance(item, (str, int, float, bool)) or item is None
    }
    return json.dumps(safe, ensure_ascii=False, sort_keys=True)[:2000]


def heartbeat_worker(
    db: Session,
    *,
    role: str,
    worker_id: str,
    enabled: bool = True,
    now: datetime | None = None,
    hostname: str | None = None,
    config_summary: Mapping[str, object] | None = None,
) -> ScheduledTaskWorkerHeartbeat:
    """Upsert a role heartbeat without storing prompts, outputs, or secrets."""
    if role not in WORKER_ROLES:
        raise ValueError("scheduled_task_worker_role_invalid")
    if not worker_id:
        raise ValueError("scheduled_task_worker_id_required")
    now = _as_utc(now or datetime.now(timezone.utc))
    row_id = f"{role}:{worker_id}"
    row = db.get(ScheduledTaskWorkerHeartbeat, row_id)
    if row is None:
        row = ScheduledTaskWorkerHeartbeat(
            id=row_id,
            role=role,
            worker_id=worker_id,
            last_heartbeat_at=now,
        )
        db.add(row)
    row.hostname = (hostname if hostname is not None else socket.gethostname())[:128]
    row.enabled = bool(enabled)
    row.status = "healthy" if enabled else "disabled"
    row.config_summary = _safe_config_summary(config_summary)
    row.last_heartbeat_at = now
    row.updated_at = now
    db.commit()
    return row


def worker_health(
    db: Session,
    *,
    now: datetime | None = None,
    roles: tuple[str, ...] = WORKER_ROLES,
    enabled_roles: Mapping[str, bool] | None = None,
) -> dict[str, dict[str, object]]:
    """Return privacy-safe health by role with 60-second stale detection."""
    now = _as_utc(now or datetime.now(timezone.utc))
    health: dict[str, dict[str, object]] = {}
    enabled_roles = enabled_roles or {}
    for role in roles:
        if role not in WORKER_ROLES:
            raise ValueError("scheduled_task_worker_role_invalid")
        if enabled_roles.get(role) is False:
            health[role] = {
                "role": role,
                "status": "disabled",
                "healthy": False,
                "worker_id": "",
                "last_heartbeat_at": "",
                "seconds_since_heartbeat": None,
            }
            continue
        row = (
            db.query(ScheduledTaskWorkerHeartbeat)
            .filter_by(role=role)
            .order_by(ScheduledTaskWorkerHeartbeat.last_heartbeat_at.desc())
            .first()
        )
        if row is None:
            health[role] = {
                "role": role,
                "status": "missing",
                "healthy": False,
                "worker_id": "",
                "last_heartbeat_at": "",
                "seconds_since_heartbeat": None,
            }
            continue
        last = _as_utc(row.last_heartbeat_at)
        age = max(0, int((now - last).total_seconds()))
        status = "healthy" if row.enabled and now - last <= HEALTH_STALE_AFTER else "unhealthy"
        if not row.enabled:
            status = "disabled"
        health[role] = {
            "role": role,
            "status": status,
            "healthy": status == "healthy",
            "worker_id": row.worker_id,
            "last_heartbeat_at": _iso(last),
            "seconds_since_heartbeat": age,
        }
    return health
