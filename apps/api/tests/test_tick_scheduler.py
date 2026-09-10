from __future__ import annotations

from types import SimpleNamespace

from app.services import tick_scheduler
from app.services.agent_runtime.hub import (
    clear_auto_start_block,
    is_auto_start_blocked,
    stop_chat,
    _auto_start_blocked,
    _running,
)


class FakeScheduler:
    def __init__(self):
        self.added = []
        self.removed = []
        self.jobs = {}

    def add_job(self, func, trigger, **kwargs):
        self.added.append((func, trigger, kwargs))
        job = SimpleNamespace(id=kwargs["id"], next_run_time="2026-09-05T09:00:00+08:00")
        self.jobs[job.id] = job
        return job

    def remove_job(self, job_id):
        self.removed.append(job_id)

    def get_job(self, job_id):
        return self.jobs.get(job_id)


def test_add_tick_job_allows_short_scheduler_misfires():
    scheduler = FakeScheduler()
    original_scheduler = tick_scheduler._scheduler
    original_job_map = dict(tick_scheduler._job_map)
    tick_scheduler._scheduler = scheduler
    tick_scheduler._job_map.clear()
    try:
        tick_scheduler.add_tick_job("tick1", "0 9 * * *")
        next_run_time = tick_scheduler.next_run_time("tick1")
    finally:
        tick_scheduler._scheduler = original_scheduler
        tick_scheduler._job_map.clear()
        tick_scheduler._job_map.update(original_job_map)

    assert len(scheduler.added) == 1
    _, _, kwargs = scheduler.added[0]
    assert kwargs["id"] == "tick_tick1"
    assert kwargs["replace_existing"] is True
    assert kwargs["misfire_grace_time"] == tick_scheduler.TICK_MISFIRE_GRACE_SECONDS
    assert kwargs["coalesce"] is True
    assert next_run_time == "2026-09-05T09:00:00+08:00"


def test_build_tick_trigger_rejects_invalid_cron():
    try:
        tick_scheduler.build_tick_trigger("bad cron")
    except Exception as exc:
        assert "Wrong number of fields" in str(exc) or "Unrecognized expression" in str(exc)
    else:
        raise AssertionError("invalid cron should fail")


def test_stop_chat_blocks_auto_start_until_manual_clear():
    _running.clear()
    _auto_start_blocked.clear()
    _running["agent-a:session-a"] = True

    was_running = stop_chat("agent-a", "session-a")

    assert was_running is True
    assert _running["agent-a:session-a"] is False
    assert is_auto_start_blocked("agent-a", "session-a") is True

    clear_auto_start_block("agent-a", "session-a")

    assert is_auto_start_blocked("agent-a", "session-a") is False


def test_run_tick_skips_manually_stopped_chat(monkeypatch):
    class FakeQuery:
        def __init__(self, row):
            self.row = row

        def filter(self, *_args, **_kwargs):
            return self

        def first(self):
            return self.row

    tick = SimpleNamespace(
        tick_id="tick1",
        agent_id="agent-a",
        session_id="session-a",
        message="scheduled",
        enabled=True,
    )
    agent = SimpleNamespace(id="agent-a")

    class FakeDb:
        def query(self, model):
            if model.__name__ == "AgentTick":
                return FakeQuery(tick)
            return FakeQuery(agent)

        def close(self):
            pass

    called = []
    monkeypatch.setattr(tick_scheduler, "SessionLocal", lambda: FakeDb())
    monkeypatch.setattr(tick_scheduler, "is_auto_start_blocked", lambda aid, sid: True)
    monkeypatch.setattr(tick_scheduler, "run_agent", lambda *_args, **_kwargs: called.append(True))

    tick_scheduler._run_tick("tick1")

    assert called == []


def test_run_tick_skips_already_running_chat(monkeypatch):
    class FakeQuery:
        def __init__(self, row):
            self.row = row

        def filter(self, *_args, **_kwargs):
            return self

        def first(self):
            return self.row

    tick = SimpleNamespace(
        tick_id="tick1",
        agent_id="agent-a",
        session_id="session-a",
        message="scheduled",
        enabled=True,
    )
    agent = SimpleNamespace(id="agent-a")

    class FakeDb:
        def query(self, model):
            if model.__name__ == "AgentTick":
                return FakeQuery(tick)
            return FakeQuery(agent)

        def close(self):
            pass

    called = []
    monkeypatch.setattr(tick_scheduler, "SessionLocal", lambda: FakeDb())
    monkeypatch.setattr(tick_scheduler, "is_auto_start_blocked", lambda aid, sid: False)
    monkeypatch.setattr(tick_scheduler, "is_running", lambda aid, sid: True)
    monkeypatch.setattr(tick_scheduler, "run_agent", lambda *_args, **_kwargs: called.append(True))

    tick_scheduler._run_tick("tick1")

    assert called == []
