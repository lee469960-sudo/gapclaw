"""user_info speed: pay-cohort defer + uid batches; register narrow pages."""

from pathlib import Path

from app.services.export_column_plan import (
    build_column_plan,
    build_query_graph,
    mcp_line_from_query_node,
    sql_from_agg_spec,
)
from app.services.react_engine import (
    _EXPORT_USER_PAGE_LIMIT,
    _EXPORT_USER_UID_BATCH,
    _full_page_row_threshold,
    _user_select_cols_from_plan,
    _uid_batch_sql,
)
from app.services.workplace import (
    collect_export_uids_from_task,
    write_task_json_page,
)


def test_pay_cohort_user_field_sql_empty_deferred():
    spec = {
        "role": "user",
        "view": "view_result_user_info",
        "mode": "field",
        "select": "uid, register_time, channel_id",
    }
    view, sql = sql_from_agg_spec(spec, {"start_ms": 1, "end_ms": 2, "cohort": "pay"})
    assert view == "view_result_user_info"
    assert sql == ""

    view2, sql2 = sql_from_agg_spec(
        spec, {"start_ms": 1, "end_ms": 2, "cohort": "register"},
    )
    assert "register_time >= 1" in sql2
    assert "WHERE" in sql2


def test_query_graph_pay_cohort_defers_user_no_full_table():
    plan = build_column_plan(["用户ID", "总充值金额", "注册渠道"])
    tw = {"start_ms": 1, "end_ms": 2, "cohort": "pay"}
    nodes = build_query_graph(plan, tw, page_limit=10000)
    user_nodes = [n for n in nodes if n.get("role") == "user"]
    assert user_nodes
    for n in user_nodes:
        assert n.get("deferred") is True
        assert n.get("defer_until") == "pay"
        assert not (n.get("sql") or "").strip()
        assert int(n.get("limit") or 0) <= _EXPORT_USER_PAGE_LIMIT
        assert mcp_line_from_query_node(n) == ""


def test_uid_batch_sql_uses_bound_identity_resource():
    assert _EXPORT_USER_UID_BATCH == 500
    uids = [str(i) for i in range(1, 6)]
    sql = _uid_batch_sql(
        uids,
        view="bound_identity_resource",
        select_cols="uid, channel_id, sc",
    )
    assert "bound_identity_resource" in sql
    assert "uid IN (1, 2, 3, 4, 5)" in sql
    assert "SELECT uid, channel_id, sc" in sql
    assert "SELECT *" not in sql
    # Cohort identity lookup is by uid, not a default registration window.
    assert "register_time >=" not in sql
    assert _full_page_row_threshold(_EXPORT_USER_PAGE_LIMIT) == 1800


def test_uid_batch_probe_and_fail_message():
    from app.services.react_engine import (
        _UID_BATCH_NARROW_COLS,
        _clip_mcp_error_text,
        _uid_batch_fail_message,
        _uid_batch_probe_sql,
    )

    probe = _uid_batch_probe_sql("42", view="bound_identity_resource")
    assert "uid IN (42)" in probe
    assert "LIMIT 1" in probe
    assert "register_time >=" not in probe
    assert _UID_BATCH_NARROW_COLS == "uid"

    msg = _uid_batch_fail_message(
        "view_result_user_info",
        mcp_error="MCP 错误: timeout after 30s " + ("x" * 500),
        detail="探活失败",
    )
    assert "pay.create_time" in msg
    assert "不用 register_time" in msg
    assert "MCP:" in msg
    assert "timeout" in msg
    clipped = _clip_mcp_error_text("a" * 1000, max_chars=50)
    assert len(clipped) <= 50
    assert clipped.endswith("…")


def test_collect_pay_uids_and_plan_cols(tmp_path, monkeypatch):
    sid = "sbx_uid"
    root = tmp_path / sid / "workplace"
    root.mkdir(parents=True)

    def _wp(s):
        return root if s == sid else Path("/nope")

    monkeypatch.setattr("app.services.workplace.ensure_workplace", _wp)

    write_task_json_page(
        sid,
        [
            {"uid": 10, "status": 2, "price": 100},
            {"uid": 20, "status": 2, "price": 200},
            {"uid": 10, "status": 2, "price": 50},
        ],
        run_id="run1",
        page=1,
        view="view_result_pay_order_log",
    )
    write_task_json_page(
        sid,
        [{"uid": 30, "status": 2, "price": 300}],
        run_id="run1",
        page=2,
        view="view_result_user_pay_order_log",
    )
    uids = collect_export_uids_from_task(sid, "run1")
    assert uids == ["10", "20", "30"]

    plan = build_column_plan(["用户ID", "注册时间", "当前余额(SC)"])
    cols = _user_select_cols_from_plan(plan)
    assert "uid" in cols
    assert "register_time" in cols or "sc" in cols
