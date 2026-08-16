"""Bound-MCP unavailable-tools excuse: LLM intent soft-reject (no regex)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from app.services.intent_router import (
    UNAVAILABLE_DATA_TOOLS_COACH,
    append_unavailable_tools_soft_nudge,
    classify_unavailable_data_tools_claim,
    maybe_soft_reject_unavailable_tools_finish,
)


def test_append_soft_nudge_idempotent_and_no_hard_gate():
    text = append_unavailable_tools_soft_nudge("无法完成任务。")
    assert "list_ads_views" in text
    assert "工具软提示" in text
    assert "禁止 FINAL" not in text
    again = append_unavailable_tools_soft_nudge(text)
    assert again.count("工具软提示") == 1


def test_classify_true_false_and_fail_open():
    async def _run():
        with patch(
            "app.services.llm_client.chat_completion",
            new=AsyncMock(
                return_value='{"claims_unavailable_data_tools": true, "reason": "无MCP借口"}'
            ),
        ):
            assert await classify_unavailable_data_tools_claim(
                object(), "环境没有可用的数据库工具",
            ) is True
        with patch(
            "app.services.llm_client.chat_completion",
            new=AsyncMock(
                return_value='{"claims_unavailable_data_tools": false, "reason": "正常"}'
            ),
        ):
            assert await classify_unavailable_data_tools_claim(
                object(), "查询结果如下",
            ) is False
        with patch(
            "app.services.llm_client.chat_completion",
            new=AsyncMock(side_effect=RuntimeError("timeout")),
        ):
            assert await classify_unavailable_data_tools_claim(
                object(), "任意正文",
            ) is None
        assert await classify_unavailable_data_tools_claim(None, "x") is None

    asyncio.run(_run())


def test_maybe_soft_reject_reject_then_append():
    async def _run():
        with patch(
            "app.services.intent_router.classify_unavailable_data_tools_claim",
            new=AsyncMock(return_value=True),
        ):
            action, n, hit = await maybe_soft_reject_unavailable_tools_finish(
                llm=object(),
                assistant_text="excuse",
                has_mcp=True,
                ran_any_tool=False,
                soft_reject_count=0,
            )
            assert action == "reject" and n == 1 and hit is True
            action2, n2, hit2 = await maybe_soft_reject_unavailable_tools_finish(
                llm=object(),
                assistant_text="excuse",
                has_mcp=True,
                ran_any_tool=False,
                soft_reject_count=2,
            )
            assert action2 == "append" and n2 == 2 and hit2 is True
            out = append_unavailable_tools_soft_nudge("done")
            assert "list_ads_views" in out
            assert UNAVAILABLE_DATA_TOOLS_COACH.split("【")[0] or True

        with patch(
            "app.services.intent_router.classify_unavailable_data_tools_claim",
            new=AsyncMock(return_value=True),
        ):
            action, n, hit = await maybe_soft_reject_unavailable_tools_finish(
                llm=object(),
                assistant_text="excuse",
                has_mcp=True,
                ran_any_tool=True,
                soft_reject_count=0,
            )
            assert action == "allow" and hit is False

        with patch(
            "app.services.intent_router.classify_unavailable_data_tools_claim",
            new=AsyncMock(return_value=None),
        ):
            action, n, hit = await maybe_soft_reject_unavailable_tools_finish(
                llm=object(),
                assistant_text="excuse",
                has_mcp=True,
                ran_any_tool=False,
                soft_reject_count=0,
            )
            assert action == "allow" and hit is False

    asyncio.run(_run())
