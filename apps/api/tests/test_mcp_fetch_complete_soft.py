"""MCP once-pull-full soft optimizations: threshold, OFFSET stride, idle defer."""

from __future__ import annotations

from app.services.react_engine import (
    _EXPORT_FULL_PAGE_ROWS,
    _EXPORT_PAGE_LIMIT,
    _EXPORT_USER_PAGE_LIMIT,
    _auto_offset_for_node_page,
    _compute_fact_page_cap,
    _compute_full_fetch_budget,
    _compute_user_page_cap,
    _export_roles_needing_page_continue,
    _fact_roles_needing_continue,
    _full_page_row_threshold,
    _is_full_page_rows,
    _offset_for_pages_done,
    _page_limit_for_view,
    _should_auto_offset_continue,
    _sql_with_offset,
)


def test_full_page_threshold_follows_actual_limit():
    assert _full_page_row_threshold(1000) == 900
    assert _full_page_row_threshold(2000) == 1800
    assert _full_page_row_threshold(_EXPORT_USER_PAGE_LIMIT) == 1800
    # Hot-path constant remains the default for limit=1000
    assert _EXPORT_FULL_PAGE_ROWS == _full_page_row_threshold(_EXPORT_PAGE_LIMIT)


def test_user_1500_rows_is_short_not_full():
    """Previously absolute 900 mis-classified user pages of 1500 as full."""
    view = "view_result_user_info"
    assert _page_limit_for_view(view) == _EXPORT_USER_PAGE_LIMIT
    assert not _is_full_page_rows(1500, view=view)
    assert _is_full_page_rows(1800, view=view)
    assert _is_full_page_rows(2000, view=view)
    # Facts still use 900
    assert _is_full_page_rows(900, view="view_result_pay_order_log")
    assert not _is_full_page_rows(899, view="view_result_pay_order_log")


def test_offset_stride_matches_page_limit():
    assert _offset_for_pages_done(1, limit=1000) == 1000
    assert _offset_for_pages_done(2, limit=2000) == 4000
    assert _offset_for_pages_done(1, view="view_result_user_info") == 2000
    assert _offset_for_pages_done(1, view="view_result_pay_order_log") == 1000
    assert _auto_offset_for_node_page(0, limit=2000) == 2000
    sql = _sql_with_offset(
        "SELECT * FROM ads.view_result_user_info WHERE register_time >= 1",
        _offset_for_pages_done(1, limit=2000),
        limit=2000,
    )
    assert "LIMIT 2000" in sql
    assert "OFFSET 2000" in sql


def test_should_auto_offset_uses_page_limit_threshold():
    # 1500 rows with user limit=2000 → short → stop
    assert not _should_auto_offset_continue(
        phase="fetch",
        last_page_rows=1500,
        pages_done=1,
        page_cap=12,
        budget_left=5,
        auto_done=0,
        page_limit=2000,
        view="view_result_user_info",
    )
    # 1800 with limit=2000 → full → continue
    assert _should_auto_offset_continue(
        phase="fetch",
        last_page_rows=1800,
        pages_done=1,
        page_cap=12,
        budget_left=5,
        auto_done=0,
        page_limit=2000,
        view="view_result_user_info",
    )


def test_roles_needing_continue_includes_user_when_full():
    roles = ["user", "pay"]
    pages = {
        "view_result_user_info": 1,
        "view_result_pay_order_log": 1,
    }
    last = {
        "view_result_user_info": 2000,
        "view_result_pay_order_log": 1000,
    }
    need = _export_roles_needing_page_continue(
        roles,
        pages,
        last,
        budget_left=5,
        fact_page_cap=8,
        user_page_cap=12,
    )
    assert "user" in need
    assert "pay" in need
    # budget < 2 → no continue (reserve slot)
    assert (
        _export_roles_needing_page_continue(
            roles, pages, last, budget_left=1, fact_page_cap=8, user_page_cap=12,
        )
        == []
    )


def test_idle_defer_condition_via_roles_helper():
    """Still-full + budget maps to needing continue (idle must not jump analyze)."""
    pages = {"view_result_pay_order_log": 2}
    last = {"view_result_pay_order_log": 950}
    need = _fact_roles_needing_continue(
        ["pay"], pages, last, budget_left=4, max_fact_pages=8,
    )
    assert need == ["pay"]
    # short page → idle may proceed
    last_short = {"view_result_pay_order_log": 400}
    assert (
        _fact_roles_needing_continue(
            ["pay"], pages, last_short, budget_left=4, max_fact_pages=8,
        )
        == []
    )


def test_soft_caps_and_budget_headroom_from_cohort():
    fact = _compute_fact_page_cap(pay_cohort=False, cohort_uid_estimate=3748)
    user = _compute_user_page_cap(3748)
    assert fact >= 8
    assert user >= 6
    bud = _compute_full_fetch_budget(
        type_b=True,
        pay_cohort=False,
        fact_page_cap=fact,
        user_page_cap=user,
        current=18,
    )
    # user + 3*fact + dims + COUNT + auto-OFFSET headroom
    assert bud >= user + 3 * fact + 2
    assert bud <= 45


def test_no_hard_gate_copy_in_soft_fetch_helpers():
    """Regression: soft fetch helpers must not introduce hard FINAL/write bans."""
    import inspect
    from app.services import react_engine as re

    for name in (
        "_export_roles_needing_page_continue",
        "_is_full_page_rows",
        "_offset_for_pages_done",
        "_page_limit_for_view",
        "_compute_full_fetch_budget",
    ):
        src = inspect.getsource(getattr(re, name))
        low = src.lower()
        assert "禁止写表" not in src
        assert "禁止 final" not in low
        assert "禁止final" not in low.replace(" ", "")
