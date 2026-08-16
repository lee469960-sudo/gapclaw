"""Need-based data_query: gaps + whitelist, no hexad full fetch."""

from __future__ import annotations

from app.services.intent_router import (
    MetricIntentItem,
    TurnIntent,
    compute_data_query_gaps,
    parse_turn_intent_payload,
)
from app.services.mcp_resource_bind import (
    BindResult,
    CatalogTools,
    ResourceBinding,
    binding_resource_whitelist,
    format_need_based_query_plan,
)


def test_gaps_missing_time_window_for_metric_ask():
    turn = parse_turn_intent_payload({
        "intent": "data_query",
        "query_goal": "查一下注册人数",
        "metrics": [{"goal": "注册人数", "kind": "count", "entity_hint": "user_register"}],
        "time_window": None,
    })
    gaps = compute_data_query_gaps(turn, user_message="查一下注册人数")
    assert "time_window" in gaps
    assert "metrics" not in gaps


def test_gaps_no_time_when_window_resolved():
    turn = parse_turn_intent_payload({
        "intent": "data_query",
        "metrics": [{"goal": "注册人数", "kind": "count"}],
        "time_window": {
            "start_date": "2026-08-06",
            "end_date": "2026-08-06",
            "inclusive_end_day": True,
            "tz": "ET",
            "label": "2026-08-06",
        },
    })
    gaps = compute_data_query_gaps(
        turn,
        user_message="获取一下8月6日的新增注册人数",
    )
    assert "time_window" not in gaps


def test_gaps_metrics_empty_data_query():
    turn = TurnIntent(intent="data_query", query_goal="", metrics=[], metric_intents=[])
    gaps = compute_data_query_gaps(turn, user_message="帮我查一下数据")
    assert "metrics" in gaps


def test_gaps_chat_empty():
    turn = TurnIntent(intent="chat")
    assert compute_data_query_gaps(turn, user_message="你好") == []


def test_gaps_resource_when_unbound():
    turn = parse_turn_intent_payload({
        "intent": "data_query",
        "metrics": [{"goal": "充值卡数量", "kind": "count_distinct"}],
        "time_window": {
            "start_date": "2026-08-06",
            "end_date": "2026-08-06",
            "inclusive_end_day": True,
            "tz": "ET",
        },
    })
    bindings = [
        ResourceBinding(goal="充值卡数量", resource="", confidence=0.2, source="llm"),
    ]
    gaps = compute_data_query_gaps(turn, user_message="充值卡数量", bindings=bindings)
    assert "resource" in gaps
    assert "time_window" not in gaps


def test_whitelist_card_only_no_bet_no_hexad():
    result = BindResult(
        bindings=[
            ResourceBinding(
                goal="充值卡数量",
                tool="run_query",
                resource="pay_cards",
                confidence=0.9,
                source="llm",
            ),
        ],
        catalog=CatalogTools(
            list_tool="list_resources",
            describe_tool="describe_resource",
            query_tool="run_query",
            all_names=["list_resources", "describe_resource", "run_query"],
        ),
    )
    wl = binding_resource_whitelist(result)
    assert wl == ["pay_cards"]
    assert "user_bet_log" not in wl
    assert "bet" not in " ".join(wl).lower()

    turn = TurnIntent(
        intent="data_query",
        query_goal="充值卡数量",
        metrics=["充值卡数量"],
        metric_intents=[
            MetricIntentItem(goal="充值卡数量", kind="count_distinct", entity_hint="payment_card"),
        ],
        time_window={
            "start_ms": 1,
            "end_ms": 2,
            "label": "d",
            "tz": "ET",
            "start_date": "2026-08-06",
            "end_date": "2026-08-06",
        },
    )
    plan = format_need_based_query_plan(
        turn,
        gaps=[],
        bind_result=result,
        time_window=turn.time_window,
    )
    assert "【查数计划·按需】" in plan
    assert "pay_cards" in plan
    assert "白名单" in plan
    assert "user_bet_log" not in plan
    assert "全量" in plan
    # Must not coach full view list
    assert "禁止" in plan


def test_plan_with_gaps_soft_coach():
    turn = TurnIntent(
        intent="data_query",
        query_goal="注册人数",
        metrics=["注册人数"],
        metric_intents=[MetricIntentItem(goal="注册人数", kind="count")],
    )
    plan = format_need_based_query_plan(
        turn,
        gaps=["time_window"],
        bind_result=None,
    )
    assert "仍缺" in plan
    assert "时间窗" in plan
    assert "全量" in plan
    assert "不要因缺口停止" in plan or "继续 MCP" in plan


def test_plan_exposes_llm_time_series_shape_without_business_keywords():
    turn = TurnIntent(
        intent="data_query",
        query_goal="查看指标",
        metrics=["指标A"],
        metric_intents=[MetricIntentItem(goal="指标A", kind="sum")],
        result_shape="time_series",
        temporal_grain="day",
    )
    plan = format_need_based_query_plan(turn, gaps=[], bind_result=None)
    assert "结果形态=time_series/day" in plan
