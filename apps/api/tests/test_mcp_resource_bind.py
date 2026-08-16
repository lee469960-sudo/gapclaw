"""MCP-agnostic metric intent → resource binding tests."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services.intent_router import MetricIntentItem, parse_turn_intent_payload
from app.services.mcp_resource_bind import (
    bind_metrics_to_mcp,
    classify_catalog_tools,
    ensure_sql_resource_aligned,
    format_resource_binding_hint,
    merge_bindings,
    metric_intents_from_export_columns,
    metric_intents_from_turn,
    parse_bindings_payload,
    parse_resource_catalog,
    soft_seed_bindings,
    ResourceBinding,
)
from app.services.export_column_plan import apply_binding_hints_to_column_plan, build_column_plan


def test_parse_metric_intents_from_objects():
    turn = parse_turn_intent_payload({
        "intent": "data_query",
        "metrics": [
            {"goal": "充值卡数量", "kind": "count_distinct", "entity_hint": "payment_card"},
            {"goal": "提现卡数量", "kind": "count_distinct", "entity_hint": "payout_card"},
        ],
    })
    assert turn.metrics == ["充值卡数量", "提现卡数量"]
    assert len(turn.metric_intents) == 2
    assert turn.metric_intents[0].kind == "count_distinct"
    assert turn.metric_intents[0].entity_hint == "payment_card"


def test_metric_intents_strip_hardcoded_view_entity():
    turn = parse_turn_intent_payload({
        "intent": "data_query",
        "metrics": [
            {"goal": "充值卡数量", "kind": "count_distinct", "entity_hint": "view_result_pay_order_log"},
        ],
    })
    assert turn.metric_intents[0].entity_hint == ""


def test_classify_catalog_tools_ads_and_generic():
    ads = classify_catalog_tools([
        {"name": "list_ads_views", "description": "list views"},
        {"name": "describe_ads_view", "description": "schema"},
        {"name": "query_ads_view", "description": "query"},
    ])
    assert ads.list_tool == "list_ads_views"
    assert ads.query_tool == "query_ads_view"

    alt = classify_catalog_tools([
        {"name": "list_resources", "description": "enumerate tables"},
        {"name": "describe_resource", "description": "describe table schema"},
        {"name": "run_query", "description": "run sql select on table"},
    ])
    assert alt.list_tool == "list_resources"
    assert alt.describe_tool == "describe_resource"
    assert alt.query_tool == "run_query"


def test_ensure_sql_resource_aligned_rewrites_mismatch():
    sql = (
        "SELECT * FROM ads.view_result_user_bet_log "
        "WHERE create_time >= 1 AND create_time < 2"
    )
    fixed = ensure_sql_resource_aligned(
        "view_result_gameuser_betstat_everyday_bygame",
        sql,
    )
    assert "view_result_gameuser_betstat_everyday_bygame" in fixed
    assert "view_result_user_bet_log" not in fixed


def test_ensure_sql_resource_aligned_builds_from_where():
    out = ensure_sql_resource_aligned(
        "pay_cards",
        "WHERE status = 2",
        schema_prefix="ads",
    )
    assert out == "SELECT * FROM ads.pay_cards WHERE status = 2"


def test_soft_seed_only_preserves_explicit_resource_hint():
    intents = [
        MetricIntentItem(goal="充值卡数量", kind="count_distinct", entity_hint="payment_card"),
        MetricIntentItem(
            goal="提现卡数量",
            kind="count_distinct",
            entity_hint="payout_card",
            resource_hint="live_payout_cards",
        ),
    ]
    seeds = soft_seed_bindings(intents)
    assert len(seeds) == 1
    assert seeds[0].goal == "提现卡数量"
    assert seeds[0].resource == "live_payout_cards"
    assert seeds[0].source == "seed"


def test_bind_mock_catalog_prefers_card_resources_not_betlog():
    intents = [
        MetricIntentItem(goal="充值卡数量", kind="count_distinct", entity_hint="payment_card"),
    ]
    tools = [
        {"name": "list_resources", "description": "list"},
        {"name": "describe_resource", "description": "describe"},
        {"name": "run_query", "description": "query sql"},
    ]
    catalog = [
        {"name": "pay_cards", "description": "payment card numbers"},
        {"name": "cash_cards", "description": "payout cards"},
        {"name": "user_bet_log", "description": "betting log"},
    ]
    payload = (
        '{"bindings":[{"goal":"充值卡数量","tool":"run_query","resource":"pay_cards",'
        '"select_hint":"distinct card_id","filter_hints":["status=ok"],"confidence":0.9}]}'
    )
    llm = SimpleNamespace(type="llm", model="x")

    async def _run():
        with patch(
            "app.services.llm_client.chat_completion",
            new=AsyncMock(return_value=payload),
        ):
            return await bind_metrics_to_mcp(
                llm,
                intents,
                tools=tools,
                catalog_resources=catalog,
                allow_seed=True,
            )

    result = asyncio.run(_run())
    assert result.bindings
    b = result.bindings[0]
    assert b.resource == "pay_cards"
    assert b.tool == "run_query"
    assert "bet" not in b.resource
    hint = format_resource_binding_hint(result)
    assert "pay_cards" in hint
    assert "list→describe" in hint or "list" in hint


def test_bind_keeps_llm_choice_without_business_name_blacklist():
    intents = [
        MetricIntentItem(goal="充值银行卡数量", kind="count_distinct", entity_hint="payment_card"),
    ]
    tools = [
        {"name": "list_ads_views"},
        {"name": "describe_ads_view"},
        {"name": "query_ads_view"},
    ]
    catalog = [
        {"name": "view_result_pay_order_log", "description": "pay"},
        {"name": "view_result_user_bet_log", "description": "bet"},
    ]
    # The engine does not override semantic choices by a business-name regex.
    # Correctness comes from catalog/describe evidence supplied to the LLM.
    payload = (
        '{"bindings":[{"goal":"充值银行卡数量","tool":"query_ads_view",'
        '"resource":"view_result_user_bet_log","confidence":0.9}]}'
    )
    llm = SimpleNamespace(type="llm", model="x")

    async def _run():
        with patch(
            "app.services.llm_client.chat_completion",
            new=AsyncMock(return_value=payload),
        ):
            return await bind_metrics_to_mcp(
                llm,
                intents,
                tools=tools,
                catalog_resources=catalog,
                allow_seed=True,
            )

    result = asyncio.run(_run())
    assert result.bindings
    assert result.bindings[0].resource == "view_result_user_bet_log"
    assert result.bindings[0].source == "llm"


def test_parse_resource_catalog_json():
    rows = parse_resource_catalog(
        '{"views":[{"name":"pay_cards","description":"cards"},{"name":"user_bet_log"}]}'
    )
    assert rows[0]["name"] == "pay_cards"
    assert len(rows) == 2


def test_parse_resource_catalog_walks_nested_payloads():
    rows = parse_resource_catalog(
        '{"data":{"items":[{"resource_name":"view_result_user_info","table_comment":"用户信息表"},'
        '{"resource":"view_result_cash_order_log","comment":"提现订单表"}]}}'
    )
    assert [r["name"] for r in rows] == [
        "view_result_user_info",
        "view_result_cash_order_log",
    ]


def test_merge_and_parse_bindings():
    parsed = parse_bindings_payload(
        {
            "bindings": [
                {"goal": "x", "resource": "ghost", "tool": "q", "confidence": 0.8},
            ]
        },
        allowed_resources={"pay_cards"},
        default_tool="q",
    )
    assert parsed[0].resource == ""  # ghost rejected
    merged = merge_bindings(
        parsed,
        [ResourceBinding(goal="x", tool="q", resource="pay_cards", source="seed", confidence=0.35)],
    )
    assert merged[0].resource == "pay_cards"


def test_metric_intents_from_turn_fallback_query_goal():
    turn = parse_turn_intent_payload({
        "intent": "data_query",
        "query_goal": "统计充值卡数量",
        "metrics": [],
    })
    items = metric_intents_from_turn(turn)
    assert items and "充值卡" in items[0].goal


def test_metric_intents_from_export_columns_respects_ban_view_context():
    plan = [{"header": "是否被封禁"}]
    items = metric_intents_from_export_columns(
        plan,
        None,
        context_text="封禁需要看具体的封禁视图，请使用封禁视图来完成",
    )
    assert items
    assert items[0].goal == "是否被封禁"
    assert items[0].entity_hint == "ban_status"


def test_metric_intents_from_export_columns_extracts_explicit_column_view_preference():
    plan = build_column_plan(["用户ID", "注册时间", "是否被封禁"])
    items = metric_intents_from_export_columns(
        plan,
        None,
        context_text="第3列使用 view_result_user_block 做中文转换。",
    )
    by_goal = {item.goal: item for item in items}
    assert by_goal["是否被封禁"].resource_hint == "view_result_user_block"
    assert by_goal["用户ID"].resource_hint == ""


def test_metric_intents_from_export_columns_adds_user_and_payout_hints():
    plan = build_column_plan(["用户ID", "注册时间", "总提现金额", "是否被封禁"])
    items = metric_intents_from_export_columns(plan, None)
    by_goal = {item.goal: item for item in items}
    assert by_goal["用户ID"].entity_hint == "user_identity"
    assert by_goal["注册时间"].entity_hint == "user_register"
    assert by_goal["总提现金额"].entity_hint == "payout"
    assert by_goal["是否被封禁"].entity_hint == "ban_status"


def test_apply_binding_hints_propagates_same_role_resource_to_sibling_columns():
    plan = build_column_plan(["用户ID", "注册时间", "是否被封禁"])
    bound = apply_binding_hints_to_column_plan(
        plan,
        bindings=[{"goal": "是否被封禁", "resource": "view_result_user_info"}],
    )
    for col in bound:
        assert col["binding_status"] == "bound"
        assert col["数据来源"] == "`view_result_user_info`"


def test_apply_binding_hints_does_not_spread_specialized_view_to_other_user_columns():
    plan = build_column_plan(["用户ID", "注册时间", "是否被封禁"])
    bound = apply_binding_hints_to_column_plan(
        plan,
        bindings=[{"goal": "是否被封禁", "resource": "view_result_user_block"}],
    )
    by_header = {str(col.get("header")): col for col in bound}
    assert by_header["是否被封禁"]["数据来源"] == "`view_result_user_block`"
    assert by_header["是否被封禁"]["binding_status"] == "bound"
    assert by_header["用户ID"]["数据来源"] == "`view_result_user_info`"
    assert by_header["注册时间"]["数据来源"] == "`view_result_user_info`"
