"""react-engine-v15 tests.

Covers the v15 corrections to v14:
- R1′: no-progress rounds inject a soft「破局复盘」coach hint (non-terminating),
  progress resets the streak (6.1).
- R2 reverted: every FINAL goes through the completion review, including short
  tasks (6.2).
- R4: real context-availability percentage (pre-trim, shared estimate_tokens
  口径), written to the assistant message meta (6.3).

No real LLM/MCP involved; ``chat_completion`` / tools / reflection are mocked.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services.agent_runtime.context_manager import ContextManager
from app.services.agent_runtime.runtime import AgentRuntime, _context_available_percent
from app.services.llm_client import ChatResult


class _FakeQuery:
    def filter(self, *_a, **_k):
        return self

    def order_by(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def all(self):
        return []

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

    def query(self, _model):
        return _FakeQuery()


def _fake_ctx(**overrides):
    base = dict(
        agent=SimpleNamespace(
            id="a1", llm_timeout=30, sandbox_id=None, name="t",
            max_iterations=50, prompt="You are a test agent.", memory="",
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


def _hint_seen(seen: list[list[dict]], marker: str) -> bool:
    """True when any system message across the captured LLM contexts contains marker."""
    return any(
        marker in (m.get("content") or "")
        for msgs in seen
        for m in msgs
        if m.get("role") == "system"
    )


# ---- 6.1 破局提示：连续无进展 → 注入提示且不终止；有推进 → 清零不注入 ----

def test_no_progress_injects_breakthrough_hint_and_does_not_stop():
    ctx = _fake_ctx()
    ctx.agent.max_iterations = 8
    seen: list[list[dict]] = []

    async def _chat(llm, messages, **_kw):
        seen.append([dict(m) for m in messages])
        return ChatResult(text="还在思考…")

    async def _run():
        with patch(
            "app.services.llm_client.chat_completion", new=_chat,
        ), patch(
            "app.services.agent_runtime.system_prompt.SystemPromptBuilder.build_tools_desc",
            new=AsyncMock(return_value="- shell: SHELL: <cmd>"),
        ), patch.object(
            AgentRuntime, "_distill_final",
            new=AsyncMock(return_value="诚实总结：已完成 X，缺少 Y"),
        ):
            return await AgentRuntime().run(ctx)

    result = asyncio.run(_run())

    assert result == "诚实总结：已完成 X，缺少 Y"
    # 全程跑满预算（8 轮），未因无进展提前终止。
    assert len(seen) == 8
    # 连续无进展 → 注入「破局复盘」提示。
    assert _hint_seen(seen, "【破局复盘】")


def test_progress_resets_streak_and_does_not_inject():
    ctx = _fake_ctx()
    ctx.agent.max_iterations = 20
    replies = [f"SHELL: echo hi{i}" for i in range(6)] + ["FINAL: 完成"]
    seen: list[list[dict]] = []

    async def _chat(llm, messages, **_kw):
        seen.append([dict(m) for m in messages])
        return ChatResult(text=replies[len(seen) - 1])

    async def _run():
        with patch(
            "app.services.llm_client.chat_completion", new=_chat,
        ), patch(
            "app.services.agent_runtime.system_prompt.SystemPromptBuilder.build_tools_desc",
            new=AsyncMock(return_value="- shell: SHELL: <cmd>"),
        ), patch(
            "app.services.agent_tools.execute_action",
            new=AsyncMock(return_value="tool-ran"),
        ), patch.object(
            AgentRuntime, "_reflect_final", new=AsyncMock(return_value=None),
        ):
            return await AgentRuntime().run(ctx)

    result = asyncio.run(_run())

    assert result == "完成"
    # 每轮都有推进 → 清零，从不注入破局提示。
    assert not _hint_seen(seen, "【破局复盘】")


# ---- 6.2 全程复核：短任务 FINAL 仍走 _reflect_final ----

def test_short_task_still_reflects():
    ctx = _fake_ctx()

    async def _run():
        with patch(
            "app.services.llm_client.chat_completion",
            new=AsyncMock(return_value=ChatResult(text="FINAL: 直接完成")),
        ), patch(
            "app.services.agent_runtime.system_prompt.SystemPromptBuilder.build_tools_desc",
            new=AsyncMock(return_value="- shell: SHELL: <cmd>"),
        ), patch.object(
            AgentRuntime, "_reflect_final", new=AsyncMock(return_value=None),
        ) as reflect_mock:
            result = await AgentRuntime().run(ctx)

        # v15 撤销 R2：短任务（无子任务/无文件）也必须走完成度复核。
        reflect_mock.assert_awaited_once()
        return result

    assert asyncio.run(_run()) == "直接完成"


# ---- 6.3 上下文可用百分比：裁剪前口径、随占用下降、复用 estimate_tokens ----

def test_context_available_percent_decreases_with_usage():
    cm = ContextManager()
    cm.set_base(system_prompt="x" * 200)
    ctx = SimpleNamespace(llm=SimpleNamespace(max_context_tokens=1000))

    small = _context_available_percent(ctx, cm)
    cm.push_tool_result("y" * 600, action="shell")
    large = _context_available_percent(ctx, cm)

    assert 0 <= small <= 100
    assert 0 <= large <= 100
    assert large < small  # 占用增长 → 可用率下降


def test_context_available_percent_clamps_to_zero():
    cm = ContextManager()
    cm.set_base(system_prompt="x" * 10000)  # 远超 1000 预算
    ctx = SimpleNamespace(llm=SimpleNamespace(max_context_tokens=1000))
    assert _context_available_percent(ctx, cm) == 0


def test_context_available_percent_written_to_meta():
    ctx = _fake_ctx()
    ctx.llm = SimpleNamespace(id="llm1", max_context_tokens=1000)

    async def _run():
        with patch(
            "app.services.llm_client.chat_completion",
            new=AsyncMock(return_value=ChatResult(text="FINAL: 完成")),
        ), patch(
            "app.services.agent_runtime.system_prompt.SystemPromptBuilder.build_tools_desc",
            new=AsyncMock(return_value="- shell: SHELL: <cmd>"),
        ), patch.object(
            AgentRuntime, "_reflect_final", new=AsyncMock(return_value=None),
        ):
            return await AgentRuntime().run(ctx)

    result = asyncio.run(_run())

    assert result == "完成"
    assistant = [r for r in ctx.db.added if getattr(r, "role", None) == "assistant"]
    assert assistant, "应落盘一条 assistant ChatMessage"
    meta = json.loads(assistant[0].meta)
    assert "context_available_percent" in meta
    assert 0 <= meta["context_available_percent"] <= 100
