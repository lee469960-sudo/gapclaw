"""Adapter from a claimed scheduled run to the formal session Agent runtime."""
from __future__ import annotations

import asyncio
from contextlib import suppress
import json

from sqlalchemy.orm import Session

from app.models import Agent, ChatMessage, ScheduledTask, ScheduledTaskRun, ScheduledTaskProgress
from app.services.agent_runtime import run_agent
from app.services.agent_runtime.hub import hub, stop_chat


class ScheduledTaskRuntimeError(ValueError):
    pass


class ScheduledTaskCancelled(Exception):
    pass


def _cancel_requested(db: Session, run: ScheduledTaskRun) -> bool:
    db.refresh(run, attribute_names=["cancel_requested_at"])
    return run.cancel_requested_at is not None


def execute_claimed_run(db: Session, run: ScheduledTaskRun) -> str:
    """Use the normal session path; no parallel Skill/MCP execution path exists."""
    if run.chat_message_id is not None or run.state in {"succeeded", "failed", "cancelled"}:
        raise ScheduledTaskRuntimeError("scheduled_task_run_already_finalized")
    if _cancel_requested(db, run):
        raise ScheduledTaskCancelled()
    task = db.get(ScheduledTask, run.task_id)
    if task is None:
        raise ScheduledTaskRuntimeError("scheduled_task_missing")
    if not (task.message or "").strip():
        raise ScheduledTaskRuntimeError("scheduled_task_message_empty")
    agent = db.get(Agent, task.agent_id)
    if agent is None:
        raise ScheduledTaskRuntimeError("scheduled_task_agent_missing")
    meta = {
        "source": "scheduled_task",
        "scheduled_task_run_id": run.id,
        "scheduled_task_source": run.source,
    }
    async def execute():
        key = f"{task.agent_id}:{task.session_id}"
        progress = db.get(ScheduledTaskProgress, run.id)
        if progress is None:
            progress = ScheduledTaskProgress(run_id=run.id, steps="[]")
            db.add(progress)
        progress.steps = "[]"
        db.commit()
        steps = []

        async def capture(event):
            if event.get("type") != "step" or not isinstance(event.get("step"), dict):
                return
            index = event.get("index", len(steps) if event.get("op") == "append" else max(0, len(steps) - 1))
            if not isinstance(index, int) or index < 0:
                return
            if index < len(steps):
                if event.get("op") == "patch":
                    steps[index].update(event["step"])
                else:
                    steps[index] = dict(event["step"])
            else:
                steps.append(dict(event["step"]))
            progress.steps = json.dumps(steps, ensure_ascii=False)
            db.commit()

        hub.subscribe(key, capture)
        async def stop_when_requested():
            while True:
                await asyncio.sleep(0.25)
                if _cancel_requested(db, run):
                    stop_chat(task.agent_id, task.session_id, block_auto_start=False)
                    return

        cancellation_watcher = asyncio.create_task(stop_when_requested())
        try:
            result = await run_agent(db, agent, task.session_id, task.message, message_meta=meta)
            if _cancel_requested(db, run):
                raise ScheduledTaskCancelled()
            return result
        finally:
            cancellation_watcher.cancel()
            with suppress(asyncio.CancelledError):
                await cancellation_watcher
            hub.unsubscribe(key, capture)

    return asyncio.run(execute())


def bind_run_message(db: Session, run: ScheduledTaskRun) -> ChatMessage:
    """Bind only the durable assistant message produced by this run's metadata."""
    task = db.get(ScheduledTask, run.task_id)
    if task is None:
        raise ScheduledTaskRuntimeError("scheduled_task_missing")
    messages = db.query(ChatMessage).filter(
        ChatMessage.agent_id == task.agent_id,
        ChatMessage.session_id == task.session_id,
        ChatMessage.role == "assistant",
    ).order_by(ChatMessage.id.desc()).all()
    for message in messages:
        try:
            meta = json.loads(message.meta or "{}")
        except json.JSONDecodeError:
            continue
        if meta.get("scheduled_task_run_id") == run.id:
            if not (message.content or "").strip():
                raise ScheduledTaskRuntimeError("scheduled_task_output_empty")
            run.chat_message_id = message.id
            run.agent_run_id = f"scheduled:{run.id}"
            return message
    raise ScheduledTaskRuntimeError("scheduled_task_message_write_missing")


def execute_and_finalize_claimed_run(db: Session, run: ScheduledTaskRun) -> str:
    """A run cannot become successful unless its session output is bound first."""
    try:
        result = execute_claimed_run(db, run)
    except ScheduledTaskCancelled:
        from app.services.scheduled_tasks.lifecycle import finish_run_cancelled
        finish_run_cancelled(db, run)
        return ""
    if not (result or "").strip():
        raise ScheduledTaskRuntimeError("scheduled_task_output_empty")
    bind_run_message(db, run)
    from app.services.scheduled_tasks.lifecycle import finish_run_success

    finish_run_success(db, run)
    return result
