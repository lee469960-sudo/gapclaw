"""FINAL-priority contract: same-round tool steps are dropped and surfaced.

Spec R1 (task 2.2): SHELL+FINAL warns and skips SHELL; FINAL alone adds no warning;
PLAN+FINAL triggers no non-PLAN warning.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services.agent_runtime.runtime import AgentRuntime
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
        allowed_actions=["shell", "mcp_tool_call"],
        save_dir="",
        im_source="",
        note_content="",
        message_meta={},
    )
    base.update(overrides)
    return SimpleNamespace(**base)


async def _run_reply(ctx, reply: str) -> tuple[str, list[dict], AsyncMock]:
    """Run one loop turn; returns (final, published_steps, execute_action_mock)."""
    published: list[dict] = []

    async def _pub(key, event):
        published.append(event)

    with patch(
        "app.services.llm_client.chat_completion",
        new=AsyncMock(side_effect=[ChatResult(text=reply)]),
    ), patch(
        "app.services.agent_runtime.system_prompt.SystemPromptBuilder.build_tools_desc",
        new=AsyncMock(return_value="- shell: SHELL: <cmd>\n- MCP: list_ads_views {}"),
    ), patch.object(
        AgentRuntime, "_reflect_final", new=AsyncMock(return_value=None)
    ), patch(
        "app.services.agent_tools.execute_action", new=AsyncMock(return_value="tool-ran")
    ) as exec_mock, patch(
        "app.services.agent_runtime.hub.hub"
    ) as hub:
        hub.publish = AsyncMock(side_effect=_pub)
        result = await AgentRuntime().run(ctx)
    return result, published, exec_mock


def _skipped_steps(published: list[dict]) -> list[dict]:
    return [
        e["step"]
        for e in published
        if e.get("type") == "step"
        and e.get("op") == "append"
        and isinstance(e.get("step"), dict)
        and e["step"].get("action") == "final_skipped_tools"
    ]


def test_shell_plus_final_warns_and_skips_shell():
    ctx = _fake_ctx()
    result, published, exec_mock = asyncio.run(
        _run_reply(ctx, "SHELL: echo hi\nFINAL: 完成")
    )
    assert result == "完成"
    exec_mock.assert_not_awaited()  # SHELL not executed
    assert _skipped_steps(published)  # warning step present


def test_final_alone_adds_no_warning():
    ctx = _fake_ctx()
    result, published, exec_mock = asyncio.run(_run_reply(ctx, "FINAL: 完成"))
    assert result == "完成"
    exec_mock.assert_not_awaited()
    assert _skipped_steps(published) == []


def test_plan_plus_final_no_non_plan_warning():
    ctx = _fake_ctx()
    result, published, exec_mock = asyncio.run(
        _run_reply(ctx, "PLAN:\n- [x] 子任务1\n- [x] 子任务2\nFINAL: 完成")
    )
    assert result == "完成"
    exec_mock.assert_not_awaited()
    assert _skipped_steps(published) == []  # PLAN is not a "skipped tool"
