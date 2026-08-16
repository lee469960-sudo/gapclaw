"""full_fetch: short-page write gate, core retries, fact order lock."""

from app.services.react_engine import (
    _compute_full_fetch_budget,
    _fact_roles_needing_continue,
    _full_fetch_allow_force_write,
    _full_fetch_core_unavailable,
    _full_fetch_page_caps_exhausted,
    _full_fetch_short_pages_ready,
    _EXPORT_FULL_PAGE_ROWS,
)


def test_full_fetch_short_pages_ready_requires_user_and_facts():
    assert not _full_fetch_short_pages_ready(
        target_roles=["user", "pay", "cash"],
        user_fetch_complete=False,
        fact_truncated_roles=[],
        missing_roles=[],
    )
    assert not _full_fetch_short_pages_ready(
        target_roles=["user", "pay"],
        user_fetch_complete=True,
        fact_truncated_roles=["pay"],
        missing_roles=[],
    )
    assert not _full_fetch_short_pages_ready(
        target_roles=["user", "pay"],
        user_fetch_complete=True,
        fact_truncated_roles=[],
        missing_roles=["pay"],
    )
    assert _full_fetch_short_pages_ready(
        target_roles=["user", "pay", "bet"],
        user_fetch_complete=True,
        fact_truncated_roles=[],
        missing_roles=[],
        abandoned_roles={"bet"},
    )


def test_full_fetch_refuse_force_write_until_short_or_caps():
    assert not _full_fetch_allow_force_write(
        full_fetch=True,
        prefer_fallback=True,
        short_pages_ready=False,
        core_unavailable=False,
        budget_exhausted=True,
        page_caps_exhausted=False,
    )
    assert _full_fetch_allow_force_write(
        full_fetch=True,
        prefer_fallback=True,
        short_pages_ready=False,
        core_unavailable=False,
        budget_exhausted=True,
        page_caps_exhausted=True,
    )
    assert _full_fetch_allow_force_write(
        full_fetch=True,
        prefer_fallback=False,
        short_pages_ready=True,
        core_unavailable=False,
        budget_exhausted=False,
        page_caps_exhausted=False,
    )
    assert not _full_fetch_allow_force_write(
        full_fetch=True,
        prefer_fallback=True,
        short_pages_ready=False,
        core_unavailable=True,
        budget_exhausted=True,
        page_caps_exhausted=True,
    )
    # Non-full_fetch keeps legacy force-write path
    assert _full_fetch_allow_force_write(
        full_fetch=False,
        prefer_fallback=True,
        short_pages_ready=False,
        core_unavailable=True,
        budget_exhausted=False,
        page_caps_exhausted=False,
    )


def test_full_fetch_core_unavailable_user_or_pay():
    assert _full_fetch_core_unavailable(
        target_roles=["user", "pay"],
        abandoned_roles={"user"},
        pay_cohort=True,
    )
    assert _full_fetch_core_unavailable(
        target_roles=["user", "pay"],
        abandoned_roles={"pay"},
        pay_cohort=True,
    )
    assert not _full_fetch_core_unavailable(
        target_roles=["user", "pay"],
        abandoned_roles={"bet"},
        pay_cohort=True,
    )


def test_fact_continue_blocks_all_facts_until_user():
    need = _fact_roles_needing_continue(
        ["user", "pay", "cash", "bet"],
        {
            "view_result_pay_order_log": 2,
            "view_result_cash_order_log": 2,
            "view_result_gameuser_betstat_everyday_bygame": 1,
        },
        {
            "view_result_pay_order_log": _EXPORT_FULL_PAGE_ROWS,
            "view_result_cash_order_log": _EXPORT_FULL_PAGE_ROWS,
            "view_result_gameuser_betstat_everyday_bygame": _EXPORT_FULL_PAGE_ROWS,
        },
        budget_left=20,
        max_fact_pages=20,
        pay_cohort=True,
        full_fetch=True,
    )
    # Pay cohort: pay may continue before user; cash/bet stay blocked
    assert need == ["pay"]
    need2 = _fact_roles_needing_continue(
        ["user", "pay", "cash"],
        {
            "view_result_user_info": 1,
            "view_result_pay_order_log": 2,
            "view_result_cash_order_log": 2,
        },
        {
            "view_result_pay_order_log": _EXPORT_FULL_PAGE_ROWS,
            "view_result_cash_order_log": _EXPORT_FULL_PAGE_ROWS,
        },
        budget_left=20,
        max_fact_pages=20,
        pay_cohort=True,
        full_fetch=True,
    )
    assert "pay" in need2
    assert "cash" in need2


def test_full_fetch_budget_covers_user_plus_facts():
    bud = _compute_full_fetch_budget(
        type_b=True,
        pay_cohort=True,
        fact_page_cap=8,
        current=18,
        user_page_cap=6,
    )
    # dims*2 + user + 3*fact + 2 slack
    assert bud >= 2 + 6 + 3 * 8 + 2
    assert bud <= 45


def test_page_caps_exhausted_when_truncated_at_cap():
    assert not _full_fetch_page_caps_exhausted(
        target_roles=["user", "pay"],
        fetched_view_pages={},
        fact_truncated_roles=[],
        abandoned_roles=set(),
        user_fetch_complete=False,
        user_page_cap=6,
        fact_page_cap=8,
    )
    assert _full_fetch_page_caps_exhausted(
        target_roles=["user", "pay"],
        fetched_view_pages={
            "view_result_user_info": 6,
            "view_result_pay_order_log": 8,
        },
        fact_truncated_roles=["pay"],
        abandoned_roles=set(),
        user_fetch_complete=False,
        user_page_cap=6,
        fact_page_cap=8,
    )

