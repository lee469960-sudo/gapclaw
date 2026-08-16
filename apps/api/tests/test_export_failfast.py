"""Export fail-fast: blacklist failed views, done-enough write without bet."""

from pathlib import Path

from app.services.export_build_report import write_export_deliverable
from app.services.export_column_plan import (
    build_column_plan,
    build_query_graph,
    query_graph_complete,
    query_graph_done_enough,
)
from app.services.react_engine import (
    _export_role_pull_blocked,
    _missing_export_roles,
    _noncore_abandon_progress,
    _query_graph_fail_message,
    _query_node_oneshot_modes,
    _record_export_view_failure,
    _should_import_prior_node_state,
    _strip_repair_plan_node_state,
    _summarize_run_page_state,
)
from app.services.workplace import write_task_json_page


def test_query_graph_fail_message_includes_mcp_clip():
    msg = _query_graph_fail_message(
        "view_result_user_bet_log",
        hint="同 view 无第二数据源，失败快停",
        mcp_error="timeout after 30s " + ("x" * 500),
    )
    assert "远程失败" in msg
    assert "view_result_user_bet_log" in msg
    assert "失败快停" in msg
    assert "MCP:" in msg
    assert "timeout" in msg
    assert len(msg) < 600

    exc_msg = _query_graph_fail_message(
        "view_result_gameuser_betstat_everyday_bygame",
        hint="将尝试 raw 兜底（最多1次）",
        exc="ConnectionResetError: peer closed",
    )
    assert "异常:" in exc_msg
    assert "ConnectionResetError" in exc_msg
    assert "raw 兜底" in exc_msg


def test_noncore_abandon_progress_copy():
    soft = _noncore_abandon_progress(
        mode="top_n", view="view_result_user_bet_log", soft=True,
    )
    assert "重节点" in soft
    assert "非核心" in soft
    assert "可继续写表" in soft
    assert "未完整" in soft

    hard = _noncore_abandon_progress(
        role="bet",
        view="view_result_gameuser_betstat_everyday_bygame",
        soft=False,
    )
    assert "放弃 bet/" in hard
    assert "可继续写表" in hard


def test_top_n_is_one_shot_without_source_substitution():
    """A failed top-N node stays local; the engine never substitutes a source."""
    assert "top_n" in _query_node_oneshot_modes()

    # Soft-fail path blacklists node key only — shared view stays usable for agg
    failed: set[str] = set()
    node_key = "top_n:view_result_user_bet_log:最多游戏"
    failed.add(f"node:{node_key}")
    assert "view_result_user_bet_log" not in failed
    assert not _export_role_pull_blocked(
        "bet",
        failed_views=failed,
        abandoned_roles=set(),
    )


def test_query_graph_done_enough_with_failed_bet():
    plan = build_column_plan(
        ["用户ID", "总充值金额", "总下注金额(SC)", "连续充值次数"]
    )
    tw = {"start_ms": 1, "end_ms": 2, "cohort": "pay"}
    nodes = build_query_graph(plan, tw)
    assert nodes
    pages = {
        "view_result_user_info": 1,
        "view_result_pay_order_log": 1,
    }
    # Strict complete still false while bet/heavy missing
    assert not query_graph_complete(nodes, pages)

    failed = {
        n["view"]
        for n in nodes
        if n.get("role") == "bet"
    }
    failed.update(
        f"node:{n['key']}"
        for n in nodes
        if n.get("segment") in ("sequence", "top_n") and n.get("key")
    )
    # Mark all non-core remaining as failed
    for n in nodes:
        v = n.get("view") or ""
        if int(pages.get(v, 0) or 0) < 1:
            failed.add(v)
    done = {n["key"] for n in nodes if n.get("role") == "pay" and n.get("mode") == "agg"}
    assert query_graph_done_enough(nodes, pages, failed, done_node_keys=done)


def test_query_graph_done_enough_requires_core_success():
    nodes = [
        {"view": "view_result_user_info", "role": "user", "segment": "identity"},
        {"view": "view_result_pay_order_log", "role": "pay", "segment": "fact_agg"},
        {
            "view": "view_result_gameuser_betstat_everyday_bygame",
            "role": "bet",
            "segment": "bet_daily",
        },
    ]
    failed = {
        "view_result_user_info",
        "view_result_pay_order_log",
        "view_result_gameuser_betstat_everyday_bygame",
        "view_result_user_bet_log",
    }
    # All failed including core → not writable
    assert not query_graph_done_enough(nodes, {}, failed)
    # Core landed, bet failed → writable
    assert query_graph_done_enough(
        nodes,
        {"view_result_user_info": 1, "view_result_pay_order_log": 1},
        {"view_result_gameuser_betstat_everyday_bygame", "view_result_user_bet_log"},
    )


def test_record_failure_stays_on_the_failed_resource():
    failed: set[str] = set()
    abandoned: set[str] = set()
    _record_export_view_failure(
        failed,
        "view_result_gameuser_betstat_everyday_bygame",
        role="bet",
        abandoned_roles=abandoned,
        abandon_role=True,
    )
    assert "view_result_gameuser_betstat_everyday_bygame" in failed
    assert "view_result_user_bet_log" not in failed
    assert "bet" in abandoned
    assert _export_role_pull_blocked(
        "bet", failed_views=failed, abandoned_roles=abandoned,
    )
    # Second pull blocked (no-op)
    assert _export_role_pull_blocked(
        "bet",
        failed_views=failed,
        abandoned_roles=abandoned,
        dim_views=None,
    )


def test_missing_roles_skips_abandoned():
    miss = _missing_export_roles(
        None,
        "run1",
        ["user", "pay", "bet", "cash"],
        abandoned_roles={"bet"},
    )
    assert "bet" not in miss
    assert "user" in miss and "pay" in miss


def test_summarize_run_page_state_from_landed_meta(tmp_path, monkeypatch):
    sid = "sbx_page_state"
    root = tmp_path / sid / "workplace"
    root.mkdir(parents=True)

    def _wp(s):
        return root if s == sid else Path("/nope")

    monkeypatch.setattr("app.services.workplace.ensure_workplace", _wp)
    monkeypatch.setattr("app.services.react_engine.ensure_workplace", _wp)

    run_id = "run_pages"
    write_task_json_page(
        sid,
        [{"uid": 1, "pay_sum": 10}],
        run_id=run_id,
        page=1,
        view="view_result_pay_order_log",
    )
    write_task_json_page(
        sid,
        [{"uid": 2, "pay_sum": 20}, {"uid": 3, "pay_sum": 30}],
        run_id=run_id,
        page=2,
        view="view_result_pay_order_log",
    )
    write_task_json_page(
        sid,
        [{"uid": 1, "cash_sum": 5}],
        run_id=run_id,
        page=4,
        view="view_result_cash_order_log",
    )

    pages, last_rows, max_page = _summarize_run_page_state(sid, run_id)
    assert pages == {
        "view_result_pay_order_log": 2,
        "view_result_cash_order_log": 1,
    }
    assert last_rows["view_result_pay_order_log"] == 2
    assert last_rows["view_result_cash_order_log"] == 1
    assert max_page == 4


def test_prior_node_state_only_imports_for_same_or_hydrated_run():
    assert _should_import_prior_node_state(
        prior_run_id="run1",
        current_run_id="run1",
        current_fetched_pages={},
    )
    assert not _should_import_prior_node_state(
        prior_run_id="run1",
        current_run_id="run2",
        current_fetched_pages={},
    )
    assert not _should_import_prior_node_state(
        prior_run_id="run1",
        current_run_id="run2",
        current_fetched_pages={"view_result_pay_order_log": 1},
    )


def test_strip_repair_plan_node_state_for_cross_run_resume():
    stripped = _strip_repair_plan_node_state({
        "status": "repairable",
        "actions": [{"action_type": "describe_ads_views"}],
        "preserve_done_node_keys": ["agg:pay"],
        "skip_failed_node_keys": ["top_n:bet"],
    })
    assert stripped["actions"][0]["action_type"] == "describe_ads_views"
    assert stripped["preserve_done_node_keys"] == []
    assert stripped["skip_failed_node_keys"] == []


def test_write_still_works_when_bet_abandoned(tmp_path, monkeypatch):
    """user+pay fixture → deliverable; abandoned bet column marked 未完整 in 口径."""
    from app.services.export_build_report import write_export_from_uid_maps

    sid = "sbx_failfast"
    root = tmp_path / sid / "workplace"
    root.mkdir(parents=True)

    def _wp(s):
        return root if s == sid else Path("/nope")

    monkeypatch.setattr("app.services.workplace.ensure_workplace", _wp)
    monkeypatch.setattr("app.services.export_build_report.ensure_workplace", _wp)

    run_id = "run_ff"
    write_task_json_page(
        sid,
        [{"uid": 1, "register_time": 1500, "channel_id": 9, "sc": 10000}],
        run_id=run_id,
        page=1,
        view="view_result_user_info",
    )
    write_task_json_page(
        sid,
        [{"uid": 1, "pay_sum": 12.5}],
        run_id=run_id,
        page=2,
        view="view_result_pay_order_log",
    )
    write_task_json_page(
        sid,
        [{"channel_id": 9, "channel_name": "Organic"}],
        run_id=run_id,
        page=3,
        view="view_result_config_channel",
    )
    plan_core = build_column_plan(["用户ID", "总充值金额", "注册渠道"])
    rel = write_export_deliverable(
        sid,
        run_id,
        column_plan=plan_core,
        time_window={"start_ms": 1000, "end_ms": 2000, "label": "failfast", "cohort": "pay"},
        title="失败快停仍交付",
    )
    assert rel
    assert (root / rel).is_file()

    plan_full = build_column_plan(["用户ID", "总充值金额", "总下注金额(SC)"])
    for col in plan_full:
        if str((col.get("agg_spec") or {}).get("role") or "") == "bet":
            col["统计方法"] = str(col.get("统计方法") or "下注") + "（未完整）"
    dest = tmp_path / "failfast_koujing.xlsx"
    assert write_export_from_uid_maps(
        dest,
        headers=["用户ID", "总充值金额", "总下注金额(SC)"],
        rows=[{"用户ID": 1, "总充值金额": 10.0, "总下注金额(SC)": ""}],
        column_plan=plan_full,
        title="失败快停仍交付",
    )
    import openpyxl

    wb = openpyxl.load_workbook(dest)
    blob = " ".join(
        str(v or "")
        for row in wb["口径说明"].iter_rows(values_only=True)
        for v in row
    )
    assert "未完整" in blob
    wb.close()
