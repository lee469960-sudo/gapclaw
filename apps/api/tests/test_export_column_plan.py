"""Column-driven export: header → sources; roles not always hexad."""

from app.services.export_column_plan import (
    build_column_plan,
    format_column_plan_summary,
    roles_from_column_plan,
    unknown_columns,
    views_from_column_plan,
)
from app.services.react_engine import (
    _export_roles_ready_for_analyze,
    _type_b_prefilled_plan_ready,
)


def test_pay_and_channel_columns_exclude_bet():
    plan = build_column_plan(
        ["用户ID", "总充值金额", "注册渠道"]
    )
    views = views_from_column_plan(plan)
    assert any("pay" in v for v in views)
    assert any("user_info" in v for v in views)
    assert not any("bet" in v for v in views)
    assert not any("cash" in v for v in views)
    assert not any("config_game" in v for v in views)
    summary = format_column_plan_summary(plan)
    assert "总充值金额" in summary
    assert "本轮目标 role：user、pay、channel" in summary


def test_identity_columns_only_user():
    plan = build_column_plan(["用户ID", "姓", "名", "手机"])
    views = views_from_column_plan(plan)
    assert views == ["view_result_user_info"]
    assert not unknown_columns(plan)


def test_unknown_column_does_not_force_hexad():
    plan = build_column_plan(["用户ID", "神秘自定义列XYZ"])
    views = views_from_column_plan(plan)
    assert views == ["view_result_user_info"]
    assert "神秘自定义列XYZ" in unknown_columns(plan)


def test_roles_ready_skips_user_gate_when_user_not_targeted():
    # pay-only plan: no user target → do not require user short page
    assert _export_roles_ready_for_analyze(
        missing_roles=[],
        has_data=True,
        user_fetch_complete=False,
        user_pages=0,
        fact_need_continue=[],
        target_roles=["pay"],
    )
    # user in targets → still need short page or cap
    assert not _export_roles_ready_for_analyze(
        missing_roles=[],
        has_data=True,
        user_fetch_complete=False,
        user_pages=0,
        fact_need_continue=[],
        target_roles=["user", "pay"],
    )


def test_type_b_column_plan_does_not_skip_schema_discovery():
    todos = [
        {"phase": "analyze", "text": "用户ID"},
        {"phase": "analyze", "text": "总充值金额"},
        {"phase": "analyze", "text": "注册渠道"},
        {"phase": "analyze", "text": "当前余额"},
    ]
    plan = build_column_plan([t["text"] for t in todos])
    assert not _type_b_prefilled_plan_ready(
        todos, roles_from_column_plan(plan), "multi_fact",
        column_plan=plan,
    )
    assert not _type_b_prefilled_plan_ready(
        todos, ["user"], "single_view", column_plan=plan,
    )


def test_type_c_mode_not_overridden_by_helpers():
    # single_view must not use type-B prefilled skip
    todos = [{"phase": "analyze", "text": f"列{i}"} for i in range(12)]
    assert not _type_b_prefilled_plan_ready(
        todos, [], "single_view", column_plan=build_column_plan(["a"]),
    )
