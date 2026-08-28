"""Tests for soft FINAL reflection + FINAL-buried-in-think recovery + honest dead-end.

No LLM/MCP involved; chat_completion is mocked.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services.agent_runtime.loop_state import AgentLoopState
from app.services.agent_runtime.runtime import AgentRuntime, _forced_stop_reply
from app.services.tool_parser import extract_tool_steps


# ---- FINAL buried in <think> ----

def test_extract_tool_steps_recovers_final_in_think():
    steps = extract_tool_steps("<think>\nFINAL: 已完成导出\n</think>")
    finals = [s for s in steps if s.is_final]
    assert finals, "FINAL 埋在 think 里应被找回"
    assert "已完成导出" in finals[0].reply


def test_extract_tool_steps_plain_final_still_works():
    steps = extract_tool_steps("FINAL: 直接完成")
    assert any(s.is_final for s in steps)


def test_extract_tool_steps_no_final_in_think_only():
    steps = extract_tool_steps("<think>只是思考，没有结论</think>")
    assert not any(s.is_final for s in steps)


# ---- soft reflection ----

def _ctx(memory=""):
    return SimpleNamespace(
        agent=SimpleNamespace(id="a1", memory=memory, llm_timeout=30),
        user_message="导出 5124 行",
        llm=SimpleNamespace(id="llm1"),
        db=object(),
        chat_key="a1:s1",
    )


def test_reflect_final_pass_returns_none():
    ctx = _ctx()

    async def _run():
        with patch(
            "app.services.llm_client.chat_completion",
            new=AsyncMock(return_value="PASS"),
        ):
            return await AgentRuntime()._reflect_final(ctx, AgentLoopState(), "完成")

    assert asyncio.run(_run()) is None


def test_reflect_final_miss_returns_report():
    ctx = _ctx()

    async def _run():
        with patch(
            "app.services.llm_client.chat_completion",
            new=AsyncMock(return_value="FAIL: 缺少字段口径表\n修复清单：\n- 补全字段口径表\n- 给出最终 SQL"),
        ):
            return await AgentRuntime()._reflect_final(ctx, AgentLoopState(), "完成")

    report = asyncio.run(_run())
    assert report["missing"] == "缺少字段口径表"
    assert report["fix_list"] == ["补全字段口径表", "给出最终 SQL"]


def test_reflect_final_miss_without_fix_list():
    ctx = _ctx()

    async def _run():
        with patch(
            "app.services.llm_client.chat_completion",
            new=AsyncMock(return_value="FAIL: 缺少字段口径表"),
        ):
            return await AgentRuntime()._reflect_final(ctx, AgentLoopState(), "完成")

    report = asyncio.run(_run())
    assert report["missing"] == "缺少字段口径表"
    assert report["fix_list"] == []


def test_reflect_final_error_returns_none():
    ctx = _ctx()

    async def _run():
        with patch(
            "app.services.llm_client.chat_completion",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ):
            return await AgentRuntime()._reflect_final(ctx, AgentLoopState(), "完成")

    assert asyncio.run(_run()) is None


def test_reflect_final_miss_returns_revised_plan():
    ctx = _ctx()

    async def _run():
        with patch(
            "app.services.llm_client.chat_completion",
            new=AsyncMock(return_value=(
                "FAIL: 缺最终 SQL\n"
                "修复清单：\n- 补充最终 SQL\n"
                "修订 PLAN：\n- [x] 确认字段口径\n- [ ] 补 SQL\n- [ ] 生成 xlsx"
            )),
        ):
            return await AgentRuntime()._reflect_final(ctx, AgentLoopState(), "完成")

    report = asyncio.run(_run())
    assert report["missing"] == "缺最终 SQL"
    assert report["fix_list"] == ["补充最终 SQL"]
    assert report["revised_plan"] == "- [x] 确认字段口径\n- [ ] 补 SQL\n- [ ] 生成 xlsx"


# ---- budget-exhaustion distillation ----

def test_distill_final_returns_llm_summary():
    ctx = _ctx()
    state = AgentLoopState(goal="导出", progress_lines=["已写入 a.json"], saved_paths=["a.json"])

    async def _run():
        with patch(
            "app.services.llm_client.chat_completion",
            new=AsyncMock(return_value="已完成部分，缺少 xlsx"),
        ):
            return await AgentRuntime()._distill_final(ctx, state, "达到上限")

    assert asyncio.run(_run()) == "已完成部分，缺少 xlsx"


def test_distill_final_falls_back_on_error():
    ctx = _ctx()
    state = AgentLoopState(goal="导出", progress_lines=["已写入 a.json"])

    async def _run():
        with patch(
            "app.services.llm_client.chat_completion",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ):
            return await AgentRuntime()._distill_final(ctx, state, "达到上限")

    out = asyncio.run(_run())
    assert "未完成" in out


# ---- honest dead-end ----

def test_forced_stop_reply_marks_incomplete():
    state = AgentLoopState(
        progress_lines=["已写入 task/1/a.json"],
        saved_paths=["a.json"],
    )
    reply = _forced_stop_reply(state, "工具失败")
    assert "未完成" in reply
    assert "中间产物" in reply
    assert "非最终交付" in reply
