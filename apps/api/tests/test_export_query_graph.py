"""Column-plan query graph, plan card, anti-hallucination."""

import json

from app.services.export_column_plan import (
    FETCH_AGG,
    FETCH_DERIVED,
    FETCH_SEQUENCE,
    FETCH_TOP_N,
    apply_binding_hints_to_column_plan,
    apply_schema_hints_to_column_plan,
    build_column_plan,
    build_query_graph,
    column_plan_entry_is_bound,
    format_column_plan_card,
    mcp_line_from_query_node,
    needs_export_clarify,
    query_graph_complete,
    query_graph_done_enough,
    roles_from_column_plan,
    sanitize_final_without_landing,
    sql_from_agg_spec,
    text_has_export_stat_claims,
)
_GOLDEN_16 = [
    "注册时间",
    "用户ID",
    "注册渠道",
    "总充值金额",
    "总提现金额",
    "当前余额(SC)",
    "总下注金额(SC)",
    "总返奖金额(SC)",
    "下注次数",
    "流水倍数",
    "连续充值次数",
    "SC投注金额最多的游戏",
    "是否被封禁",
    "是否有退款",
    "充值银行卡数量",
    "提现银行卡数量",
]


def test_golden_16_plan_modes():
    plan = build_column_plan(_GOLDEN_16)
    assert len(plan) == 16
    by_h = {c["header"]: c for c in plan}
    assert by_h["总充值金额"]["fetch_mode"] == FETCH_AGG
    assert by_h["流水倍数"]["fetch_mode"] == FETCH_DERIVED
    assert by_h["连续充值次数"]["fetch_mode"] == FETCH_SEQUENCE
    assert by_h["SC投注金额最多的游戏"]["fetch_mode"] == FETCH_TOP_N
    assert by_h["是否有退款"]["fetch_mode"] == "flag"
    roles = roles_from_column_plan(plan)
    for r in ("user", "pay", "cash", "bet", "channel", "game"):
        assert r in roles


def test_plan_card_table_shape():
    plan = build_column_plan(["用户ID", "总充值金额", "流水倍数"])
    card = format_column_plan_card(plan)
    assert "列计划卡" in card
    assert "总充值金额" in card
    assert "数据来源" in card
    assert "统计方法" in card
    assert "待 MCP list/describe + LLM 绑定" in card
    assert "view_result_pay_order_log" not in card
    assert "| # |" in card


def test_golden16_koujing_screenshot_keywords():
    plan = build_column_plan(_GOLDEN_16)
    by_h = {c["header"]: c for c in plan}
    pay = by_h["总充值金额"]
    assert "view_result_pay_order_log" in pay["数据来源"]
    assert "status=2" in pay["统计方法"]
    cash = by_h["总提现金额"]
    assert "status=2" in cash["统计方法"]
    cards = by_h["充值银行卡数量"]
    assert "channel_id=11" in cards["统计方法"]
    refund = by_h["是否有退款"]
    assert "status=4" in refund["统计方法"]
    bal = by_h["当前余额(SC)"]
    assert "sc/100" in bal["统计方法"]


def test_agg_sql_pay_group_by_uid():
    plan = build_column_plan(["总充值金额"])
    spec = plan[0]["agg_spec"]
    tw = {"start_ms": 1000, "end_ms": 2000}
    view, sql = sql_from_agg_spec(spec, tw)
    assert view == "view_result_pay_order_log"
    assert "GROUP BY uid" in sql
    assert "sum(price)" in sql.lower() or "sum(amount)" in sql.lower()
    assert "status = 2" in sql
    assert "finish_time" in sql or "create_time" in sql
    assert "create_time >= 1000" in sql or "finish_time >= 1000" in sql


def test_query_graph_dedupes_and_skips_derived():
    plan = build_column_plan(
        ["用户ID", "总充值金额", "总下注金额", "流水倍数", "注册渠道"]
    )
    tw = {"start_ms": 1, "end_ms": 2, "cohort": "pay"}
    nodes = build_query_graph(plan, tw)
    modes = {n["mode"] for n in nodes}
    assert "derived" not in modes
    assert any(n["mode"] == "agg" for n in nodes)


def test_runtime_query_graph_requires_live_resource_binding():
    plan = build_column_plan(["是否被封禁"])
    assert build_query_graph(plan, {"start_ms": 1, "end_ms": 2}, bound_only=True) == []

    bound = apply_binding_hints_to_column_plan(
        plan,
        bindings=[{"goal": "是否被封禁", "resource": "view_result_user_ban_status"}],
    )
    nodes = build_query_graph(bound, {"start_ms": 1, "end_ms": 2}, bound_only=True)
    assert nodes
    assert {n["view"] for n in nodes} == {"view_result_user_ban_status"}
    for n in nodes:
        line = mcp_line_from_query_node(n)
        assert line.startswith("MCP: query_ads_view ")
        args = json.loads(line.split(" ", 2)[2])
        assert args["view"] == "view_result_user_ban_status"


def test_describe_resolved_column_is_treated_as_bound_for_card_and_query_graph():
    plan = build_column_plan(["是否被封禁"])
    bound = apply_binding_hints_to_column_plan(
        plan,
        bindings=[{"goal": "是否被封禁", "resource": "view_result_user_ban_status"}],
    )
    resolved = apply_schema_hints_to_column_plan(
        bound,
        schema_hints={
            "view_result_user_ban_status": {
                "fields": ["uid", "ban_type"],
                "comments": {"ban_type": "封禁类型"},
                "view_comment": "用户封禁状态",
            }
        },
    )
    assert column_plan_entry_is_bound(resolved[0]) is True
    card = format_column_plan_card(resolved)
    assert "待 MCP list/describe + LLM 绑定" not in card
    assert "`view_result_user_ban_status`" in card
    nodes = build_query_graph(resolved, {"start_ms": 1, "end_ms": 2}, bound_only=True)
    assert nodes
    assert {n["view"] for n in nodes} == {"view_result_user_ban_status"}


def test_pay_cohort_does_not_invent_bet_sequence_raw_log():
    plan = build_column_plan(["用户ID", "总充值金额", "连续充值次数"])
    tw = {"start_ms": 1000, "end_ms": 2000, "cohort": "pay"}
    nodes = build_query_graph(plan, tw)
    # Pay sequence may exist; raw bet log sequence must not be invented
    assert not any(
        n.get("role") == "bet" and n.get("mode") == FETCH_SEQUENCE for n in nodes
    )
    assert not any("user_bet_log" in str(n.get("view") or "") for n in nodes)
    assert any(n.get("role") == "pay" and n.get("mode") == FETCH_SEQUENCE for n in nodes)


def test_pay_cohort_graph_adds_uid_anchor_before_user_and_pay_sum():
    plan = build_column_plan(["用户ID", "注册渠道", "总充值金额"])
    tw = {"start_ms": 1000, "end_ms": 2000, "cohort": "pay"}
    nodes = build_query_graph(plan, tw, page_limit=1000)

    anchor = next(n for n in nodes if str(n.get("key", "")).startswith("cohort:pay_uid:"))
    assert anchor["role"] == "pay"
    assert anchor["segment"] == "cohort"
    assert anchor["limit"] == 1000
    assert "SELECT uid, 1 AS cohort_uid" in anchor["sql"]
    assert "finish_time >= 1000" in anchor["sql"]
    assert "finish_time < 2000" in anchor["sql"]
    assert "status = 2" in anchor["sql"]
    assert "GROUP BY uid" in anchor["sql"]

    keys = [n["key"] for n in nodes]
    user_idx = next(i for i, n in enumerate(nodes) if n.get("role") == "user")
    pay_sum_idx = next(i for i, n in enumerate(nodes) if "pay_sum" in str(n.get("sql") or ""))
    assert keys.index(anchor["key"]) < user_idx
    assert keys.index(anchor["key"]) < pay_sum_idx


def test_query_graph_complete():
    nodes = [{"view": "view_result_pay_order_log", "mode": "agg"}]
    assert not query_graph_complete(nodes, {})
    assert query_graph_complete(
        nodes, {"view_result_pay_order_log": 1},
    )


def test_query_graph_done_enough_failed_counts_as_handled():
    nodes = [
        {"view": "view_result_user_info", "role": "user", "segment": "identity"},
        {"view": "view_result_pay_order_log", "role": "pay", "segment": "fact_agg"},
        {
            "view": "view_result_gameuser_betstat_everyday_bygame",
            "role": "bet",
            "segment": "bet_daily",
        },
    ]
    pages = {"view_result_user_info": 1, "view_result_pay_order_log": 1}
    assert not query_graph_done_enough(nodes, pages, set())
    assert query_graph_done_enough(
        nodes, pages, {"view_result_gameuser_betstat_everyday_bygame"},
    )


def test_query_graph_keeps_user_field_on_bound_aux_view():
    plan = build_column_plan(["用户ID", "是否被封禁"])
    plan = apply_binding_hints_to_column_plan(
        plan,
        bindings=[{"goal": "是否被封禁", "resource": "view_result_user_ban_log"}],
    )
    nodes = build_query_graph(plan, {"start_ms": 1000, "end_ms": 2000})
    assert any(
        n.get("mode") == "field" and n.get("view") == "view_result_user_ban_log"
        for n in nodes
    )


def test_sanitize_bans_fabricated_overview():
    fake = (
        "| 总用户数 | 2650 |\n| 被封禁用户 | 295 |\n"
        "有 SC 下注记录 2565"
    )
    assert text_has_export_stat_claims(fake)
    out = sanitize_final_without_landing(fake, has_task_data=False)
    assert "2650" not in out or "禁止" in out
    assert "未能交付可信概况" in out
    ok = sanitize_final_without_landing(fake, has_task_data=True)
    assert "2650" in ok




def test_needs_clarify_without_window():
    q = needs_export_clarify(
        time_window=None,
        column_headers=[],
        user_message="帮我导出一下数据",
    )
    assert "时间" in q or "对齐" in q
    assert not needs_export_clarify(
        time_window={"start_ms": 1, "end_ms": 2},
        column_headers=["用户ID", "总充值金额"],
        user_message="导出充值用户",
    )


def test_top_n_most_game_uses_bet_sc_not_amount():
    plan = build_column_plan(["SC投注金额最多的游戏"])
    spec = plan[0]["agg_spec"]
    assert spec.get("mode") == FETCH_TOP_N or spec.get("mode") == "top_n"
    tw = {"start_ms": 1000, "end_ms": 2000}
    view, sql = sql_from_agg_spec(spec, tw)
    assert view == "view_result_user_bet_log"
    assert "sum(bet_sc)" in sql.lower()
    assert "sum(amount)" not in sql.lower()
    assert "create_time >= 1000" in sql


def test_bet_everyday_stat_date_uses_to_date_milli():
    plan = build_column_plan(["下注次数"])
    spec = plan[0]["agg_spec"]
    tw = {"start_ms": 1_700_000_000_000, "end_ms": 1_700_086_400_000}
    view, sql = sql_from_agg_spec(spec, tw)
    assert "everyday_bygame" in view
    assert "toDate(fromUnixTimestamp64Milli(" in sql
    assert "stat_date >=" in sql
    assert "goods_type IN (1, 4)" in sql
    # Must not compare Date column to raw ms literals
    assert "stat_date >= 1700000000000" not in sql


def test_pay_count_aligns_finish_time_with_amount():
    plan = build_column_plan(["充值次数"])
    spec = plan[0]["agg_spec"]
    tw = {"start_ms": 1000, "end_ms": 2000}
    _view, sql = sql_from_agg_spec(spec, tw)
    assert "finish_time >= 1000" in sql
    assert "finish_time < 2000" in sql
    assert "status = 2" in sql
    assert "count()" in sql.lower() or "count(*)" in sql.lower()
    assert "create_time >= 1000" not in sql


def test_aptpay_cards_use_json_extract_not_raw_account():
    tw = {"start_ms": 1000, "end_ms": 2000}
    pay_plan = build_column_plan(["充值银行卡数量"])
    pay_spec = pay_plan[0]["agg_spec"]
    assert "cardNumber" in (pay_spec.get("select") or "")
    assert "cardNumber" in (pay_plan[0].get("统计方法") or "")
    _v, pay_sql = sql_from_agg_spec(pay_spec, tw)
    assert "JSONExtractString" in pay_sql
    assert "cardNumber" in pay_sql
    assert "count(DISTINCT account)" not in pay_sql.lower().replace(" ", "")
    # whitespace-tolerant: no bare DISTINCT account
    assert "DISTINCT account)" not in pay_sql
    assert "channel_id = 11" in pay_sql
    assert "finish_time" in pay_sql
    assert "status = 2" in pay_sql

    cash_plan = build_column_plan(["提现银行卡数量"])
    cash_spec = cash_plan[0]["agg_spec"]
    assert "disbursementNumber" in (cash_spec.get("select") or "")
    _v2, cash_sql = sql_from_agg_spec(cash_spec, tw)
    assert "disbursementNumber" in cash_sql
    assert "JSONExtractString" in cash_sql
    assert "DISTINCT account)" not in cash_sql
    assert "update_time" in cash_sql
    assert "CARD" in cash_sql.upper()
