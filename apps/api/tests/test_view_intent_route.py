"""View intent routing keeps resource pins separate from execution binding."""

from app.services.task_policy import (
    extract_pinned_views,
    route_export_view_intent,
    strip_view_tokens,
)
from app.services.react_engine import _apply_view_intent_route


def test_gameuser_view_name_is_only_an_explicit_resource_pin():
    msg = "通过 view_result_gameuser_stat_everyday_all 进行分析并导出 xlsx"
    route = route_export_view_intent(msg)
    assert route.pinned_views == ["view_result_gameuser_stat_everyday_all"]


def test_extract_pinned_views():
    msg = "用 ads.view_result_gameuser_stat_everyday_all 和 view_result_user_info"
    views = extract_pinned_views(msg)
    assert views[0] == "view_result_gameuser_stat_everyday_all"
    assert "view_result_user_info" in views


def test_strip_view_tokens_removes_gameuser():
    raw = "分析 view_result_gameuser_stat_everyday_all 报表"
    assert "gameuser" not in strip_view_tokens(raw).lower()


def test_named_agg_view_is_single_view():
    msg = "请用 view_result_gameuser_stat_everyday_all 进行分析导出"
    route = route_export_view_intent(msg)
    assert route.mode == "single_view"
    assert route.pinned_views == ["view_result_gameuser_stat_everyday_all"]
    mode, pins = _apply_view_intent_route(route)
    assert mode == "single_view"
    assert pins == ["view_result_gameuser_stat_everyday_all"]


def test_plan_resource_view_changes_mode_without_execution_targets():
    msg = "用 view_result_gameuser_stat_everyday_all 导出"
    plan = "PLAN:\n- 需要资源: view_result_pay_order_log\n- 输出列: 总充值"
    route = route_export_view_intent(msg, plan)
    assert route.mode == "multi_fact"
    assert route.pinned_views == []


def test_strong_multi_without_pin_is_multi_fact():
    msg = (
        "导出新增注册用户报表，列：\n"
        "1. 注册时间\n2. 用户ID\n3. 注册渠道\n4. 总充值金额\n"
        "5. 总提现金额\n6. 流水倍数\n7. 是否有退款\n8. SC投注金额最多的游戏\n"
    )
    route = route_export_view_intent(msg)
    assert route.mode == "multi_fact"
    # Mode only — resource binding happens after MCP list/describe.


def test_pin_only_hint():
    msg = "仅通过 view_result_pay_order_log 拉取并导出，不要查其它表"
    route = route_export_view_intent(msg)
    assert route.mode == "single_view"
    assert route.pinned_views == ["view_result_pay_order_log"]
