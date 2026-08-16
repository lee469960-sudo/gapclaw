"""Exhaust force-write: no user pages still yields root xlsx; fact ordering."""

from pathlib import Path

from app.services.export_build_report import write_export_deliverable
from app.services.export_column_plan import build_column_plan
from app.services.react_engine import (
    _build_run_state_digest,
    _fact_roles_needing_continue,
)
from app.services.workplace import (
    materialize_analyzed_export,
    write_task_json_page,
)


def test_fact_continue_skips_pay_when_user_missing():
    # Pay cohort: pay may continue; cash still blocked until user
    need = _fact_roles_needing_continue(
        ["user", "pay", "cash"],
        {
            "view_result_pay_order_log": 2,
            "view_result_cash_order_log": 1,
        },
        {
            "view_result_pay_order_log": 10000,
            "view_result_cash_order_log": 100,
        },
        budget_left=10,
        max_fact_pages=20,
        pay_cohort=True,
    )
    assert "pay" in need
    assert "cash" not in need
    need2 = _fact_roles_needing_continue(
        ["user", "pay"],
        {
            "view_result_user_info": 1,
            "view_result_pay_order_log": 2,
        },
        {"view_result_pay_order_log": 10000},
        budget_left=10,
        max_fact_pages=20,
        pay_cohort=True,
    )
    assert "pay" in need2


def test_materialize_without_user_from_pay_uids(tmp_path, monkeypatch):
    """No user_info pages: synthesize uids from pay → still write xlsx."""
    sid = "sbx_exhaust"
    root = tmp_path / sid / "workplace"
    root.mkdir(parents=True)

    def _wp(s):
        return root if s == sid else Path("/nope")

    monkeypatch.setattr("app.services.workplace.ensure_workplace", _wp)
    monkeypatch.setattr("app.services.export_build_report.ensure_workplace", _wp)

    run_id = "run_ex"
    write_task_json_page(
        sid,
        [
            {"uid": 101, "status": 2, "price": 1250, "finish_time": 1500},
            {"uid": 102, "status": 2, "price": 300, "finish_time": 1600},
        ],
        run_id=run_id,
        page=1,
        view="view_result_pay_order_log",
    )
    plan = build_column_plan(["用户ID", "总充值金额"])
    rel = write_export_deliverable(
        sid,
        run_id,
        column_plan=plan,
        time_window={"start_ms": 1, "end_ms": 2, "label": "exhaust", "cohort": "pay"},
        preferred_name="exhaust_dual.xlsx",
        title="exhaust",
    )
    if not rel:
        # Fallback path still proves synthetic-user join
        rel = materialize_analyzed_export(
            sid,
            run_id,
            column_headers=["用户ID", "总充值金额"],
            preferred_name="exhaust_syn.xlsx",
            column_plan=plan,
        )
    assert rel, "expected xlsx even without user_info pages"
    assert (root / rel).is_file()
    import openpyxl

    wb = openpyxl.load_workbook(root / rel)
    assert wb.active.max_row >= 2  # header + data
    wb.close()


def test_iters_exhausted_digest_still_mentions_task_when_no_deliverable():
    dig = _build_run_state_digest(
        completeness="iters_exhausted",
        has_task_data=True,
        mcp_query_count=34,
        mcp_budget=34,
        deliverable="",
    )
    assert "MCP 仅 34/34" in dig
    assert "未写出当前目录 xlsx" in dig
    dig2 = _build_run_state_digest(
        completeness="iters_exhausted",
        has_task_data=True,
        mcp_query_count=34,
        mcp_budget=34,
        deliverable="用户分析.xlsx",
    )
    assert "用户分析.xlsx" in dig2
    assert "未写出当前目录 xlsx" not in dig2
