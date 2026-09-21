"""Privacy-safe structured observability for scheduled-task coordination."""
from __future__ import annotations

import json
import logging

logger = logging.getLogger("app.scheduled_tasks")
EVENTS: list[dict[str, object]] = []
_ALLOWED = {"event", "task_id", "run_id", "session_id", "source", "attempt", "state", "count", "delay_seconds"}


def emit(event: str, **fields: object) -> None:
    """Emit only IDs/state/counts; never prompts, errors, credentials, or output."""
    record = {"event": event, **{key: value for key, value in fields.items() if key in _ALLOWED}}
    EVENTS.append(record)
    logger.info("scheduled_task %s", json.dumps(record, sort_keys=True, default=str))
