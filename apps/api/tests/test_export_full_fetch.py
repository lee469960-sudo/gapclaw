"""MCP once-pull-full: dynamic caps, short-page gate, COUNT/OFFSET coach."""

from app.services.react_engine import (
    _compute_fact_page_cap,
    _compute_full_fetch_budget,
    _export_next_action_coach,
    _export_roles_ready_for_analyze,
    _fact_roles_needing_continue,
    _format_export_task_anchor,
    _format_offset_mcp_example,
    _is_count_sql,
    _needed_pages_for_estimate,
    _parse_count_from_mcp_rows,
    _parse_export_time_window,
    _sql_with_offset,
    _EXPORT_MAX_PAGES_PER_VIEW,
    _EXPORT_MAX_PAGES_PER_VIEW_PAY_COHORT,
    _EXPORT_FULL_PAGE_ROWS,
)


def test_needed_pages_for_estimate_3748():
    from app.services.react_engine import _EXPORT_PAGE_LIMIT

    n = _needed_pages_for_estimate(3748)
    # ceil(3748/1000)+2 = 4+2 = 6
    assert n == (3748 + _EXPORT_PAGE_LIMIT - 1) // _EXPORT_PAGE_LIMIT + 2
    assert n == 6
    assert _needed_pages_for_estimate(None) == 0
    assert _needed_pages_for_estimate(0) == 0


def test_dynamic_fact_page_cap_from_estimate():
    # With page=1000, needed(3748)=6 → pay stays at pay-cohort base (8)
    cap = _compute_fact_page_cap(pay_cohort=True, cohort_uid_estimate=3748)
    assert cap == _EXPORT_MAX_PAGES_PER_VIEW_PAY_COHORT
    # register: max(3, needed+2) = max(3, 8) = 8 (soft climb toward MAX)
    cap_reg = _compute_fact_page_cap(pay_cohort=False, cohort_uid_estimate=3748)
    assert cap_reg == 8
    # Large estimate raises above base
    cap_big = _compute_fact_page_cap(pay_cohort=False, cohort_uid_estimate=50_000)
    assert cap_big > _EXPORT_MAX_PAGES_PER_VIEW
    assert cap_big == 20
    # no estimate → base
    assert _compute_fact_page_cap(pay_cohort=True, cohort_uid_estimate=None) == (
        _EXPORT_MAX_PAGES_PER_VIEW_PAY_COHORT
    )


def test_full_fetch_budget_scales_with_cap():
    bud = _compute_full_fetch_budget(
        type_b=True, pay_cohort=True, fact_page_cap=10, current=18,
    )
    assert bud >= 18
    assert bud >= 3 * 10  # room for three fact roles
    assert bud <= 45


def test_parse_count_from_mcp_rows():
    assert _parse_count_from_mcp_rows([{"cnt": 3748}]) == 3748
    assert _parse_count_from_mcp_rows([{"count": "1200"}]) == 1200
    assert _parse_count_from_mcp_rows([{"uid": 1, "amount": 2}]) is None
    assert _is_count_sql("SELECT count(DISTINCT uid) AS cnt FROM ads.x")
    assert not _is_count_sql("SELECT * FROM ads.x")


def test_fact_need_continue_when_full_page_under_cap():
    roles = ["user", "pay", "cash", "bet"]
    pages = {"view_result_pay_order_log": 2}
    last = {"view_result_pay_order_log": _EXPORT_FULL_PAGE_ROWS + 10}
    need = _fact_roles_needing_continue(
        roles, pages, last, budget_left=5, max_fact_pages=8,
    )
    assert "pay" in need
    # at cap → no longer need continue (cap exhausted)
    pages2 = {"view_result_pay_order_log": 8}
    need2 = _fact_roles_needing_continue(
        roles, pages2, last, budget_left=5, max_fact_pages=8,
    )
    assert "pay" not in need2
    # budget 0 → empty
    assert not _fact_roles_needing_continue(
        roles, pages, last, budget_left=0, max_fact_pages=8,
    )


def test_roles_ready_blocks_when_fact_need_continue():
    assert not _export_roles_ready_for_analyze(
        missing_roles=[],
        has_data=True,
        user_fetch_complete=True,
        user_pages=2,
        fact_need_continue=["pay"],
    )
    assert _export_roles_ready_for_analyze(
        missing_roles=[],
        has_data=True,
        user_fetch_complete=True,
        user_pages=2,
        fact_need_continue=[],
    )


def test_offset_mcp_example_and_sql():
    from app.services.react_engine import _EXPORT_PAGE_LIMIT

    sql = "SELECT * FROM ads.view_result_pay_order_log WHERE create_time >= 1 AND create_time < 2"
    out = _format_offset_mcp_example("view_result_pay_order_log", sql, 2)
    assert f"OFFSET {2 * _EXPORT_PAGE_LIMIT}" in out
    assert f"LIMIT {_EXPORT_PAGE_LIMIT}" in out
    assert "FORMAT" not in out.upper()
    assert "view_result_pay_order_log" in out
    cleaned = _sql_with_offset(
        sql + f" LIMIT {_EXPORT_PAGE_LIMIT} OFFSET 0", _EXPORT_PAGE_LIMIT,
    )
    assert f"OFFSET {_EXPORT_PAGE_LIMIT}" in cleaned


def test_coach_includes_count_and_offset():
    tw = _parse_export_time_window(
        "导出美国时间2026-06-01至2026-08-01充值用户数据"
    )
    assert tw is not None
    # Early fetch: COUNT first
    coach0 = _export_next_action_coach(
        phase="fetch",
        fetched_view_pages={},
        target_roles=["user", "pay", "cash", "bet"],
        time_window=tw,
        budget_left=20,
        cohort_uid_estimate=None,
    )
    assert "COUNT" in coach0
    # Full page continue
    coach = _export_next_action_coach(
        phase="fetch",
        fetched_view_pages={"view_result_pay_order_log": 2},
        target_roles=["user", "pay", "cash", "bet"],
        covered_roles=["user", "pay", "cash", "bet"],
        missing_roles=[],
        time_window=tw,
        budget_left=10,
        user_fetch_complete=True,
        fact_need_continue=["pay"],
        cohort_uid_estimate=3748,
        fact_page_cap=8,
    )
    assert "一次拉全" in coach or "OFFSET" in coach
    assert "OFFSET" in coach
    assert "3748" in coach


def test_task_anchor_keeps_count_resource_agnostic():
    tw = _parse_export_time_window(
        "导出美国时间2026-06-01至2026-08-01充值用户分析表"
    )
    assert tw and tw.get("start_ms")
    anchor = _format_export_task_anchor(
        source_brief="充值用户导出",
        time_window=tw,
        column_headers=["用户ID", "总充值金额"],
        target_roles=["user", "pay"],
    )
    assert "时间窗" in anchor
    assert "view_result_pay_order_log" not in anchor
