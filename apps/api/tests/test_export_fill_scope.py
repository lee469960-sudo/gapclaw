"""LLM column-fill scope: only named columns; no regex column scrape."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services.export_column_plan import (
    apply_binding_hints_to_column_plan,
    build_column_plan,
    roles_from_column_plan,
)
from app.services.export_fill_scope import (
    ExportFillScope,
    align_focus_to_headers,
    build_column_fill_repair_plan,
    merge_fill_headers,
    parse_fill_scope_payload,
    prior_headers_from_state,
    resolve_export_fill_scope,
)
from app.services.export_repair_plan import limit_query_nodes_for_repair


PRIOR_HEADERS = [
    "注册时间",
    "用户ID",
    "注册渠道",
    "总充值金额",
    "总提现金额",
    "总下注金额",
    "充值银行卡数量",
    "提现银行卡数量",
]


def test_align_focus_to_headers_near_match():
    out = align_focus_to_headers(
        ["总下注", "充值银行卡数量"],
        PRIOR_HEADERS,
    )
    assert "总下注金额" in out
    assert "充值银行卡数量" in out
    assert "总提现金额" not in out


def test_merge_fill_headers_adds_identity():
    cols = merge_fill_headers(
        ["总下注金额", "充值银行卡数量"],
        PRIOR_HEADERS,
        include_identity=True,
    )
    assert cols[0] == "用户ID" or "用户ID" in cols
    assert "总下注金额" in cols
    assert "总提现金额" not in cols


def test_parse_full_reexport_not_scoped():
    scope = parse_fill_scope_payload(
        {
            "is_column_fill": False,
            "focus_columns": [],
            "include_identity": True,
            "reason": "整表重导",
        },
        prior_headers=PRIOR_HEADERS,
    )
    assert scope.is_column_fill is False
    assert scope.focus_columns == []


def test_parse_fill_drops_unaligned_invention():
    scope = parse_fill_scope_payload(
        {
            "is_column_fill": True,
            "focus_columns": ["完全不存在的列XYZ"],
            "include_identity": True,
            "reason": "幻觉",
        },
        prior_headers=PRIOR_HEADERS,
    )
    assert scope.is_column_fill is False


def test_column_fill_roles_exclude_unrelated_cash():
    headers = merge_fill_headers(
        ["总下注金额", "充值银行卡数量"],
        PRIOR_HEADERS,
        include_identity=True,
    )
    plan = apply_binding_hints_to_column_plan(
        build_column_plan(headers),
        bindings=[
            {"goal": "总下注金额", "resource": "live_bet_resource"},
            {"goal": "充值银行卡数量", "resource": "live_payment_resource"},
            {"goal": "用户ID", "resource": "live_identity_resource"},
        ],
    )
    roles = roles_from_column_plan(plan)
    assert "bet" in roles or "pay" in roles
    assert "cash" not in roles  # 提现列未点名


def test_build_fill_repair_plan_scopes_query_nodes():
    headers = merge_fill_headers(
        ["总下注金额", "充值银行卡数量"],
        PRIOR_HEADERS,
        include_identity=True,
    )
    plan = apply_binding_hints_to_column_plan(
        build_column_plan(headers),
        bindings=[
            {"goal": "总下注金额", "resource": "live_bet_resource"},
            {"goal": "充值银行卡数量", "resource": "live_payment_resource"},
            {"goal": "用户ID", "resource": "live_identity_resource"},
        ],
    )
    repair = build_column_fill_repair_plan(
        focus_columns=headers,
        column_plan=plan,
        time_window={"start_ms": 1, "end_ms": 2, "label": "t"},
    )
    assert repair.get("status") == "repairable"
    actions = repair.get("actions") or []
    assert actions
    keys = {
        k
        for a in actions
        if isinstance(a, dict)
        for k in (a.get("node_keys") or [])
        if k
    }
    assert keys
    from app.services.export_column_plan import build_query_graph

    nodes = build_query_graph(plan, {"start_ms": 1, "end_ms": 2, "label": "t"})
    scoped, ok = limit_query_nodes_for_repair(nodes, repair)
    assert ok is True
    assert len(scoped) <= len(nodes)
    assert all(str(n.get("key") or "") in keys for n in scoped)


def test_prior_headers_from_state():
    st = {
        "analyze_columns": ["用户ID", "总充值金额"],
        "column_plan": [{"header": "总下注金额"}],
    }
    hs = prior_headers_from_state(st)
    assert "用户ID" in hs
    assert "总充值金额" in hs
    assert "总下注金额" in hs


def test_resolve_export_fill_scope_timeout_no_raise():
    async def _boom(*_a, **_k):
        raise TimeoutError("slow")

    async def _run():
        with patch(
            "app.services.llm_client.chat_completion",
            new=_boom,
        ):
            return await resolve_export_fill_scope(
                SimpleNamespace(id="llm"),
                user_message="只补总下注和充值卡",
                prior_headers=PRIOR_HEADERS,
                timeout=1,
            )

    scope = asyncio.run(_run())
    assert isinstance(scope, ExportFillScope)
    assert scope.is_column_fill is False
    assert scope.source == "fallback"


def test_resolve_export_fill_scope_parses_llm():
    async def _ok(*_a, **_k):
        return (
            '{"is_column_fill":true,"focus_columns":["总下注金额","充值银行卡数量"],'
            '"include_identity":true,"reason":"用户点名"}'
        )

    async def _run():
        with patch(
            "app.services.llm_client.chat_completion",
            new=AsyncMock(side_effect=_ok),
        ):
            return await resolve_export_fill_scope(
                SimpleNamespace(id="llm"),
                user_message="主要补齐总下注和充值卡",
                prior_headers=PRIOR_HEADERS,
            )

    scope = asyncio.run(_run())
    assert scope.is_column_fill is True
    assert "总下注金额" in scope.focus_columns
    assert "充值银行卡数量" in scope.focus_columns
    assert "总提现金额" not in scope.focus_columns
