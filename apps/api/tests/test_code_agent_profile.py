"""Compatibility coverage for CodeAgent profile persistence (OpenSpec task 1.1)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.models import Agent
from app.routers.agent import AgentBody, agent_post


class _FakeDB:
    def __init__(self):
        self.row = None

    def add(self, row):
        self.row = row

    def commit(self):
        pass


def _create(body: AgentBody) -> Agent:
    db = _FakeDB()
    response = asyncio.run(agent_post(body, SimpleNamespace(username="admin"), db))
    assert response["code"] == 0
    return db.row


def test_agent_profile_model_defaults_to_standard_in_serialization():
    agent = Agent(id="a1", name="legacy")
    assert Agent.__table__.columns["profile"].default.arg == "standard"
    assert agent.to_dict()["profile"] == "standard"


def test_agent_create_without_profile_persists_standard():
    agent = _create(AgentBody(action="create", name="legacy request"))
    assert agent.profile == "standard"
    assert agent.to_dict()["profile"] == "standard"


def test_agent_model_serializes_explicit_code_profile():
    agent = Agent(id="code1", name="code", profile="code")
    assert agent.to_dict()["profile"] == "code"


def test_agent_profile_rejects_unknown_value():
    db = _FakeDB()
    response = asyncio.run(agent_post(
        AgentBody(action="create", name="bad", profile="unknown"),
        SimpleNamespace(username="admin"),
        db,
    ))
    assert response["code"] == 1
    assert db.row is None
