from __future__ import annotations

import json
from types import SimpleNamespace

from app.services import tick_scheduler
from app.routers.agent_chat import _agent_session_ids, _validate_tick_scope


class _FakeQuery:
    def __init__(self, agent):
        self.agent = agent

    def filter(self, *_args, **_kwargs):
        return self

    def first(self):
        return self.agent


class _FakeDb:
    def __init__(self, agent):
        self.agent = agent

    def query(self, _model):
        return _FakeQuery(self.agent)


def test_agent_session_ids_come_from_session_list():
    agent = SimpleNamespace(
        id="agent1",
        session_list=json.dumps([
            {"name": "default", "session_id": "s1"},
            {"name": "work", "session_id": "s2"},
        ]),
    )

    assert _agent_session_ids(agent) == {"s1", "s2"}


def test_validate_tick_scope_rejects_empty_session():
    _agent, err = _validate_tick_scope(_FakeDb(SimpleNamespace(id="agent1")), "agent1", "")

    assert "会话未就绪" in err


def test_validate_tick_scope_rejects_session_not_owned_by_agent():
    agent = SimpleNamespace(id="agent1", session_list=json.dumps([{"session_id": "s1"}]))

    _agent, err = _validate_tick_scope(_FakeDb(agent), "agent1", "other")

    assert "不属于该 Agent" in err


def test_validate_tick_scope_accepts_owned_session():
    agent = SimpleNamespace(id="agent1", session_list=json.dumps([{"session_id": "s1"}]))

    found, err = _validate_tick_scope(_FakeDb(agent), "agent1", "s1")

    assert found is agent
    assert err == ""


def test_tick_payload_includes_next_run_time_for_enabled_tick(monkeypatch):
    monkeypatch.setattr(tick_scheduler, "next_run_time", lambda tick_id: "2026-09-05T09:00:00+08:00")
    from app.routers.agent_chat import _tick_payload

    payload = _tick_payload(SimpleNamespace(
        tick_id="tick1",
        cron="0 9 * * *",
        message="run",
        enabled=True,
    ))

    assert payload["next_run_time"] == "2026-09-05T09:00:00+08:00"
