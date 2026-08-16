"""Export full-fetch soft diagnosis: intent fallback, page limit, fake short page."""

from __future__ import annotations

import inspect

from app.services.intent_router import (
    TurnIntent,
    fallback_turn_intent,
    soft_upgrade_export_intent,
    task_policy_from_intent,
)
from app.services.react_engine import (
    _EXPORT_PAGE_LIMIT,
    _EXPORT_USER_PAGE_LIMIT,
    _is_short_page_complete,
    _looks_like_remote_tiny_page_cap,
    _normalize_ads_mcp_args,
    _should_auto_offset_continue,
)
from app.services.task_policy import extract_numbered_column_headers


_EXPORT_BRIEF = (
    "导出美国时间8月1日至今充值用户分析 xlsx，列含：\n"
    "1.用户ID\n"
    "2.总充值金额\n"
    "3.总下注金额\n"
    "4.总返奖金额\n"
    "5.当前余额\n"
    "6.充值次数\n"
    "7.流水倍数\n"
    "8.是否封禁\n"
)


def test_fallback_export_shaped_to_export_report():
    turn = fallback_turn_intent(_EXPORT_BRIEF)
    assert turn.intent == "export_report"
    assert turn.wants_deliverable is True
    assert task_policy_from_intent(turn).export_like is True
    assert len(turn.metrics) >= 4
    assert any("用户ID" in m for m in turn.metrics)
    assert "禁止写表" not in (turn.reason or "")
    assert "禁止 FINAL" not in (turn.reason or "")


def test_fallback_still_data_query_for_single_metric():
    turn = fallback_turn_intent("获取一下8月7日的新增注册人数")
    assert turn.intent == "data_query"
    assert task_policy_from_intent(turn).export_like is False


def test_soft_upgrade_data_query_to_export():
    base = TurnIntent(
        intent="data_query",
        reason="llm_missed",
        wants_deliverable=False,
        query_goal="查数",
        metrics=[],
        source="llm",
    )
    up = soft_upgrade_export_intent(base, _EXPORT_BRIEF)
    assert up.intent == "export_report"
    assert up.wants_deliverable is True
    assert len(up.metrics) >= 4


def test_extract_numbered_column_headers():
    cols = extract_numbered_column_headers(_EXPORT_BRIEF)
    assert "用户ID" in cols[0]
    assert len(cols) >= 8


def test_soft_export_page_limit_any_export_like_phase():
    """Normalize flag works without requiring fetch phase (caller passes export_like)."""
    raw = {"view": "view_result_pay_order_log", "sql": "SELECT * FROM ads.x"}
    aligned = _normalize_ads_mcp_args(
        "query_ads_view", raw, soft_export_page_limit=True,
    )
    assert aligned["limit"] == _EXPORT_PAGE_LIMIT
    tiny = _normalize_ads_mcp_args(
        "query_ads_view",
        {**raw, "limit": 30},
        soft_export_page_limit=True,
    )
    assert tiny["limit"] == _EXPORT_PAGE_LIMIT
    user = _normalize_ads_mcp_args(
        "query_ads_view",
        {"view": "view_result_user_info", "sql": "SELECT * FROM ads.u", "limit": 30},
        soft_export_page_limit=True,
    )
    assert user["limit"] == _EXPORT_USER_PAGE_LIMIT


def test_fake_short_page_30_under_large_limit():
    assert _looks_like_remote_tiny_page_cap(30, requested_limit=1000) is True
    assert _looks_like_remote_tiny_page_cap(30, requested_limit=2000) is True
    assert _looks_like_remote_tiny_page_cap(30, requested_limit=30) is False
    assert _looks_like_remote_tiny_page_cap(400, requested_limit=1000) is False
    assert _is_short_page_complete(30, limit=1000) is False
    assert _is_short_page_complete(400, limit=1000) is True
    assert _is_short_page_complete(950, limit=1000) is False
    # Do not auto-OFFSET on fake tiny (wrong stride)
    assert not _should_auto_offset_continue(
        phase="fetch",
        last_page_rows=30,
        pages_done=1,
        page_cap=12,
        budget_left=10,
        auto_done=0,
        page_limit=1000,
        view="view_result_pay_order_log",
    )


def test_no_hard_gate_copy_in_new_helpers():
    from app.services import intent_router as ir
    from app.services import react_engine as re

    for fn in (
        ir.fallback_turn_intent,
        ir.soft_upgrade_export_intent,
        re._looks_like_remote_tiny_page_cap,
        re._is_short_page_complete,
    ):
        src = inspect.getsource(fn)
        assert "禁止写表" not in src
        assert "禁止 FINAL" not in src
