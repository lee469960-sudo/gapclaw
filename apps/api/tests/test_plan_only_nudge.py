"""PLAN-only 软提示（react-engine-v2 spec：PLAN-only 轮次注入「规划待执行」）。

覆盖三个场景：
- PLAN-only 一轮后必注入「规划待执行」hint（下一轮 LLM 上下文含该提示）；
- PLAN+工具同轮不发该 hint；
- PLAN+FINAL 同轮不发该 hint（FINAL 在提示分支之前 return）。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services.agent_runtime.runtime import AgentRuntime
from app.services.llm_client import ChatResult


class _FakeQuery:
    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return None


class _FakeDB:
    def __init__(self):
        self.added = []

    def add(self, row):
        self.added.append(row)

    def commit(self):
        pass

    def delete(self, row):
        pass

    def query(self, model):
        return _FakeQuery()


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
        allowed_actions=["shell", "mcp_tool_call"],
        save_dir="",
        im_source="",
        note_content="",
        message_meta={},
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _run_replies(ctx, replies: list[str]):
    """Run the loop with canned LLM replies; returns (final, chat_mock, exec_mock)."""
    with patch(
        "app.services.llm_client.chat_completion",
        new=AsyncMock(side_effect=[ChatResult(text=r) for r in replies]),
    ) as chat_mock, patch(
        "app.services.agent_runtime.system_prompt.SystemPromptBuilder.build_tools_desc",
        new=AsyncMock(return_value="- shell: SHELL: <cmd>\n- MCP: list_ads_views {}"),
    ), patch.object(
        AgentRuntime, "_reflect_final", new=AsyncMock(return_value=None)
    ), patch(
        "app.services.agent_tools.execute_action", new=AsyncMock(return_value="tool-ran")
    ) as exec_mock:
        result = asyncio.run(AgentRuntime().run(ctx))
    return result, chat_mock, exec_mock


def _messages_at(chat_mock, idx: int):
    return chat_mock.await_args_list[idx].args[1]


def test_plan_only_injects_nudge_hint():
    ctx = _fake_ctx()
    result, chat_mock, _ = _run_replies(
        ctx,
        ["PLAN:\n- [ ] 步骤1\n- [ ] 步骤2", "PLAN:\n- [x] 步骤1\n- [x] 步骤2\nFINAL: 完成"],
    )
    assert result == "完成"
    second_messages = _messages_at(chat_mock, 1)
    assert any("规划待执行" in str(m) for m in second_messages)


def test_plan_plus_tool_no_nudge_hint():
    ctx = _fake_ctx()
    result, chat_mock, exec_mock = _run_replies(
        ctx,
        ["PLAN:\n- [ ] 步骤1\nSHELL: echo hi", "PLAN:\n- [x] 步骤1\nFINAL: 完成"],
    )
    assert result == "完成"
    exec_mock.assert_awaited()  # SHELL 确实执行了
    second_messages = _messages_at(chat_mock, 1)
    assert not any("规划待执行" in str(m) for m in second_messages)


def test_plan_plus_final_no_nudge_hint():
    ctx = _fake_ctx()
    result, chat_mock, exec_mock = _run_replies(
        ctx,
        ["PLAN:\n- [x] 步骤1\n- [x] 步骤2\nFINAL: 完成"],
    )
    assert result == "完成"
    exec_mock.assert_not_awaited()
    # FINAL 在提示分支之前 return，循环只走一轮 LLM 调用，未进入提示注入路径
    assert chat_mock.await_count == 1
