"""Authorization boundary for session-scoped scheduled tasks."""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.models import Agent, ScheduledTask, User


class ScheduledTaskAuthorizationError(PermissionError):
    """Raised without resource details when a task operation is unauthorized."""


def _users(value: str) -> set[str]:
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, json.JSONDecodeError):
        return set()
    return {str(item) for item in parsed if isinstance(item, str) and item}


def _is_admin(user: User) -> bool:
    return bool({"admin", "master"} & _users(user.roles))


def require_session_task_manager(
    db: Session,
    user: User,
    agent_id: str,
    session_id: str,
    task: ScheduledTask | None = None,
) -> Agent:
    """Return the in-scope Agent only for users allowed to manage its session tasks."""
    agent = db.query(Agent).filter(Agent.id == agent_id).first()
    if not agent or session_id not in _session_ids(agent):
        raise ScheduledTaskAuthorizationError("scheduled_task_not_found")
    if task and (task.agent_id != agent.id or task.session_id != session_id):
        raise ScheduledTaskAuthorizationError("scheduled_task_not_found")
    if _is_admin(user):
        return agent
    managers = _users(agent.allowed_users)
    managers.add(str(agent.creator or ""))
    if task:
        managers.add(str(task.owner_username or ""))
    if user.username not in managers:
        raise ScheduledTaskAuthorizationError("scheduled_task_unauthorized")
    return agent


def _session_ids(agent: Agent) -> set[str]:
    try:
        sessions = json.loads(agent.session_list or "[]")
    except (TypeError, json.JSONDecodeError):
        sessions = []
    values = {
        str(item.get("session_id") or "")
        for item in sessions
        if isinstance(item, dict) and str(item.get("session_id") or "")
    }
    return values or {str(agent.id)}
