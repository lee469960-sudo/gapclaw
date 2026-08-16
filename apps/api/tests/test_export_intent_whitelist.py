"""Export path: column intent → resource whitelist; never default hexad."""

from __future__ import annotations

from app.services.export_column_plan import (
    build_column_plan,
    roles_from_column_plan,
    views_from_column_plan,
)
from app.services.mcp_resource_bind import (
    BindResult,
    ResourceBinding,
    resolve_export_resource_whitelist,
)
from app.services.task_policy import route_export_view_intent


def test_card_columns_no_bet_role():
    plan = build_column_plan(
        ["用户ID", "充值银行卡数量", "提现银行卡数量", "注册时间"],
    )
    views = roles_from_column_plan(plan)
    assert not any("bet" in v for v in views)
    assert not any("game" in v for v in views)
    views = views_from_column_plan(plan)
    assert not any("bet" in v.lower() for v in views)


def test_route_strong_cols_not_hexad_fill():
    msg = (
        "导出新增注册用户报表，列：\n"
        "1. 注册时间\n2. 用户ID\n3. 注册渠道\n4. 总充值金额\n"
        "5. 总提现金额\n6. 流水倍数\n7. 是否有退款\n8. 充值卡\n"
    )
    route = route_export_view_intent(msg)
    assert route.mode in ("multi_fact", "ambiguous")
    # Keywords do not authorize a source; MCP binding fills it later.


def test_resolve_whitelist_requires_runtime_binding_not_column_template():
    plan = build_column_plan(["用户ID", "充值银行卡数量", "提现银行卡数量"])
    wl = resolve_export_resource_whitelist(plan, allow_seed=False)
    assert wl == []


def test_resolve_whitelist_prefers_bind_over_seed():
    plan = build_column_plan(["用户ID", "充值银行卡数量"])
    bind = BindResult(
        bindings=[
            ResourceBinding(
                goal="充值银行卡数量",
                resource="pay_cards",
                confidence=0.9,
                source="llm",
            ),
        ],
    )
    wl = resolve_export_resource_whitelist(plan, bind_result=bind, allow_seed=False)
    assert wl[0] == "pay_cards"
    assert "pay_cards" in wl
