"""OpenSpec task 2.4: Standard Profile remains the unchanged default path."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Agent, CodeAgentRun, CodeSourceSnapshot
from app.services.agent_runtime.hub import hub
from app.services.agent_runtime.runtime import AgentRuntime, run_agent
from app.services.code_agent.runner import CodeContainerRunner
from app.services.code_agent.snapshot_store import SourceSnapshotStore
from app.services.code_agent.workspace import WorkspaceManager


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_legacy_agent_without_materialized_profile_runs_as_standard():
    db = _db()
    legacy = Agent(
        id="legacy",
        name="Legacy",
        allowed_actions='["shell"]',
        skills="[]",
        mcps="[]",
        rags="[]",
        httpmcps="[]",
    )

    async def _run():
        with patch("app.services.agent_runtime.runtime.AgentRuntime.run", new=AsyncMock(return_value="ok")) as execute:
            result = await run_agent(db, legacy, "s1", "hello")
        ctx = execute.await_args.args[0]
        assert ctx.profile == "standard"
        assert ctx.code_execution is None
        assert ctx.allowed_actions == ["shell"]
        return result

    assert asyncio.run(_run()) == "ok"


def test_standard_runtime_emits_existing_done_event_without_profile_payload():
    events = []
    ctx = SimpleNamespace(
        agent=SimpleNamespace(id="standard", name="Standard"),
        db=SimpleNamespace(add=lambda _row: None, commit=lambda: None),
        session_id="s1",
        chat_key="standard:s1",
        user_message="hello",
        message_meta={},
        code_execution=None,
        mcp_ids=["m1"],
        rag_ids=[],
        skill_ids=[],
        httpmcp_ids=[],
        allowed_actions=["mcp_tool_call"],
    )

    async def _collect(event):
        events.append(event)

    async def _run():
        hub.subscribe(ctx.chat_key, _collect)
        with patch.object(AgentRuntime, "_save_user_message"), patch.object(
            AgentRuntime, "_publish_inbound_events", new=AsyncMock()
        ), patch.object(AgentRuntime, "_is_conversational", return_value=False), patch.object(
            AgentRuntime, "_run_modular", new=AsyncMock(return_value=("done", [], [], 0, 100))
        ), patch.object(AgentRuntime, "_save_assistant_message"):
            result = await AgentRuntime().run(ctx)
        hub.unsubscribe(ctx.chat_key, _collect)
        return result

    assert asyncio.run(_run()) == "done"
    assert not [event for event in events if event.get("type") == "profile"]
    done = next(event for event in events if event.get("type") == "done")
    assert done["content"] == "done"
    assert done["content_truncated"] is False
    assert done["workplace_changed"] is False


def test_standard_profile_never_allocates_code_snapshot_workspace_or_runner():
    db = _db()
    agent = Agent(
        id="standard",
        name="Standard",
        profile="standard",
        allowed_actions='["shell"]',
        skills="[]",
        mcps="[]",
        rags="[]",
        httpmcps="[]",
    )
    db.add(agent)
    db.commit()

    async def _run():
        with patch.object(
            SourceSnapshotStore, "seal", side_effect=AssertionError("snapshot allocated")
        ) as seal, patch.object(
            WorkspaceManager, "prepare", side_effect=AssertionError("workspace allocated")
        ) as prepare, patch.object(
            CodeContainerRunner, "start", side_effect=AssertionError("runner allocated")
        ) as start, patch(
            "app.services.agent_runtime.runtime.AgentRuntime.run",
            new=AsyncMock(return_value="ok"),
        ) as execute:
            result = await run_agent(db, agent, "s1", "hello")
        ctx = execute.await_args.args[0]
        assert ctx.profile == "standard"
        assert ctx.code_execution is None
        assert ctx.tool_executor is None
        seal.assert_not_called()
        prepare.assert_not_called()
        start.assert_not_called()
        return result

    assert asyncio.run(_run()) == "ok"
    assert db.query(CodeSourceSnapshot).count() == 0
    assert db.query(CodeAgentRun).count() == 0
