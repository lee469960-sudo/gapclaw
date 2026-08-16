"""投注/返奖：whitelist 跟 agg_spec；返浆同义；per-goal gap-seed；无硬门禁。"""

from __future__ import annotations

from app.services.export_column_plan import (
    build_column_plan,
    build_query_graph,
    views_from_column_plan,
)
from app.services.mcp_resource_bind import (
    BindResult,
    ResourceBinding,
    resolve_export_resource_whitelist,
)
from app.services.metric_registry import GOLDEN_16_HEADERS, match_metric
from app.services.react_engine import (
    _export_resource_allowed,
    _soft_reconcile_export_column_todos,
    _parse_export_todos,
)


_EVERYDAY = "view_result_gameuser_betstat_everyday_bygame"
_BET_LOG = "view_result_user_bet_log"


def test_golden16_views_include_everyday_bygame():
    plan = build_column_plan(list(GOLDEN_16_HEADERS))
    views = views_from_column_plan(plan)
    assert _EVERYDAY in views
    # Top-game may still need archive bet log
    assert _BET_LOG in views or any(
        (c.get("agg_spec") or {}).get("view") == _BET_LOG for c in plan
    )
    by_h = {c["header"]: c for c in plan}
    for h in ("总下注金额(SC)", "总返奖金额(SC)", "下注次数"):
        src_views = {str(s.get("view") or "") for s in by_h[h].get("sources") or []}
        assert _EVERYDAY in src_views
        assert _EVERYDAY == str((by_h[h].get("agg_spec") or {}).get("view") or "")


def test_whitelist_does_not_seed_static_bet_resources():
    plan = build_column_plan(list(GOLDEN_16_HEADERS))
    wl = resolve_export_resource_whitelist(plan, list(GOLDEN_16_HEADERS), gap_seed=True)
    assert wl == []


def test_match_fanjiang_aliases():
    m = match_metric("总返浆金额")
    assert m is not None
    assert m.metric_id == "win_sum_sc"
    m2 = match_metric("返浆")
    assert m2 is not None
    assert m2.metric_id == "win_sum_sc"
    m3 = match_metric("总投注金额")
    assert m3 is not None
    assert m3.metric_id == "bet_sum_sc"


def test_whitelist_keeps_only_actual_llm_bindings():
    plan = build_column_plan(["用户ID", "总充值金额", "总下注金额(SC)", "总返奖金额(SC)"])
    bind = BindResult(
        bindings=[
            ResourceBinding(
                goal="总充值金额",
                tool="query",
                resource="view_result_pay_order_log",
                confidence=0.9,
                source="llm",
            ),
        ]
    )
    wl = resolve_export_resource_whitelist(
        plan,
        ["用户ID", "总充值金额", "总下注金额(SC)", "总返奖金额(SC)"],
        bind_result=bind,
        allow_seed=True,
        gap_seed=True,
    )
    assert wl == ["view_result_pay_order_log"]


def test_soft_reconcile_expands_shrunken_todos():
    brief = "\n".join(f"{i}. {h}" for i, h in enumerate(GOLDEN_16_HEADERS, 1))
    short = _parse_export_todos(
        "1. 用户ID\n2. 总充值金额\n3. 总提现金额\n4. 当前余额(SC)"
    )
    assert sum(1 for t in short if t.get("phase") == "analyze") == 4
    richer, nudge = _soft_reconcile_export_column_todos(brief, short)
    assert nudge
    assert sum(1 for t in richer if t.get("phase") == "analyze") == 16
