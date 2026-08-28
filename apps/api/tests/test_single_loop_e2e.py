"""End-to-end test for the single LLM-driven ReAct loop.

Covers the new contract: LLM → parse → security-gate → execute → observe →
FINAL. Completion is decided by the LLM alone; the engine only parses protocol
lines, blocks disallowed actions, and feeds results back.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services.agent_runtime.runtime import AgentRuntime
from app.services.agent_runtime.hub import stop_chat
from app.services.llm_client import ChatResult


class _FakeDB:
    def __init__(self):
        self.added = []

    def add(self, row):
        self.added.append(row)

    def commit(self):
        pass


def _fake_ctx(**overrides):
    base = dict(
        agent=SimpleNamespace(
            id="a1", llm_timeout=30, sandbox_id=None, name="t",
            max_iterations=10, prompt="You are a test agent.", memory="",
        ),
        session_id="s1",
        chat_key="a1:s1",
        user_message="查询视图",
        username="u",
        db=_FakeDB(),
        llm=SimpleNamespace(id="llm1"),
        sandbox=None,
        mcp_ids=["m1"],
        skill_ids=[],
        skill_names=[],
        mcp_names=["ads-mcp"],
        skill_mds=[],
        httpmcp_ids=[],
        rag_ids=[],
        allowed_actions=["mcp_tool_call", "shell"],
        save_dir="",
        im_source="",
        note_content="",
        message_meta={},
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_single_loop_executes_tool_then_final():
    ctx = _fake_ctx()

    async def _run():
        with patch(
            "app.services.llm_client.chat_completion",
            new=AsyncMock(side_effect=[ChatResult(text="MCP: list_ads_views {}"), ChatResult(text="FINAL: 已完成")]),
        ), patch(
            "app.services.agent_runtime.system_prompt.SystemPromptBuilder.build_tools_desc",
            new=AsyncMock(return_value="- mcp ads-mcp: list_ads_views {}"),
        ), patch(
            "app.services.agent_tools.execute_action",
            new=AsyncMock(return_value="ok"),
        ) as exec_mock:
            result = await AgentRuntime().run(ctx)

        exec_mock.assert_awaited_once()
        action = exec_mock.await_args.args[0]
        assert action == "mcp_tool_call"
        return result

    assert asyncio.run(_run()) == "已完成"


def test_single_loop_blocks_disallowed_tool():
    ctx = _fake_ctx(allowed_actions=["shell"])  # mcp_tool_call not allowed

    async def _run():
        with patch(
            "app.services.llm_client.chat_completion",
            new=AsyncMock(side_effect=[ChatResult(text="MCP: list_ads_views {}"), ChatResult(text="FINAL: 无法使用 MCP")]),
        ), patch(
            "app.services.agent_runtime.system_prompt.SystemPromptBuilder.build_tools_desc",
            new=AsyncMock(return_value="- shell: SHELL: <cmd>"),
        ), patch(
            "app.services.agent_tools.execute_action",
            new=AsyncMock(return_value="should not run"),
        ) as exec_mock:
            result = await AgentRuntime().run(ctx)

        exec_mock.assert_not_awaited()
        return result

    assert asyncio.run(_run()) == "无法使用 MCP"


def test_cancellation_between_native_tool_calls_blocks_the_next_action():
    ctx = _fake_ctx(allowed_actions=["shell"])
    calls = [
        {"id": "c1", "function": {"name": "shell", "arguments": '{"cmd":"first"}'}},
        {"id": "c2", "function": {"name": "shell", "arguments": '{"cmd":"second"}'}},
    ]

    async def _execute(*_args, **_kwargs):
        stop_chat(ctx.agent.id, ctx.session_id)
        return "ok"

    async def _run():
        with patch(
            "app.services.llm_client.chat_completion",
            new=AsyncMock(return_value=ChatResult(tool_calls=calls)),
        ), patch(
            "app.services.agent_runtime.system_prompt.SystemPromptBuilder.build_tools_desc",
            new=AsyncMock(return_value="- shell"),
        ), patch(
            "app.services.agent_tools.execute_action",
            new=AsyncMock(side_effect=_execute),
        ) as exec_mock:
            result = await AgentRuntime().run(ctx)
        assert exec_mock.await_count == 1
        return result

    assert asyncio.run(_run()) == "[已停止]"
