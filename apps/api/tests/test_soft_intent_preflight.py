"""Soft intent preflight: no remote describe stall; clarify is not abort."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services.intent_router import MetricIntentItem, TurnIntent
from app.services.mcp_resource_bind import format_need_based_query_plan
from app.services.react_engine import (
    _bind_mcp_resources_for_turn,
    _is_clarify_progress_reply,
)


def test_gap_copy_is_soft_not_hard_stop():
    turn = TurnIntent(
        intent="data_query",
        query_goal="注册人数",
        metrics=["注册人数"],
        metric_intents=[MetricIntentItem(goal="注册人数", kind="count")],
    )
    plan = format_need_based_query_plan(turn, gaps=["time_window"], bind_result=None)
    assert "【查数计划·按需】" in plan
    assert "软教练" in plan or "允许 MCP" in plan
    assert "不要因缺口停止" in plan or "继续 MCP" in plan
    assert "禁止一切" not in plan
    assert "补齐前禁止" not in plan
    assert "不是禁止使用 MCP" in plan or "允许 MCP" in plan


def test_clarify_progress_reply_detected():
    assert _is_clarify_progress_reply("FINAL: 请问要查哪一天的注册人数？")
    assert _is_clarify_progress_reply("请问要查哪一天？")
    assert _is_clarify_progress_reply("需要确认一下时间窗吗")
    assert not _is_clarify_progress_reply("MCP: list_ads_views {}")
    assert not _is_clarify_progress_reply("正在拉全量明细请稍候")


def test_preflight_bind_skips_remote_list_describe():
    """Preflight must not call call_mcp_tool (list/describe deferred to tool loop)."""
    turn = TurnIntent(
        intent="data_query",
        metrics=["充值卡数量"],
        metric_intents=[
            MetricIntentItem(goal="充值卡数量", kind="count_distinct", entity_hint="payment_card"),
        ],
    )
    llm = SimpleNamespace(type="llm", model="x")
    mcp = SimpleNamespace(id="m1", name="ads")
    tools = [
        {"name": "list_ads_views"},
        {"name": "describe_ads_view"},
        {"name": "query_ads_view"},
    ]
    db = SimpleNamespace(query=lambda *_a, **_k: SimpleNamespace(
        filter=lambda *_a2, **_k2: SimpleNamespace(first=lambda: mcp),
    ))

    call_mcp = AsyncMock(side_effect=AssertionError("preflight must not call MCP tools"))

    async def _run():
        with patch(
            "app.services.react_engine._get_mcp_tools_cached",
            new=AsyncMock(return_value=tools),
        ), patch(
            "app.services.mcp_client.call_mcp_tool",
            new=call_mcp,
        ), patch(
            "app.services.llm_client.chat_completion",
            new=AsyncMock(side_effect=TimeoutError("bind llm slow")),
        ):
            return await _bind_mcp_resources_for_turn(
                db=db,
                llm=llm,
                agent=SimpleNamespace(id="a1"),
                mcp_ids=["m1"],
                turn_intent=turn,
                allow_seed=True,
            )

    result = asyncio.run(_run())
    call_mcp.assert_not_called()
    # Seed fallback still ok; must not raise
    assert result is not None
    assert not result.error or "Timeout" in result.error or result.bindings is not None


def test_preflight_timeout_falls_back_to_seed():
    from app.services.mcp_resource_bind import BindResult, ResourceBinding

    turn = TurnIntent(
        intent="data_query",
        metrics=["充值银行卡数量"],
        metric_intents=[MetricIntentItem(goal="充值银行卡数量", kind="count_distinct")],
    )
    llm = SimpleNamespace(type="llm", model="x")
    calls = {"n": 0}

    async def _flaky_bind(llm_arg, *_a, **_k):
        calls["n"] += 1
        if llm_arg is not None:
            raise asyncio.TimeoutError("bind llm timeout")
        return BindResult(
            bindings=[
                ResourceBinding(
                    goal="充值银行卡数量",
                    resource="view_result_pay_order_log",
                    source="seed",
                    confidence=0.35,
                )
            ],
            used_seed=True,
        )

    async def _run():
        with patch(
            "app.services.react_engine._get_mcp_tools_cached",
            new=AsyncMock(return_value=[{"name": "query_ads_view"}]),
        ), patch(
            "app.services.react_engine.bind_metrics_to_mcp",
            new=_flaky_bind,
        ):
            return await _bind_mcp_resources_for_turn(
                db=SimpleNamespace(query=lambda *_a, **_k: SimpleNamespace(
                    filter=lambda *_a2, **_k2: SimpleNamespace(
                        first=lambda: SimpleNamespace(id="m1"),
                    ),
                )),
                llm=llm,
                agent=SimpleNamespace(id="a1"),
                mcp_ids=["m1"],
                turn_intent=turn,
                allow_seed=True,
            )

    result = asyncio.run(_run())
    assert result.used_seed or result.bindings
    assert result.bindings[0].resource == "view_result_pay_order_log"
    assert calls["n"] >= 2
