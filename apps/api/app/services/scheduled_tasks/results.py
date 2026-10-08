"""Safe scheduled-task terminal result serialization."""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models import (
    ChatMessage,
    ScheduledTaskNotificationDelivery,
    ScheduledTaskProgress,
    ScheduledTaskRun,
)
from app.services.code_agent.output_security import redact_code_output


CONTENT_LIMIT = 8000
PREVIEW_LIMIT = 500
STEP_LIMIT = 200


def _iso(value: datetime | None) -> str:
    return value.isoformat() if value else ""


def _seconds_between(start: datetime | None, end: datetime | None) -> int | None:
    if not start or not end:
        return None
    return max(0, int((end - start).total_seconds()))


def _safe_text(value: str | None, limit: int) -> str:
    text = redact_code_output(value or "").text.strip()
    return text[:limit]


def _load_json_object(raw: str | None) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _load_json_list(raw: str | None) -> list[Any]:
    try:
        value = json.loads(raw or "[]")
    except Exception:
        return []
    return value if isinstance(value, list) else []


def _safe_steps(progress: ScheduledTaskProgress | None, limit: int = STEP_LIMIT) -> dict[str, Any]:
    steps = _load_json_list(progress.steps if progress else "[]")
    total = len(steps)
    tail = steps[-max(1, int(limit)):] if total > limit else steps
    safe: list[dict[str, Any]] = []
    for step in tail:
        if not isinstance(step, dict):
            continue
        item: dict[str, Any] = {
            "type": step.get("type"),
            "action": step.get("action"),
            "title": step.get("title"),
            "status": step.get("status"),
            "iteration": step.get("iteration"),
            "hidden": step.get("hidden"),
        }
        for key, max_len in (("preview", 300), ("content", 1200), ("snippet", 1200)):
            value = step.get(key)
            if isinstance(value, str) and value.strip():
                item[key] = _safe_text(value, max_len)
        safe.append(item)
    return {"steps": safe, "step_count": total, "older": max(0, total - len(safe))}


def _message_payload(message: ChatMessage | None) -> dict[str, Any] | None:
    if message is None:
        return None
    content = _safe_text(message.content, CONTENT_LIMIT)
    meta = _load_json_object(message.meta)
    return {
        "id": message.id,
        "role": message.role,
        "content": content,
        "content_preview": content[:PREVIEW_LIMIT],
        "created_at": message.created_at or "",
        "meta": {
            key: meta[key]
            for key in ("source", "scheduled_task_run_id", "scheduled_task_source", "step_count")
            if meta.get(key) is not None
        },
    }


def _notification_payloads(rows: list[ScheduledTaskNotificationDelivery]) -> list[dict[str, Any]]:
    return [
        {
            "id": row.id,
            "channel_id": row.channel_id,
            "destination": row.destination,
            "state": row.state,
            "attempts": row.attempts,
            "error_summary": _safe_text(row.error_summary, 500),
            "attempted_at": _iso(row.attempted_at),
            "delivered_at": _iso(row.delivered_at),
            "next_attempt_at": _iso(row.next_attempt_at),
        }
        for row in rows
    ]


def _notification_state(rows: list[ScheduledTaskNotificationDelivery]) -> str:
    states = {row.state for row in rows}
    if not rows:
        return "none"
    if "failed" in states:
        return "failed"
    if "pending" in states:
        return "pending"
    if states == {"delivered"}:
        return "delivered"
    return "mixed"


def _is_forced_stop_text(text: str | None) -> bool:
    return (text or "").strip().startswith("任务未完成，已自动结束")


def is_forced_stop_result(text: str | None) -> bool:
    """Return true for the runtime's no-progress/budget forced-stop fallback."""
    return _is_forced_stop_text(text)


_DELIVERABLE_SUFFIXES = (".xlsx", ".xls", ".csv", ".pdf", ".png", ".md", ".docx")
_INTERNAL_DUMP_RE = re.compile(r"mcp_result_\d+\.json|shell_result_\d+\.|read_result_\d+\.", re.I)


def _user_facing_report(text: str) -> str:
    body = (text or "").strip()
    if len(body) < 80:
        return ""
    if body.startswith(("任务未完成", "工具调用", "定时任务已完成", "本次定时任务没有产生")):
        return ""
    return body


def scheduled_delivery_text(content: str, steps, paths: list[str]) -> str:
    """Prefer the report already written in the run over a filename stub."""
    report = _user_facing_report(content)
    if not report:
        best = ""
        for step in steps or []:
            if not isinstance(step, dict) or step.get("type") != "llm":
                continue
            candidate = _user_facing_report(str(step.get("content") or ""))
            if len(candidate) > len(best):
                best = candidate
        report = best
    kept = [path for path in paths if path][:8]
    listed = "\n".join(f"- {path}" for path in kept)
    if report:
        missing = [path for path in kept if path not in report]
        if missing:
            extra = "\n".join(f"- {path}" for path in missing)
            return f"{report}\n\n交付文件：\n{extra}"
        return report
    if listed:
        return f"定时任务已完成。\n\n交付文件：\n{listed}"
    return ""


def deliverable_paths_in_message(message) -> list[str]:
    """User-facing files recorded on an assistant message."""
    try:
        meta = json.loads(getattr(message, "meta", None) or "{}")
    except json.JSONDecodeError:
        return []
    if not isinstance(meta, dict):
        return []
    paths = []
    for path in meta.get("saved_paths") or []:
        text = str(path or "").strip()
        if not text or _INTERNAL_DUMP_RE.search(text):
            continue
        if text.lower().endswith(_DELIVERABLE_SUFFIXES):
            paths.append(text)
    return paths


def serialize_scheduled_run_result(db: Session, run: ScheduledTaskRun) -> dict[str, Any]:
    """Build a bounded, user-safe terminal/result view for a scheduled run."""
    message = db.get(ChatMessage, run.chat_message_id) if run.chat_message_id else None
    progress = db.get(ScheduledTaskProgress, run.id)
    notifications = (
        db.query(ScheduledTaskNotificationDelivery)
        .filter_by(run_id=run.id)
        .order_by(ScheduledTaskNotificationDelivery.created_at.asc())
        .all()
    )
    message_payload = _message_payload(message)
    step_payload = _safe_steps(progress)
    notification_payloads = _notification_payloads(notifications)
    notification_state = _notification_state(notifications)
    legacy_forced_message = _is_forced_stop_text(message.content if message else "")
    forced_stop = legacy_forced_message or "no_progress" in (run.error_summary or "")
    terminal = run.state in {"succeeded", "failed", "cancelled", "skipped"}
    # Older runs may have persisted the generic no-progress guardrail as the
    # assistant message. New scheduled runs replace it with a user-facing quality
    # note; only suppress the legacy guardrail text.
    content = "" if legacy_forced_message else (message_payload["content"] if message_payload else "")
    error_summary = _safe_text(run.error_summary, 500)
    return {
        "id": run.id,
        "task_id": run.task_id,
        "state": run.state,
        "source": run.source,
        "attempt": run.attempt,
        "scheduled_for": _iso(run.scheduled_for),
        "available_at": _iso(run.available_at),
        "queued_at": _iso(run.queued_at),
        "started_at": _iso(run.started_at),
        "finished_at": _iso(run.finished_at),
        "scheduling_delay_seconds": _seconds_between(run.scheduled_for, run.available_at),
        "executor_delay_seconds": _seconds_between(run.available_at, run.started_at),
        "runtime_duration_seconds": _seconds_between(run.started_at, run.finished_at),
        "cancel_requested": run.cancel_requested_at is not None,
        "chat_message_id": run.chat_message_id,
        "agent_run_id": run.agent_run_id,
        "error_summary": error_summary,
        "terminal": terminal,
        "forced_stop": forced_stop,
        "outcome": "no_progress" if forced_stop else run.state,
        "message": message_payload,
        "content": content,
        "content_preview": content[:PREVIEW_LIMIT],
        "steps": step_payload["steps"],
        "step_count": step_payload["step_count"],
        "steps_older": step_payload["older"],
        "notifications": notification_payloads,
        "notification_state": notification_state,
        "notification_warning": (
            next((row["error_summary"] for row in notification_payloads if row["state"] == "failed"), "")
            if notification_state == "failed"
            else ""
        ),
    }


def run_diagnostics(run: ScheduledTaskRun | None) -> dict[str, Any]:
    if run is None:
        return {
            "last_scheduled": "",
            "last_started": "",
            "last_finished": "",
            "last_status": "",
            "scheduling_delay_seconds": None,
            "executor_delay_seconds": None,
            "runtime_duration_seconds": None,
        }
    return {
        "last_scheduled": _iso(run.scheduled_for),
        "last_started": _iso(run.started_at),
        "last_finished": _iso(run.finished_at),
        "last_status": run.state,
        "scheduling_delay_seconds": _seconds_between(run.scheduled_for, run.available_at),
        "executor_delay_seconds": _seconds_between(run.available_at, run.started_at),
        "runtime_duration_seconds": _seconds_between(run.started_at, run.finished_at),
    }


def skipped_missed_summary(rows: list[ScheduledTaskRun]) -> dict[str, int]:
    return {
        "skipped": sum(1 for row in rows if row.state == "skipped"),
        "missed": sum(1 for row in rows if row.state == "skipped" and row.source == "missed"),
        "coalesced": sum(1 for row in rows if row.state == "skipped" and row.source == "coalesced"),
    }
