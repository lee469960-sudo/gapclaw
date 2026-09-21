from app.services.scheduled_tasks.observability import EVENTS, emit


def test_scheduled_task_events_are_allowlisted_and_do_not_leak_prompt_or_secret():
    EVENTS.clear()
    emit("run_failed", run_id="run-1", state="failed", message="secret prompt", error="token=abc", output="model output")
    assert EVENTS == [{"event": "run_failed", "run_id": "run-1", "state": "failed"}]
