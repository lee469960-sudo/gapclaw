"""Export fetch: soft-align query_ads_view page limits; correct cohort page math."""

from __future__ import annotations

import inspect

from app.services.react_engine import (
    _EXPORT_PAGE_LIMIT,
    _EXPORT_QUERY_BUDGET_FULL_FETCH_MAX,
    _EXPORT_USER_PAGE_LIMIT,
    _format_export_task_anchor,
    _needed_pages_for_estimate,
    _normalize_ads_mcp_args,
    _reply_looks_like_underfetch_myth,
    _soft_align_export_query_limit,
    _soft_rebut_underfetch_myth,
    _EXPORT_PLAN_HINT,
)


def test_soft_align_fills_missing_limit_by_view():
    pay, note = _soft_align_export_query_limit(
        {"view": "view_result_pay_order_log", "sql": "SELECT * FROM ads.view_result_pay_order_log"}
    )
    assert pay["limit"] == _EXPORT_PAGE_LIMIT
    assert note and "1000" in note

    user, note_u = _soft_align_export_query_limit(
        {"view": "view_result_user_info", "sql": "SELECT * FROM ads.view_result_user_info"}
    )
    assert user["limit"] == _EXPORT_USER_PAGE_LIMIT
    assert note_u and "2000" in note_u


def test_soft_align_raises_tiny_limit_30():
    out, note = _soft_align_export_query_limit(
        {
            "view": "view_result_pay_order_log",
            "sql": "SELECT * FROM ads.view_result_pay_order_log WHERE create_time>=1",
            "limit": 30,
        }
    )
    assert out["limit"] == _EXPORT_PAGE_LIMIT
    assert note is not None

    user, _ = _soft_align_export_query_limit(
        {
            "view": "view_result_user_info",
            "sql": "SELECT * FROM ads.view_result_user_info WHERE register_time>=1",
            "limit": 30,
        }
    )
    assert user["limit"] == _EXPORT_USER_PAGE_LIMIT


def test_soft_align_skips_count_and_probe():
    cnt, note = _soft_align_export_query_limit(
        {
            "view": "view_result_pay_order_log",
            "sql": "SELECT count(DISTINCT uid) AS cnt FROM ads.view_result_pay_order_log",
        }
    )
    assert "limit" not in cnt or cnt.get("limit") is None
    assert note is None

    probe, note_p = _soft_align_export_query_limit(
        {
            "view": "view_result_pay_order_log",
            "sql": "SELECT uid FROM ads.view_result_pay_order_log LIMIT 1",
            "limit": 1,
        }
    )
    assert probe.get("limit") == 1
    assert note_p is None


def test_normalize_applies_soft_export_page_limit_flag():
    raw = {"view": "view_result_user_info", "sql": "SELECT * FROM ads.view_result_user_info"}
    plain = _normalize_ads_mcp_args("query_ads_view", raw, soft_export_page_limit=False)
    assert plain.get("limit") is None
    aligned = _normalize_ads_mcp_args("query_ads_view", raw, soft_export_page_limit=True)
    assert aligned.get("limit") == _EXPORT_USER_PAGE_LIMIT


def test_cohort_4268_page_estimate_not_143():
    # user limit 2000 → ceil(4268/2000)+2 = 3+2 = 5  (not 143)
    user_pages = _needed_pages_for_estimate(4268, page_limit=_EXPORT_USER_PAGE_LIMIT)
    fact_pages = _needed_pages_for_estimate(4268, page_limit=_EXPORT_PAGE_LIMIT)
    assert user_pages == (4268 + 2000 - 1) // 2000 + 2
    assert user_pages <= 6
    assert fact_pages == (4268 + 1000 - 1) // 1000 + 2
    assert fact_pages <= 8
    assert user_pages != 143
    assert fact_pages != 143


def test_underfetch_myth_detection_and_rebuttal():
    myth = (
        "已知限制：单次 query 返回 30 行；单次对话 8 次工具预算；"
        "全量需约 326 轮，建议改用宿主机 dbt 异步导出"
    )
    assert _reply_looks_like_underfetch_myth(myth) is True
    assert _reply_looks_like_underfetch_myth("继续 MCP OFFSET 续翻至短页") is False
    rebut = _soft_rebut_underfetch_myth()
    assert "1000" in rebut and "2000" in rebut
    assert str(_EXPORT_QUERY_BUDGET_FULL_FETCH_MAX) in rebut
    assert "禁止写表" not in rebut
    assert "禁止 FINAL" not in rebut


def test_plan_hint_and_anchor_correct_page_copy():
    assert "N≤14" not in _EXPORT_PLAN_HINT or "45" in _EXPORT_PLAN_HINT
    assert "1000" in _EXPORT_PLAN_HINT and "2000" in _EXPORT_PLAN_HINT
    assert "30 行" in _EXPORT_PLAN_HINT  # as negative guidance
    anchor = _format_export_task_anchor(
        source_brief="导出充值用户",
        time_window={"label": "最近30天", "start_ms": 1, "end_ms": 2},
        column_headers=["用户ID"],
        target_roles=["user", "pay"],
    )
    assert "1000" in anchor and "2000" in anchor
    assert "禁止写表" not in anchor
    assert "禁止 FINAL" not in anchor


def test_soft_align_helpers_no_hard_gate_copy():
    for fn in (
        _soft_align_export_query_limit,
        _soft_rebut_underfetch_myth,
        _reply_looks_like_underfetch_myth,
    ):
        src = inspect.getsource(fn)
        assert "禁止写表" not in src
        assert "禁止 FINAL" not in src
