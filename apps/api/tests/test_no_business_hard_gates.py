"""Hot path: no keyword/archive invent of roles/views; dims never hard-block analyze."""

from __future__ import annotations

from app.services.react_engine import (
    _autofill_ads_query_args,
    _build_run_state_next_actions,
    _executable_query_example,
    _should_hard_defer_analyze_for_missing_dims,
    _view_for_export_pull,
)
from app.services.task_policy import route_export_view_intent


def test_strong_multi_fact_mode_without_invented_roles():
    msg = (
        "导出新增注册用户报表，列：\n"
        "1. 注册时间\n2. 用户ID\n3. 注册渠道\n4. 总充值金额\n"
        "5. 总提现金额\n6. 流水倍数\n7. 是否有退款\n8. SC投注\n"
    )
    route = route_export_view_intent(msg)
    assert route.mode == "multi_fact"


def test_view_for_export_pull_facts_empty_without_contract():
    assert _view_for_export_pull("pay") == ""
    assert _view_for_export_pull("cash") == ""
    assert _view_for_export_pull("bet") == ""


def test_autofill_empty_view_no_archive_from_roles():
    args, notes = _autofill_ads_query_args(
        {},
        missing_roles=["bet", "pay"],
        target_roles=["user", "pay", "cash", "bet"],
    )
    assert not args.get("view")
    assert "user_bet_log" not in str(notes)
    assert "pay_order_log" not in str(args)


def test_executable_example_no_role_archive_invent():
    ex = _executable_query_example(target_roles=["pay", "bet"])
    assert "user_bet_log" not in ex
    assert "view_result_pay_order_log" not in ex
    assert "list_ads_views" in ex or "白名单" in ex


def test_next_actions_missing_role_no_archive_mcp():
    actions = _build_run_state_next_actions(
        completeness="truncated",
        missing_roles=["bet"],
        fetched_view_pages={},
    )
    bet = next(a for a in actions if a.get("role") == "bet")
    assert not bet.get("view")
    assert "user_bet_log" not in str(bet.get("mcp_example") or "")
    assert "list" in (bet.get("hint") or "").lower() or "绑定" in (bet.get("hint") or "")


def test_dims_never_hard_defer_analyze():
    assert _should_hard_defer_analyze_for_missing_dims(
        ["channel", "game"],
        budget_left=5,
        force_rounds=0,
    ) is False
    assert _should_hard_defer_analyze_for_missing_dims(
        ["channel"],
        budget_left=0,
        force_rounds=99,
    ) is False
