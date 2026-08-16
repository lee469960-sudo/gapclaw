"""MCP auto OFFSET continuation + dynamic user page cap."""

from app.services.react_engine import (
    _EXPORT_AUTO_OFFSET_MAX,
    _EXPORT_FULL_PAGE_ROWS,
    _EXPORT_MAX_PAGES_USER,
    _EXPORT_MAX_PAGES_USER_MAX,
    _EXPORT_PAGE_LIMIT,
    _auto_offset_for_node_page,
    _compute_full_fetch_budget,
    _compute_user_page_cap,
    _export_roles_ready_for_analyze,
    _max_pages_for_view,
    _should_auto_offset_continue,
    _sql_base_for_pagination,
    _sql_with_offset,
)


def test_user_page_cap_from_cohort_3024():
    cap = _compute_user_page_cap(3024)
    # user_info uid-batch uses its own wider query limit; cap remains floored to 6.
    assert cap >= 3
    assert cap == _EXPORT_MAX_PAGES_USER
    assert _EXPORT_MAX_PAGES_USER <= cap <= _EXPORT_MAX_PAGES_USER_MAX


def test_user_page_cap_default_without_estimate():
    assert _compute_user_page_cap(None) == _EXPORT_MAX_PAGES_USER
    assert _compute_user_page_cap(0) == _EXPORT_MAX_PAGES_USER


def test_user_page_cap_large_cohort_clamped():
    # ceil(120000/2000)+2 → clamp to MAX 12
    assert _compute_user_page_cap(120_000) == _EXPORT_MAX_PAGES_USER_MAX


def test_max_pages_for_view_uses_user_cap():
    v = "view_result_user_info"
    assert _max_pages_for_view(v, user_page_cap=9) == 9
    assert _max_pages_for_view(v) == _EXPORT_MAX_PAGES_USER


def test_full_fetch_budget_uses_user_cap():
    b_default = _compute_full_fetch_budget(
        type_b=True, pay_cohort=False, fact_page_cap=5, user_page_cap=6,
    )
    b_deep = _compute_full_fetch_budget(
        type_b=True, pay_cohort=False, fact_page_cap=5, user_page_cap=12,
    )
    assert b_deep >= b_default


def test_should_auto_offset_continue_full_page():
    assert _should_auto_offset_continue(
        phase="fetch",
        last_page_rows=_EXPORT_PAGE_LIMIT,
        pages_done=1,
        page_cap=6,
        budget_left=5,
        auto_done=0,
    )
    # short page → stop
    assert not _should_auto_offset_continue(
        phase="fetch",
        last_page_rows=400,
        pages_done=2,
        page_cap=6,
        budget_left=5,
        auto_done=0,
    )
    # at cap → stop
    assert not _should_auto_offset_continue(
        phase="fetch",
        last_page_rows=_EXPORT_PAGE_LIMIT,
        pages_done=6,
        page_cap=6,
        budget_left=5,
        auto_done=0,
    )
    # auto max → stop
    assert not _should_auto_offset_continue(
        phase="fetch",
        last_page_rows=_EXPORT_PAGE_LIMIT,
        pages_done=1,
        page_cap=6,
        budget_left=5,
        auto_done=_EXPORT_AUTO_OFFSET_MAX,
    )
    # no budget → stop
    assert not _should_auto_offset_continue(
        phase="fetch",
        last_page_rows=_EXPORT_PAGE_LIMIT,
        pages_done=1,
        page_cap=6,
        budget_left=0,
        auto_done=0,
    )
    # budget_left < 2 → stop (reserve last slot)
    assert not _should_auto_offset_continue(
        phase="fetch",
        last_page_rows=_EXPORT_PAGE_LIMIT,
        pages_done=1,
        page_cap=6,
        budget_left=1,
        auto_done=0,
    )
    # budget_left >= 2 → may continue
    assert _should_auto_offset_continue(
        phase="fetch",
        last_page_rows=_EXPORT_PAGE_LIMIT,
        pages_done=1,
        page_cap=6,
        budget_left=2,
        auto_done=0,
    )
    # not fetch → stop
    assert not _should_auto_offset_continue(
        phase="analyze",
        last_page_rows=_EXPORT_PAGE_LIMIT,
        pages_done=1,
        page_cap=6,
        budget_left=5,
        auto_done=0,
    )


def test_auto_offset_is_per_query_node():
    assert _auto_offset_for_node_page(0) == _EXPORT_PAGE_LIMIT
    assert _auto_offset_for_node_page(1) == 2 * _EXPORT_PAGE_LIMIT


def test_sql_with_offset_and_base():
    base = "SELECT * FROM ads.view_result_user_info WHERE register_time >= 1"
    sql = _sql_with_offset(base, _EXPORT_PAGE_LIMIT)
    assert f"OFFSET {_EXPORT_PAGE_LIMIT}" in sql
    assert f"LIMIT {_EXPORT_PAGE_LIMIT}" in sql
    from_current = _sql_base_for_pagination(
        base + f" LIMIT {_EXPORT_PAGE_LIMIT} OFFSET 0",
        "user",
        {"start_ms": 1, "end_ms": 2},
    )
    assert "OFFSET" not in from_current.upper()

    agg = (
        "SELECT uid, sum(price) / 100 AS pay_sum "
        "FROM ads.view_result_pay_order_log WHERE finish_time >= 1 GROUP BY uid"
    )
    agg_page = _sql_with_offset(agg, _EXPORT_PAGE_LIMIT)
    assert "GROUP BY uid ORDER BY uid" in agg_page
    assert "LIMIT" not in from_current.upper()
    assert "register_time" in from_current


def test_roles_ready_respects_dynamic_user_cap():
    # 6 pages with default cap → ready even without short page
    assert _export_roles_ready_for_analyze(
        missing_roles=[],
        has_data=True,
        user_fetch_complete=False,
        user_pages=6,
        max_user_pages=6,
    )
    # 6 pages but dynamic cap=9 → not ready
    assert not _export_roles_ready_for_analyze(
        missing_roles=[],
        has_data=True,
        user_fetch_complete=False,
        user_pages=6,
        max_user_pages=9,
    )
    # at dynamic cap → ready
    assert _export_roles_ready_for_analyze(
        missing_roles=[],
        has_data=True,
        user_fetch_complete=False,
        user_pages=9,
        max_user_pages=9,
    )


def test_full_page_threshold():
    assert _EXPORT_FULL_PAGE_ROWS == 900
    assert _EXPORT_PAGE_LIMIT == 1000
    assert _EXPORT_AUTO_OFFSET_MAX == 10
    assert _EXPORT_FULL_PAGE_ROWS == int(_EXPORT_PAGE_LIMIT * 0.9)
